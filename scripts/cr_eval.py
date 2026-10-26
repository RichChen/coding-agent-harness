"""CR-1: run retrievers and score them.

    cr_eval.py labels            label statistics for all instances (no retrieval)
    cr_eval.py run --split dev   retrieval on the split, then scoring

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
from dataclasses import asdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.retrieval import dataset, repo_cache  # noqa: E402
from src.retrieval.bm25 import BM25Index, query_term_ids  # noqa: E402
from src.retrieval.chunking import ChunkStore, Snapshot, load_snapshot  # noqa: E402
from src.retrieval.dataset import QueryView  # noqa: E402
from src.retrieval.fusion import (  # noqa: E402
    max_by_file,
    mention_first,
    mentioned_files,
    order_by_score,
    random_order,
)

TOP_K = 100
SPLIT_PATH = ROOT / "docs" / "retrieval" / "cr1-split.json"
RUNS = ROOT / "runs" / "cr"
SUMMARY_PATH = ROOT / "docs" / "retrieval" / "cr1a-dev-summary.json"
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


def run_retrieval(queries: list[QueryView], out_path: Path) -> None:
    t0 = time.time()
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
                    rec = retrieve_instance(q, snap)
                    rec["seconds"] = round(time.time() - t, 2)
                    fh.write(json.dumps(rec) + "\n")
            finally:
                store.close()
            print(f"retrieved {repo}: {len(qs)} instances ({time.time() - t0:.0f}s total)", flush=True)


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
    from src.retrieval.metrics import ALL_METRICS, FILE_METRICS, paired_bootstrap, score_instance, summarize

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
    per = {}
    for name in RETRIEVERS:
        rs = [r for r in rows if r["retriever"] == name and not r["unretrievable"]]
        metrics = ALL_METRICS if rs and rs[0].get("fn_hit@5") is not None else FILE_METRICS
        per[name] = summarize(rs, metrics)
    summary["retrievers"] = per
    pm = [r for r in rows if r["retriever"] == "path_mention" and not r["unretrievable"]]
    summary["path_mention"] = {
        "with_any_mention": sum(r["n_mentioned"] > 0 for r in pm),
        "mention_includes_gold": sum(r["mention_has_gold"] for r in pm),
        "retrievable": len(pm),
    }
    by = {(r["instance_id"], r["retriever"]): r for r in rows if not r["unretrievable"]}
    ids = sorted({r["instance_id"] for r in rows if not r["unretrievable"]})
    summary["dev_paired_bm25_chunk_minus_file"] = {
        m: paired_bootstrap([by[(i, "bm25_chunk")][m] for i in ids], [by[(i, "bm25_file")][m] for i in ids])
        for m in ("hit@5", "mrr")
    }
    return rows, summary


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


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("labels")
    run = sub.add_parser("run")
    run.add_argument("--split", default="dev", choices=["dev", "test"])
    run.add_argument("--allow-test", action="store_true")
    run.add_argument("--score-only", action="store_true")
    args = ap.parse_args()
    return cmd_labels(args) if args.cmd == "labels" else cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
