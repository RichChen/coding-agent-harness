"""CR-1: run retrievers and score them.

    cr_eval.py labels            label statistics for all instances (no retrieval)
    cr_eval.py run --split dev   CR-1a retrievers on the split, then scoring
    cr_eval.py embed-select      CR-1b, dev: each embedding model retrieving alone
    cr_eval.py grid              CR-1b, dev: hybrid grid with the selected model
    cr_eval.py write-freeze      CR-1b: docs/retrieval/cr1-freeze.json
    cr_eval.py frozen --split dev   random, path_mention and the four frozen configs

Retrieval sees only QueryView plus the base-commit corpus snapshot. Rankings
are written to disk before any LabelView is loaded.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.retrieval import dataset, repo_cache  # noqa: E402
from src.retrieval.bm25 import BM25Index, query_term_ids  # noqa: E402
from src.retrieval.chunking import ChunkStore, Snapshot, load_snapshot  # noqa: E402
from src.retrieval.dataset import QueryView  # noqa: E402
from src.retrieval.embed import MODELS, VectorStore, doc_text, query_text, text_sha  # noqa: E402
from src.retrieval.fusion import (  # noqa: E402
    max_by_file,
    mention_first,
    mentioned_files,
    order_by_score,
    random_order,
    rrf,
)
from src.retrieval.fusion import ranks_from_order as fusion_ranks  # noqa: E402

TOP_K = 100
SPLIT_PATH = ROOT / "docs" / "retrieval" / "cr1-split.json"
RUNS = ROOT / "runs" / "cr"
SUMMARY_PATH = ROOT / "docs" / "retrieval" / "cr1a-dev-summary.json"
CR1B_SUMMARY_PATH = ROOT / "docs" / "retrieval" / "cr1b-dev-summary.json"
FREEZE_PATH = ROOT / "docs" / "retrieval" / "cr1-freeze.json"
EXAMPLE_SEED = 20261018


# ---------------------------------------------------------------- retrieval


class SnapshotIndex:
    """BM25 indexes over one snapshot: chunk documents and whole-file documents."""

    def __init__(self, snap: Snapshot):
        self.snap = snap
        self.chunk_ids = snap.chunk_ids()
        self.chunk_keys = [(snap.paths[f], s.start_line) for f, s in zip(snap.chunk_file.tolist(), snap.chunk_spans)]
        self.chunk_bm25 = BM25Index(len(self.chunk_ids), *snap.chunk_doc_triples())
        self.file_bm25 = BM25Index(len(snap.paths), *snap.file_doc_triples())


def _bm25_chunk_orders(query: QueryView, ix: SnapshotIndex) -> tuple[list[int], list[int]]:
    snap = ix.snap
    scores = ix.chunk_bm25.score(query_term_ids(query.problem_statement, snap.vocab))
    chunk_order = order_by_score(scores, ix.chunk_keys)
    file_order = order_by_score(max_by_file(scores, snap.chunk_file, len(snap.paths)), snap.paths)
    return file_order, chunk_order


def retrieve_random(query: QueryView, ix: SnapshotIndex) -> dict:
    snap = ix.snap
    files = random_order(len(snap.paths), query.instance_id, "files")[:TOP_K]
    chunks = random_order(len(ix.chunk_ids), query.instance_id, "chunks")[:TOP_K]
    return {"files": [snap.paths[i] for i in files], "chunks": [ix.chunk_ids[i] for i in chunks]}


def retrieve_bm25_chunk(query: QueryView, ix: SnapshotIndex) -> dict:
    file_order, chunk_order = _bm25_chunk_orders(query, ix)
    return {
        "files": [ix.snap.paths[i] for i in file_order[:TOP_K]],
        "chunks": [ix.chunk_ids[i] for i in chunk_order[:TOP_K]],
    }


def retrieve_bm25_file(query: QueryView, ix: SnapshotIndex) -> dict:
    snap = ix.snap
    scores = ix.file_bm25.score(query_term_ids(query.problem_statement, snap.vocab))
    order = order_by_score(scores, snap.paths)
    return {"files": [snap.paths[i] for i in order[:TOP_K]], "chunks": None}


def retrieve_path_mention(query: QueryView, ix: SnapshotIndex) -> dict:
    snap = ix.snap
    mentioned = mentioned_files(query.problem_statement, snap.paths)
    file_order, chunk_order = _bm25_chunk_orders(query, ix)
    files = mention_first(file_order, mentioned)
    chunk_mentioned = {i for i, f in enumerate(snap.chunk_file.tolist()) if f in mentioned}
    chunks = mention_first(chunk_order, chunk_mentioned)
    return {
        "files": [snap.paths[i] for i in files[:TOP_K]],
        "chunks": [ix.chunk_ids[i] for i in chunks[:TOP_K]],
        "mentioned": sorted(snap.paths[i] for i in mentioned),
    }


RETRIEVERS = {
    "random": retrieve_random,
    "path_mention": retrieve_path_mention,
    "bm25_chunk": retrieve_bm25_chunk,
    "bm25_file": retrieve_bm25_file,
}


def retrieve_instance(query: QueryView, snap: Snapshot) -> dict:
    ix = SnapshotIndex(snap)
    out = {"instance_id": query.instance_id, "repo": query.repo, "n_files": len(snap.paths), "n_chunks": len(ix.chunk_ids)}
    out["rankings"] = {name: fn(query, ix) for name, fn in RETRIEVERS.items()}
    return out


# ---------------------------------------------------------------- CR-1b: embedding and hybrid


def _key_rank(keys: list) -> np.ndarray:
    """Rank of each key in a stable sort of ``keys``."""
    order = sorted(range(len(keys)), key=lambda i: keys[i])
    return fusion_ranks(order, len(keys))


def fast_order(scores, key_rank: np.ndarray) -> list[int]:
    """Same result as ``order_by_score(scores, keys)`` given ``key_rank = _key_rank(keys)``."""
    return np.lexsort((key_rank, -np.asarray(scores, np.float64))).tolist()


class EmbeddingView:
    """Chunk and query vectors of one model for one (query, snapshot)."""

    def __init__(self, spec, vectors, query: QueryView, ix: SnapshotIndex):
        snap = ix.snap
        texts = [doc_text(spec, snap.chunk(i).text) for i in range(len(ix.chunk_ids))]
        self.spec = spec
        mat = vectors.get_matrix([text_sha(t) for t in texts])
        qv = vectors.get_matrix([text_sha(query_text(spec, query.problem_statement))])[0]
        self.chunk_scores = mat @ qv


@dataclass(frozen=True)
class HybridConfig:
    k: int
    w_emb: float
    fusion: str  # chunk | file
    bm25: str  # chunk (chunk-aggregated) | file (whole-file documents)

    @property
    def name(self) -> str:
        return f"hybrid_k{self.k}_w{self.w_emb:g}_{self.fusion}fuse_bm25{self.bm25}"


GRID = (
    [HybridConfig(k, w, "chunk", "chunk") for k in (10, 30, 60) for w in (0.5, 1.0, 2.0)]
    + [HybridConfig(k, w, "file", "chunk") for k in (10, 30, 60) for w in (0.5, 1.0, 2.0)]
    + [HybridConfig(k, w, "file", "file") for k in (30, 60) for w in (0.5, 1.0, 2.0)]
)


class RankMaterial:
    """Full orderings shared by the embedding and hybrid retrievers."""

    def __init__(self, query: QueryView, ix: SnapshotIndex, ev: EmbeddingView):
        snap = ix.snap
        n_files, n_chunks = len(snap.paths), len(ix.chunk_ids)
        self.chunk_rank_key = _key_rank(ix.chunk_keys)
        self.file_rank_key = np.arange(n_files)  # snap.paths is sorted
        q_ids = query_term_ids(query.problem_statement, snap.vocab)
        b_chunk = ix.chunk_bm25.score(q_ids)
        self.bm25_chunk_order = fast_order(b_chunk, self.chunk_rank_key)
        self.bm25_chunkagg_file_order = fast_order(max_by_file(b_chunk, snap.chunk_file, n_files), self.file_rank_key)
        self.bm25_file_order = fast_order(ix.file_bm25.score(q_ids), self.file_rank_key)
        self.emb_chunk_order = fast_order(ev.chunk_scores, self.chunk_rank_key)
        self.emb_file_order = fast_order(max_by_file(ev.chunk_scores, snap.chunk_file, n_files), self.file_rank_key)
        self.ranks = {
            "bm25_chunk": fusion_ranks(self.bm25_chunk_order, n_chunks),
            "emb_chunk": fusion_ranks(self.emb_chunk_order, n_chunks),
            "bm25_chunkagg_file": fusion_ranks(self.bm25_chunkagg_file_order, n_files),
            "bm25_file": fusion_ranks(self.bm25_file_order, n_files),
            "emb_file": fusion_ranks(self.emb_file_order, n_files),
        }
        self.mentioned = mentioned_files(query.problem_statement, snap.paths)


def _material(query: QueryView, ix: SnapshotIndex, ev: EmbeddingView) -> RankMaterial:
    memo = ix.__dict__.setdefault("_materials", {})
    key = (query.instance_id, ev.spec.key)
    if key not in memo:
        memo[key] = RankMaterial(query, ix, ev)
    return memo[key]


def _out(ix: SnapshotIndex, file_order, chunk_order) -> dict:
    return {
        "files": [ix.snap.paths[i] for i in file_order[:TOP_K]],
        "chunks": [ix.chunk_ids[i] for i in chunk_order[:TOP_K]],
    }


def retrieve_embedding(query: QueryView, ix: SnapshotIndex, ev: EmbeddingView) -> dict:
    m = _material(query, ix, ev)
    return _out(ix, m.emb_file_order, m.emb_chunk_order)


def _hybrid_orders(query: QueryView, ix: SnapshotIndex, ev: EmbeddingView, cfg: HybridConfig):
    m = _material(query, ix, ev)
    snap = ix.snap
    chunk_fused = rrf([m.ranks["bm25_chunk"], m.ranks["emb_chunk"]], [1.0, cfg.w_emb], cfg.k)
    chunk_order = fast_order(chunk_fused, m.chunk_rank_key)
    if cfg.fusion == "chunk":
        file_order = fast_order(max_by_file(chunk_fused, snap.chunk_file, len(snap.paths)), m.file_rank_key)
    else:
        b = m.ranks["bm25_chunkagg_file"] if cfg.bm25 == "chunk" else m.ranks["bm25_file"]
        file_order = fast_order(rrf([b, m.ranks["emb_file"]], [1.0, cfg.w_emb], cfg.k), m.file_rank_key)
    return file_order, chunk_order, m


def retrieve_hybrid(query: QueryView, ix: SnapshotIndex, ev: EmbeddingView, cfg: HybridConfig) -> dict:
    file_order, chunk_order, _ = _hybrid_orders(query, ix, ev, cfg)
    return _out(ix, file_order, chunk_order)


def retrieve_hybrid_mention(query: QueryView, ix: SnapshotIndex, ev: EmbeddingView, cfg: HybridConfig) -> dict:
    file_order, chunk_order, m = _hybrid_orders(query, ix, ev, cfg)
    chunk_mentioned = {i for i, f in enumerate(ix.snap.chunk_file.tolist()) if f in m.mentioned}
    out = _out(ix, mention_first(file_order, m.mentioned), mention_first(chunk_order, chunk_mentioned))
    out["mentioned"] = sorted(ix.snap.paths[i] for i in m.mentioned)
    return out


CR1B_RETRIEVERS = {
    "embedding": retrieve_embedding,
    "hybrid": retrieve_hybrid,
    "hybrid_mention": retrieve_hybrid_mention,
}


def run_generic(queries: list[QueryView], out_path: Path, per_instance, setup=None) -> None:
    """``per_instance(query, ix, ctx) -> rankings``; ``setup()`` returns a context
    (e.g. open vector stores) shared across instances."""
    t0 = time.time()
    ctx = setup() if setup else None
    by_repo: dict[str, list[QueryView]] = {}
    for q in queries:
        by_repo.setdefault(q.repo, []).append(q)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        for repo, qs in sorted(by_repo.items()):
            store = ChunkStore(repo)
            git_dir = repo_cache.repo_dir(repo)
            try:
                for q in sorted(qs, key=lambda q: q.instance_id):
                    t = time.time()
                    snap = load_snapshot(store, git_dir, q.base_commit)
                    ix = SnapshotIndex(snap)
                    rec = {"instance_id": q.instance_id, "repo": q.repo, "n_files": len(snap.paths),
                           "n_chunks": len(ix.chunk_ids), "rankings": per_instance(q, ix, ctx)}
                    rec["seconds"] = round(time.time() - t, 2)
                    fh.write(json.dumps(rec) + "\n")
            finally:
                store.close()
            print(f"retrieved {repo}: {len(qs)} instances ({time.time() - t0:.0f}s total)", flush=True)


def run_retrieval(queries: list[QueryView], out_path: Path) -> None:
    run_generic(queries, out_path, lambda q, ix, ctx: {name: fn(q, ix) for name, fn in RETRIEVERS.items()})


def parse_model_arg(arg: str) -> tuple[str, int]:
    """``key`` or ``key@max_len``."""
    key, _, length = arg.partition("@")
    if key not in MODELS:
        raise SystemExit(f"unknown model {key!r}; choose from {sorted(MODELS)}")
    return key, int(length) if length else 512


def _stores_setup(models: list[tuple[str, int]]):
    def setup() -> dict:
        stores = {}
        for key, length in models:
            t = time.time()
            stores[key] = VectorStore(MODELS[key], length)
            n = stores[key].preload()
            print(f"preloaded {n} vectors for {key} in {time.time() - t:.0f}s", flush=True)
        return stores

    return setup


def per_instance_embed_select(q: QueryView, ix: SnapshotIndex, stores: dict) -> dict:
    out = {"bm25_chunk": retrieve_bm25_chunk(q, ix)}
    for key, store in stores.items():
        out[f"embedding:{key}"] = retrieve_embedding(q, ix, EmbeddingView(MODELS[key], store, q, ix))
    return out


def per_instance_grid(q: QueryView, ix: SnapshotIndex, stores: dict) -> dict:
    (key, store), = stores.items()
    ev = EmbeddingView(MODELS[key], store, q, ix)
    out = {"bm25_chunk": retrieve_bm25_chunk(q, ix), "embedding": retrieve_embedding(q, ix, ev)}
    for cfg in GRID:
        out[cfg.name] = retrieve_hybrid(q, ix, ev, cfg)
    return out


def select_hybrid(mrr: dict[HybridConfig, float], margin: float = 0.01) -> HybridConfig:
    """Contract 11.3: among configs within ``margin`` of the best dev file MRR,
    prefer k=60, then w_emb=1, then chunk fusion, then chunk-aggregated BM25;
    remaining ties go to higher MRR, then name."""
    best = max(mrr.values())
    pool = [c for c, v in mrr.items() if v >= best - margin - 1e-12]
    return min(pool, key=lambda c: (c.k != 60, c.w_emb != 1.0, c.fusion != "chunk", c.bm25 != "chunk", -mrr[c], c.name))


def load_freeze(path: Path | None = None) -> dict:
    return json.loads((path or FREEZE_PATH).read_text(encoding="utf-8"))


def frozen_per_instance(freeze: dict):
    key = freeze["embedding"]["model"]
    spec = MODELS[key]
    cfg = HybridConfig(**freeze["hybrid"]["config"])

    def fn(q: QueryView, ix: SnapshotIndex, stores: dict) -> dict:
        ev = EmbeddingView(spec, stores[key], q, ix)
        return {
            "random": retrieve_random(q, ix),
            "path_mention": retrieve_path_mention(q, ix),
            "bm25": retrieve_bm25_chunk(q, ix),
            "embedding": retrieve_embedding(q, ix, ev),
            "hybrid": retrieve_hybrid(q, ix, ev, cfg),
            "hybrid_mention": retrieve_hybrid_mention(q, ix, ev, cfg),
        }

    return fn, [(key, freeze["embedding"]["max_len"])]


# ---------------------------------------------------------------- scoring


def _load_split() -> dict:
    return json.loads(SPLIT_PATH.read_text(encoding="utf-8"))


def _queries_for(ids: set[str]) -> list[QueryView]:
    return [q for q in dataset.load_query_views() if q.instance_id in ids]


def _dist(xs) -> dict:
    a = np.asarray(xs, np.float64)
    if not a.size:
        return {}
    return {"n": int(a.size), "min": float(a.min()), "median": float(np.median(a)), "mean": float(a.mean()),
            "p90": float(np.percentile(a, 90)), "max": float(a.max())}


def label_stats(lvs) -> dict:
    lvs = list(lvs)
    unret = [lv for lv in lvs if lv.unretrievable]
    reasons = Counter("+".join(sorted(lv.excluded_counts())) or "none" for lv in unret)
    excl_total = Counter()
    for lv in lvs:
        excl_total.update(lv.excluded_counts())
    ret = [lv for lv in lvs if not lv.unretrievable]
    n_chunks = sum(len(lv.gold_chunks) for lv in ret)
    return {
        "instances": len(lvs),
        "unretrievable": len(unret),
        "unretrievable_ids": sorted(lv.instance_id for lv in unret),
        "unretrievable_reasons": dict(reasons),
        "excluded_file_entries": dict(excl_total),
        "instances_with_any_excluded_file": sum(bool(lv.excluded) for lv in lvs),
        "gold_files_per_instance": dict(sorted(Counter(len(lv.gold_files) for lv in ret).items())),
        "gold_chunks_per_instance": _dist([len(lv.gold_chunks) for lv in ret]),
        "gold_chunks_total": n_chunks,
        "gold_chunks_module": sum(lv.module_chunks for lv in ret),
        "gold_chunks_module_fraction": sum(lv.module_chunks for lv in ret) / n_chunks if n_chunks else None,
        "instances_with_module_gold_chunk": sum(lv.module_chunks > 0 for lv in ret),
        "instances_where_per_hunk_reading_differs": sum(lv.gold_chunks != lv.gold_chunks_per_hunk for lv in ret),
    }


def cmd_labels(args) -> int:
    from src.retrieval.labels import load_label_views

    split = _load_split()
    queries = dataset.load_query_views()
    lvs = load_label_views(queries)
    RUNS.mkdir(parents=True, exist_ok=True)
    with open(RUNS / "labels.jsonl", "w", encoding="utf-8") as fh:
        for iid in sorted(lvs):
            fh.write(json.dumps(asdict(lvs[iid])) + "\n")
    dev = set(split["dev_ids"])
    stats = {
        "all": label_stats(lvs.values()),
        "dev": label_stats(lv for k, lv in lvs.items() if k in dev),
        "by_repo_unretrievable": dict(sorted(Counter(lv.repo for lv in lvs.values() if lv.unretrievable).items())),
    }
    (RUNS / "label-stats.json").write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "unretrievable_ids"} if isinstance(v, dict) and "instances" in v else v for k, v in stats.items()}, indent=2))
    return 0


def score_rankings(rankings: list[dict], lvs: dict) -> tuple[list[dict], dict]:
    from src.retrieval.metrics import ALL_METRICS, FILE_METRICS, score_instance, summarize

    rows = []
    for rec in rankings:
        lv = lvs[rec["instance_id"]]
        for name, r in rec["rankings"].items():
            row = {"instance_id": rec["instance_id"], "repo": rec["repo"], "retriever": name,
                   "unretrievable": lv.unretrievable}
            if not lv.unretrievable:
                row.update(score_instance(lv, r["files"], r["chunks"] or []))
                if r["chunks"] is None:
                    row["fn_hit@5"] = row["fn_hit@10"] = None
                if "mentioned" in r:
                    row["n_mentioned"] = len(r["mentioned"])
                    row["mention_has_gold"] = bool(set(r["mentioned"]) & set(lv.gold_files))
            rows.append(row)
    summary = {"n_instances": len(rankings), "n_unretrievable": sum(lv.unretrievable for lv in (lvs[r["instance_id"]] for r in rankings))}
    names = list(rankings[0]["rankings"]) if rankings else []
    per = {}
    for name in names:
        rs = [r for r in rows if r["retriever"] == name and not r["unretrievable"]]
        metrics = ALL_METRICS if rs and rs[0].get("fn_hit@5") is not None else FILE_METRICS
        per[name] = summarize(rs, metrics)
    summary["retrievers"] = per
    if "path_mention" in names:
        pm = [r for r in rows if r["retriever"] == "path_mention" and not r["unretrievable"]]
        summary["path_mention"] = {
            "with_any_mention": sum(r["n_mentioned"] > 0 for r in pm),
            "mention_includes_gold": sum(r["mention_has_gold"] for r in pm),
            "retrievable": len(pm),
        }
    if "bm25_chunk" in names and "bm25_file" in names:
        summary["dev_paired_bm25_chunk_minus_file"] = paired(rows, "bm25_chunk", "bm25_file", ("hit@5", "mrr"))
    return rows, summary


def paired(rows: list[dict], a: str, b: str, metrics=("hit@5", "mrr")) -> dict:
    from src.retrieval.metrics import paired_bootstrap

    by = {(r["instance_id"], r["retriever"]): r for r in rows if not r["unretrievable"]}
    ids = sorted({r["instance_id"] for r in rows if not r["unretrievable"]})
    return {m: paired_bootstrap([by[(i, a)][m] for i in ids], [by[(i, b)][m] for i in ids]) for m in metrics}


def cmd_run(args) -> int:
    split = _load_split()
    if args.split != "dev" and not args.allow_test:
        print("refusing to run on test before CR-1c (pass --allow-test only in CR-1c)")
        return 2
    ids = set(split[f"{args.split}_ids"])
    queries = _queries_for(ids)
    rank_path = RUNS / f"{args.split}-rankings.jsonl"
    if not args.score_only:
        run_retrieval(queries, rank_path)
    rankings = [json.loads(line) for line in rank_path.read_text(encoding="utf-8").splitlines() if line]

    from src.retrieval.labels import load_label_views

    lvs = load_label_views(queries)
    rows, summary = score_rankings(rankings, lvs)
    with open(RUNS / f"{args.split}-per-instance.jsonl", "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    summary["seconds_per_instance"] = _dist([r["seconds"] for r in rankings])
    summary["corpus"] = {"files": _dist([r["n_files"] for r in rankings]), "chunks": _dist([r["n_chunks"] for r in rankings])}

    rng = random.Random(EXAMPLE_SEED)
    pool = sorted(r["instance_id"] for r in rankings if not lvs[r["instance_id"]].unretrievable)
    picks = sorted(rng.sample(pool, 3))
    qmap = {q.instance_id: q for q in queries}
    rmap = {r["instance_id"]: r for r in rankings}
    summary["examples"] = [
        {
            "instance_id": iid,
            "issue_head": qmap[iid].problem_statement[:300],
            "gold_files": list(lvs[iid].gold_files),
            "bm25_chunk_top5": rmap[iid]["rankings"]["bm25_chunk"]["files"][:5],
            "bm25_chunk_top5_chunks": rmap[iid]["rankings"]["bm25_chunk"]["chunks"][:5],
        }
        for iid in picks
    ]
    if args.split == "dev":
        SUMMARY_PATH.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (RUNS / f"{args.split}-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    for name, s in summary["retrievers"].items():
        a = s["all"]
        print(f"{name:13s} n={a['n']} " + " ".join(f"{m}={a[m]:.3f}" for m in a if m != "n"))
    print(json.dumps(summary["path_mention"]))
    return 0


# ---------------------------------------------------------------- CR-1b commands


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _score_and_save(queries: list[QueryView], rank_path: Path, tag: str):
    from src.retrieval.labels import load_label_views

    rankings = _read_jsonl(rank_path)
    lvs = load_label_views(queries)
    rows, summary = score_rankings(rankings, lvs)
    with open(RUNS / f"{tag}-per-instance.jsonl", "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    summary["seconds_per_instance"] = _dist([r["seconds"] for r in rankings])
    (RUNS / f"{tag}-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return rankings, rows, summary


def _update_cr1b_summary(section: str, value) -> None:
    data = json.loads(CR1B_SUMMARY_PATH.read_text(encoding="utf-8")) if CR1B_SUMMARY_PATH.exists() else {}
    data[section] = value
    CR1B_SUMMARY_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _compact(s: dict) -> dict:
    return {"all": s["all"], "macro": s["macro"]}


def _print_table(per: dict) -> None:
    for name, s in per.items():
        a = s["all"]
        print(f"{name:44s} n={a['n']} " + " ".join(f"{m}={a[m]:.3f}" for m in a if m != "n" and a[m] is not None), flush=True)


def _git_head() -> str:
    import subprocess

    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def _code_dirty(commit: str) -> bool:
    import subprocess

    paths = ["src/retrieval", "scripts/cr_eval.py", "scripts/cr_index.py"]
    r = subprocess.run(["git", "diff", "--quiet", commit, "--", *paths], cwd=ROOT)
    return r.returncode != 0


def cmd_embed_select(args) -> int:
    models = [parse_model_arg(m) for m in args.models.split(",")]
    queries = _queries_for(set(_load_split()["dev_ids"]))
    rank_path = RUNS / "dev-embed-select-rankings.jsonl"
    if not args.score_only:
        run_generic(queries, rank_path, per_instance_embed_select, _stores_setup(models))
    _, rows, summary = _score_and_save(queries, rank_path, "dev-embed-select")
    per = summary["retrievers"]
    _print_table(per)
    emb = {key: per[f"embedding:{key}"]["all"]["mrr"] for key, _ in models}
    order = list(MODELS)
    selected = max(emb, key=lambda k: (emb[k], -order.index(k)))
    _update_cr1b_summary("embed_select", {
        "code_commit": _git_head(),
        "models": {key: {"max_len": length, **_compact(per[f"embedding:{key}"])} for key, length in models},
        "bm25_chunk": _compact(per["bm25_chunk"]),
        "paired_vs_bm25_chunk": {key: paired(rows, f"embedding:{key}", "bm25_chunk") for key, _ in models},
        "selection_rule": "highest dev file-level MRR of the model retrieving alone (contract 11.2)",
        "selected": selected,
        "selected_max_len": dict(models)[selected],
        "seconds_per_instance": summary["seconds_per_instance"],
    })
    print(f"selected embedding model: {selected}")
    return 0


def cmd_grid(args) -> int:
    if args.model:
        model = parse_model_arg(args.model)
    else:
        sel = json.loads(CR1B_SUMMARY_PATH.read_text(encoding="utf-8"))["embed_select"]
        model = (sel["selected"], sel["selected_max_len"])
    queries = _queries_for(set(_load_split()["dev_ids"]))
    rank_path = RUNS / "dev-grid-rankings.jsonl"
    if not args.score_only:
        run_generic(queries, rank_path, per_instance_grid, _stores_setup([model]))
    _, rows, summary = _score_and_save(queries, rank_path, "dev-grid")
    per = summary["retrievers"]
    _print_table(per)
    mrr = {cfg: per[cfg.name]["all"]["mrr"] for cfg in GRID}
    chosen = select_hybrid(mrr)
    best = max(mrr.values())
    table = []
    for cfg in sorted(GRID, key=lambda c: -mrr[c]):
        a = per[cfg.name]["all"]
        table.append({"name": cfg.name, **asdict(cfg), **{m: a[m] for m in a if m != "n"},
                      "macro_mrr": per[cfg.name]["macro"]["mrr"],
                      "within_0.01_of_best": mrr[cfg] >= best - 0.01 - 1e-12})
    _update_cr1b_summary("grid", {
        "code_commit": _git_head(),
        "model": model[0],
        "max_len": model[1],
        "n_configs": len(GRID),
        "table": table,
        "embedding": _compact(per["embedding"]),
        "bm25_chunk": _compact(per["bm25_chunk"]),
        "best_mrr": best,
        "selected": {"name": chosen.name, **asdict(chosen), "mrr": mrr[chosen]},
        "selected_vs_bm25_chunk": paired(rows, chosen.name, "bm25_chunk"),
        "seconds_per_instance": summary["seconds_per_instance"],
    })
    print(f"best MRR {best:.4f}; selected {chosen.name} (MRR {mrr[chosen]:.4f})")
    return 0


def cmd_write_freeze(args) -> int:
    import inspect
    import platform
    from importlib.metadata import version

    from src.retrieval.chunking import CACHE_VERSION
    from src.retrieval.tokenize import TOKENIZER_VERSION

    data = json.loads(CR1B_SUMMARY_PATH.read_text(encoding="utf-8"))
    head = _git_head()
    if _code_dirty(head):
        print("retrieval code differs from HEAD; commit it before freezing")
        return 2
    grid = data["grid"]
    if grid["code_commit"] != head or data["embed_select"]["code_commit"] != head:
        print(f"embed-select/grid ran on {data['embed_select']['code_commit']}/{grid['code_commit']}, HEAD is {head}")
        if _code_dirty(grid["code_commit"]) or _code_dirty(data["embed_select"]["code_commit"]):
            print("retrieval code changed since those runs; rerun them")
            return 2
    spec = MODELS[grid["model"]]
    bm25_params = inspect.signature(BM25Index).parameters
    sel = grid["selected"]
    freeze = {
        "code_commit": head,
        "parquet_sha256": dataset.parquet_sha256(),
        "split_file": "docs/retrieval/cr1-split.json",
        "chunk_cache_version": CACHE_VERSION,
        "tokenizer_version": TOKENIZER_VERSION,
        "top_k": TOP_K,
        "file_aggregation": "max over the file's chunks",
        "ties": "path (files) / (path, start_line) (chunks)",
        "bm25": {"retriever": "bm25_chunk", "k1": bm25_params["k1"].default, "b": bm25_params["b"].default},
        "embedding": {
            "model": spec.key, "name": spec.name, "revision": spec.revision, "code_revision": spec.code_revision,
            "trust_remote_code": spec.trust_remote_code, "license": spec.license,
            "query_prefix": spec.query_prefix, "doc_prefix": spec.doc_prefix,
            "max_len": grid["max_len"], "dtype": "float16", "similarity": "dot product of unit-norm vectors",
        },
        "hybrid": {
            "name": sel["name"],
            "config": {"k": sel["k"], "w_emb": sel["w_emb"], "fusion": sel["fusion"], "bm25": sel["bm25"]},
            "bm25_weight": 1.0,
            "chunk_ranking": "chunk-level RRF of bm25_chunk and embedding chunk ranks (same k, w_emb)",
        },
        "hybrid_mention": {"base": "hybrid", "rerank": "fusion.mention_first (same as path_mention)"},
        "dev_selection": {
            "embedding_mrr": {k: v["all"]["mrr"] for k, v in data["embed_select"]["models"].items()},
            "hybrid_mrr": sel["mrr"],
            "hybrid_best_mrr": grid["best_mrr"],
        },
        "dependencies": {
            "python": platform.python_version(),
            **{p: version(p) for p in ("numpy", "pyarrow", "torch", "sentence-transformers", "transformers",
                                         "tokenizers", "safetensors", "huggingface-hub", "einops")},
        },
    }
    FREEZE_PATH.write_text(json.dumps(freeze, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(freeze, indent=2))
    return 0


def cmd_frozen(args) -> int:
    if args.split != "dev" and not args.allow_test:
        print("refusing to run on test before CR-1c (pass --allow-test only in CR-1c)")
        return 2
    freeze = load_freeze()
    if _code_dirty(freeze["code_commit"]):
        print(f"WARNING: retrieval code differs from frozen commit {freeze['code_commit']}")
    per_instance, models = frozen_per_instance(freeze)
    queries = _queries_for(set(_load_split()[f"{args.split}_ids"]))
    tag = f"{args.split}-frozen"
    rank_path = RUNS / f"{tag}-rankings.jsonl"
    if not args.score_only:
        run_generic(queries, rank_path, per_instance, _stores_setup(models))
    _, rows, summary = _score_and_save(queries, rank_path, tag)
    _print_table(summary["retrievers"])
    if args.split == "dev":
        _update_cr1b_summary("frozen_dev", {
            "code_commit": _git_head(),
            "retrievers": summary["retrievers"],
            "path_mention": summary["path_mention"],
            "paired": {
                "hybrid-bm25": paired(rows, "hybrid", "bm25"),
                "embedding-bm25": paired(rows, "embedding", "bm25"),
                "hybrid-embedding": paired(rows, "hybrid", "embedding"),
                "hybrid_mention-path_mention": paired(rows, "hybrid_mention", "path_mention"),
                "hybrid_mention-hybrid": paired(rows, "hybrid_mention", "hybrid"),
            },
            "seconds_per_instance": summary["seconds_per_instance"],
        })
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("labels")
    run = sub.add_parser("run")
    run.add_argument("--split", default="dev", choices=["dev", "test"])
    run.add_argument("--allow-test", action="store_true")
    run.add_argument("--score-only", action="store_true")
    es = sub.add_parser("embed-select", help="dev only: each model retrieving alone")
    es.add_argument("--models", default=",".join(MODELS), help="comma list of key or key@max_len")
    es.add_argument("--score-only", action="store_true")
    gr = sub.add_parser("grid", help="dev only: hybrid grid with the selected model")
    gr.add_argument("--model", default=None, help="key or key@max_len (default: embed-select choice)")
    gr.add_argument("--score-only", action="store_true")
    sub.add_parser("write-freeze", help="write docs/retrieval/cr1-freeze.json from the dev selections")
    fr = sub.add_parser("frozen", help="random, path_mention and the four frozen configs")
    fr.add_argument("--split", default="dev", choices=["dev", "test"])
    fr.add_argument("--allow-test", action="store_true")
    fr.add_argument("--score-only", action="store_true")
    args = ap.parse_args()
    return {
        "labels": cmd_labels, "run": cmd_run, "embed-select": cmd_embed_select, "grid": cmd_grid,
        "write-freeze": cmd_write_freeze, "frozen": cmd_frozen,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
