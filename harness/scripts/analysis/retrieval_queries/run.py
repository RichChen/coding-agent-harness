"""Offline retrieval: does the query text matter more than the retriever, and could a step-0 retrieval have
found the gold file in the CR-2 runs that found it late? No paid model calls.

    run.py queries    build query texts (no gold): runs/retrieval-queries/queries.jsonl
    run.py retrieve   frozen CR-1 retrievers on every query: runs/retrieval-queries/rankings.jsonl,
                      plus a check against the frozen CR-1 test rankings
    run.py score      gold labels enter here only: docs/analysis/retrieval-queries-results.json and
                      docs/analysis/retrieval-queries-per-query.jsonl

Frozen inputs (read only): the CR-1 chunk index and jina vector cache, opened with sqlite mode=ro (a cache miss
raises); docs/retrieval/cr1-freeze.json, cr1-split.json, cr2-runs.json, cr2-analysis.json, cr2e-runs-round*.json;
the CR-2 and CR-2e events.jsonl files. Vectors of new query texts go to runs/retrieval-queries/vectors/.

Queries (written before any result):
- test 400 of CR-1: full (the issue text, as in CR-1), title (first non-empty line), first_para (lines up to the
  first blank line), identifiers (rq_queries.identifiers; falls back to the title when nothing is found).
- CR-2 56 tasks (all in the CR-1 test set), groups N and T (no prefetch text in the first message): model_first
  (first search_code pattern or grep/rg pattern of the run) and model_first3 (all such patterns from the first 3
  model calls, joined). Gold is not used to pick them.
- CR-2e: each retrieve_code query, scored on its own (descriptive only).
Retrievers: bm25 (chunk, max per file), embedding (jina-code, L512), hybrid (file-level RRF k=60, w_emb=2).
File-level Hit@1, Hit@5, MRR; per-task paired bootstrap, 10000 resamples, seed 20261018.

Pre-registered decision (written before any new query was scored):
- R0: with the full issue text, the top-100 file lists of bm25, embedding and hybrid must equal the frozen CR-1 test
  rankings for all 400 instances. Otherwise stop and find out why before reporting anything else.
- D1: some q in {title, first_para, identifiers} and retriever r give, on test 400,
  Hit@5(q, r) - Hit@5(full, r) >= 0.10 with the paired 95% interval above 0, and Hit@5(q, r) >= Hit@5(full, hybrid).
- D2: on the CR-2 tasks that have the model query, some m in {model_first, model_first3} and r give
  Hit@5(m, r) - Hit@5(full, r) >= 0.10 with the interval above 0, and Hit@5(m, r) >= Hit@5(full, hybrid) on the
  same tasks. Per task, a model variant is the mean over that task's N and T runs.
- D3: slow runs are CR-2 N runs with first_gold_step > 4 (75th percentile of the N runs; 2 and 10 as sensitivity).
  Some q in {full, title, first_para, identifiers} and r other than (full, hybrid) put a gold file in the top 5 for
  >= 50% of the slow runs. (full, hybrid) is the CR-2 P prefetch, which is reported but does not count.
- Any of D1, D2, D3 holds: wire it into an experiment. None holds: close this line.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import rq_queries  # noqa: E402
from rq_readonly import ReadOnlyChunkStore, ReadOnlyVectors, file_fingerprint  # noqa: E402

from src.retrieval import dataset, repo_cache  # noqa: E402
from src.retrieval.bm25 import BM25Index, query_term_ids  # noqa: E402
from src.retrieval.chunking import load_snapshot  # noqa: E402
from src.retrieval.embed import MODELS, doc_text, query_text, text_sha  # noqa: E402
from src.retrieval.fusion import max_by_file, ranks_from_order, rrf  # noqa: E402

DOCS = ROOT / "docs" / "retrieval"
OUT = ROOT / "runs" / "retrieval-queries"
RESULTS = ROOT / "docs" / "analysis" / "retrieval-queries-results.json"
PER_QUERY = ROOT / "docs" / "analysis" / "retrieval-queries-per-query.jsonl"
FROZEN_RANKINGS = ROOT / "runs" / "cr" / "test-frozen-rankings.jsonl"
CR2E_RUNS = (DOCS / "cr2e-runs-round1.json", DOCS / "cr2e-runs-round2.json")

TOP_K = 100
SEED = 20261018
N_BOOT = 10000
MODEL_WINDOW = 3
SLOW_K = 4
SLOW_K_SENSITIVITY = (2, 10)
GAIN = 0.10
SLOW_SHARE = 0.5
RETRIEVERS = ("bm25", "embedding", "hybrid")
ISSUE_VARIANTS = ("full", "title", "first_para", "identifiers")
MODEL_VARIANTS = ("model_first", f"model_first{MODEL_WINDOW}")
METRICS = ("hit@1", "hit@5", "mrr")


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


# ---------------------------------------------------------------- stage 1: queries


def cmd_queries(_args) -> None:
    split = json.loads((DOCS / "cr1-split.json").read_text(encoding="utf-8"))
    test_ids = set(split["test_ids"])
    views = {q.instance_id: q for q in dataset.load_query_views()}
    rows = []
    for iid in sorted(test_ids):
        q = views[iid]
        for variant, info in rq_queries.issue_variants(q.problem_statement).items():
            rows.append({"qid": f"{iid}|{variant}", "instance_id": iid, "repo": q.repo, "base_commit": q.base_commit,
                         "set": "test", "variant": variant, **info})

    cr2 = json.loads((DOCS / "cr2-runs.json").read_text(encoding="utf-8"))["runs"]
    bad_commands = 0
    model_runs = [r for r in cr2 if r["group"] in ("n", "t")]
    for r in model_runs:
        found, bad = rq_queries.run_queries(rq_queries.read_events(ROOT / r["run_dir"] / "events.jsonl"))
        bad_commands += bad
        q = views[r["task_id"]]
        run = f"{r['group']}/r{r['k']}"
        for variant, info in rq_queries.model_variants(found, MODEL_WINDOW).items():
            rows.append({"qid": f"{r['task_id']}|{variant}|{run}", "instance_id": r["task_id"], "repo": q.repo,
                         "base_commit": q.base_commit, "set": "cr2", "variant": variant, "run": run,
                         "group": r["group"], **info})

    for path in CR2E_RUNS:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for r in payload["runs"]:
            found, _ = rq_queries.run_queries(rq_queries.read_events(ROOT / r["run_dir"] / "events.jsonl"))
            q = views[r["task_id"]]
            run = f"round{payload['round']}/r{r['k']}"
            for i, item in enumerate(x for x in found if x["source"] == "retrieve_code"):
                rows.append({"qid": f"{r['task_id']}|retrieve_code|{run}|{i}", "instance_id": r["task_id"],
                             "repo": q.repo, "base_commit": q.base_commit, "set": "cr2e", "variant": "retrieve_code",
                             "run": run, "step": item["step"], "text": item["text"]})

    _write_jsonl(OUT / "queries.jsonl", rows)
    counts = defaultdict(int)
    for row in rows:
        counts[f"{row['set']}:{row['variant']}"] += 1
    cr2_tasks = {r["task_id"] for r in model_runs}
    report = {
        "counts": dict(sorted(counts.items())),
        "cr2_runs_scanned": len(model_runs),
        "cr2_tasks": len(cr2_tasks),
        "cr2_tasks_in_cr1_test": len(cr2_tasks & test_ids),
        "cr2_tasks_in_cr1_holdout": len(cr2_tasks & set(split["holdout_ids"])),
        "run_commands_not_tokenized": bad_commands,
        "identifiers_fallback_title": sum(1 for r in rows if r.get("fallback_title")),
    }
    (OUT / "queries-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


# ---------------------------------------------------------------- stage 2: retrieve


def _freeze() -> dict:
    return json.loads((DOCS / "cr1-freeze.json").read_text(encoding="utf-8"))


def _vector_file(spec, max_len: int, root: Path) -> Path:
    return root / "vectors" / f"{spec.name.replace('/', '__')}@{spec.revision}-L{max_len}.sqlite"


def _query_vectors(rows: list[dict], spec, max_len: int, frozen: ReadOnlyVectors) -> tuple[dict[str, np.ndarray], dict]:
    """sha -> float32 query vector (fp16 round trip, as in the frozen cache)."""
    from src.retrieval.embed import Encoder, VectorStore, encode_missing

    texts = {text_sha(query_text(spec, r["text"])): query_text(spec, r["text"]) for r in rows}
    new = {s: t for s, t in texts.items() if s not in frozen}
    store = VectorStore(spec, max_len, root=OUT)
    encoder = Encoder(spec, max_len)
    check_texts = [t for s, t in list(texts.items()) if s in frozen][:16]
    fresh = encoder.encode(check_texts).astype(np.float16).astype(np.float32)
    old = frozen.get_matrix([text_sha(t) for t in check_texts])
    cos = (fresh * old).sum(axis=1) / (np.linalg.norm(fresh, axis=1) * np.linalg.norm(old, axis=1))
    stats = encode_missing(encoder, store, list(new.values()))
    store.preload()
    out = {s: frozen.get_matrix([s])[0] for s in texts if s in frozen}
    if new:
        mat = store.get_matrix(list(new))
        out.update({s: mat[i] for i, s in enumerate(new)})
    trunc, n = store.truncation_stats(list(new)) if new else (0, 0)
    store.close()
    info = {"query_texts": len(texts), "from_frozen_cache": len(texts) - len(new), "encoded_new": len(new),
            "new_truncated": int(trunc), "encode": stats,
            "reencode_check": {"n": len(check_texts), "min_cosine": float(cos.min()) if len(cos) else None}}
    return out, info


def _rank_files(scores_by_file: np.ndarray, file_key: np.ndarray) -> list[int]:
    return np.lexsort((file_key, -np.asarray(scores_by_file, np.float64))).tolist()


def cmd_retrieve(_args) -> None:
    freeze = _freeze()
    key = freeze["embedding"]["model"]
    spec, max_len = MODELS[key], freeze["embedding"]["max_len"]
    hcfg = freeze["hybrid"]["config"]
    assert hcfg["fusion"] == "file" and hcfg["bm25"] == "chunk"
    rows = _jsonl(OUT / "queries.jsonl")
    cache = repo_cache.cache_dir()
    vec_path = _vector_file(spec, max_len, cache)
    repos = sorted({r["repo"] for r in rows})
    watched = [vec_path] + [cache / "index" / f"{repo_cache.repo_slug(r)}.sqlite" for r in repos]
    before = file_fingerprint(watched)

    t0 = time.time()
    frozen = ReadOnlyVectors(vec_path)
    print(f"frozen vectors: {len(frozen.index)} in {time.time() - t0:.0f}s", flush=True)
    qvecs, vec_info = _query_vectors(rows, spec, max_len, frozen)
    print(json.dumps(vec_info), flush=True)

    by_instance: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_instance[r["instance_id"]].append(r)
    out_rows = []
    for repo in repos:
        store = ReadOnlyChunkStore(repo)
        git_dir = repo_cache.repo_dir(repo)
        try:
            for iid in sorted(i for i, qs in by_instance.items() if qs[0]["repo"] == repo):
                qs = by_instance[iid]
                snap = load_snapshot(store, git_dir, qs[0]["base_commit"])
                n_files, n_chunks = len(snap.paths), len(snap.chunk_spans)
                bm25 = BM25Index(n_chunks, *snap.chunk_doc_triples())
                mat = frozen.get_matrix([text_sha(doc_text(spec, snap.chunk(i).text)) for i in range(n_chunks)])
                file_key = np.arange(n_files)
                for q in qs:
                    b = bm25.score(query_term_ids(q["text"], snap.vocab))
                    e = mat @ qvecs[text_sha(query_text(spec, q["text"]))]
                    bm25_order = _rank_files(max_by_file(b, snap.chunk_file, n_files), file_key)
                    emb_order = _rank_files(max_by_file(e, snap.chunk_file, n_files), file_key)
                    fused = rrf([ranks_from_order(bm25_order, n_files), ranks_from_order(emb_order, n_files)],
                                [1.0, hcfg["w_emb"]], hcfg["k"])
                    hyb_order = _rank_files(fused, file_key)
                    out_rows.append({"qid": q["qid"], "instance_id": iid, "rankings": {
                        name: [snap.paths[i] for i in order[:TOP_K]]
                        for name, order in (("bm25", bm25_order), ("embedding", emb_order), ("hybrid", hyb_order))}})
        finally:
            store.close()
        print(f"{repo}: done ({time.time() - t0:.0f}s)", flush=True)
    _write_jsonl(OUT / "rankings.jsonl", out_rows)

    frozen_rank = {r["instance_id"]: r["rankings"] for r in _jsonl(FROZEN_RANKINGS)}
    mismatch = defaultdict(list)
    n_full = 0
    for r in out_rows:
        if not r["qid"].endswith("|full"):
            continue
        n_full += 1
        for name in RETRIEVERS:
            if r["rankings"][name] != frozen_rank[r["instance_id"]][name]["files"]:
                mismatch[name].append(r["instance_id"])
    after = file_fingerprint(watched)
    report = {"vectors": vec_info, "n_queries": len(out_rows), "seconds": round(time.time() - t0),
              "r0_full_vs_frozen": {"instances": n_full, "mismatches": {n: mismatch.get(n, []) for n in RETRIEVERS}},
              "frozen_cache_unchanged": before == after, "frozen_cache_files": after}
    (OUT / "retrieve-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "frozen_cache_files"}, indent=2))


# ---------------------------------------------------------------- stage 3: score


def _labels(instance_ids: set[str]) -> dict:
    from src.retrieval import labels

    labels.ChunkStore = ReadOnlyChunkStore
    queries = [q for q in dataset.load_query_views() if q.instance_id in instance_ids]
    return labels.load_label_views(queries)


def _boot(a, b) -> dict:
    from src.retrieval.metrics import paired_bootstrap

    return paired_bootstrap(a, b, n_boot=N_BOOT, seed=SEED)


def _summary(rows: list[dict]) -> dict:
    from src.retrieval.metrics import summarize

    return summarize(rows, METRICS)


def _round(obj, nd: int = 4):
    if isinstance(obj, float):
        return round(obj, nd)
    if isinstance(obj, dict):
        return {k: _round(v, nd) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round(v, nd) for v in obj]
    return obj


def _task_table(per_task: dict, variant: str, retriever: str, tasks: list[str]) -> list[float]:
    return [per_task[(t, variant, retriever)]["hit@5"] for t in tasks]


def cmd_score(_args) -> None:
    from src.retrieval.metrics import score_instance

    queries = {r["qid"]: r for r in _jsonl(OUT / "queries.jsonl")}
    rankings = _jsonl(OUT / "rankings.jsonl")
    lvs = _labels({r["instance_id"] for r in queries.values()})
    retrieve_report = json.loads((OUT / "retrieve-report.json").read_text(encoding="utf-8"))
    r0 = retrieve_report["r0_full_vs_frozen"]
    r0_ok = r0["instances"] == 400 and not any(r0["mismatches"].values())
    if not r0_ok:
        raise SystemExit("R0 failed: full-issue rankings differ from the frozen CR-1 test rankings")

    per_query = []
    for rk in rankings:
        q = queries[rk["qid"]]
        lv = lvs[q["instance_id"]]
        if lv.unretrievable:
            continue
        for name in RETRIEVERS:
            m = score_instance(lv, rk["rankings"][name], [])
            gold = set(lv.gold_files)
            rank = next((i for i, p in enumerate(rk["rankings"][name], 1) if p in gold), None)
            per_query.append({"qid": q["qid"], "instance_id": q["instance_id"], "repo": q["repo"], "set": q["set"],
                              "variant": q["variant"], "run": q.get("run"), "retriever": name,
                              **{k: m[k] for k in METRICS}, "gold_rank": rank})

    # per (task, variant, retriever): mean over the variant's queries (one for issue variants, runs for model ones)
    acc: dict[tuple, list[dict]] = defaultdict(list)
    for row in per_query:
        acc[(row["instance_id"], row["variant"], row["retriever"])].append(row)
    per_task = {k: {"repo": v[0]["repo"], "n": len(v), **{m: float(np.mean([x[m] for x in v])) for m in METRICS}}
                for k, v in acc.items()}

    test_tasks = sorted({r["instance_id"] for r in per_query if r["set"] == "test"})
    report: dict = {"r0": {"ok": r0_ok, **r0}, "frozen_cache_unchanged": retrieve_report["frozen_cache_unchanged"],
                    "vectors": retrieve_report["vectors"],
                    "queries": json.loads((OUT / "queries-report.json").read_text(encoding="utf-8"))}

    # A. test 400: variant x retriever
    table = {}
    for v in ISSUE_VARIANTS:
        for r in RETRIEVERS:
            rows = [{"repo": per_task[(t, v, r)]["repo"], **{m: per_task[(t, v, r)][m] for m in METRICS}}
                    for t in test_tasks]
            table[f"{v}|{r}"] = _summary(rows)
    paired = {}
    for v in ISSUE_VARIANTS[1:]:
        for r in RETRIEVERS:
            paired[f"{v}|{r} - full|{r}"] = {
                m: _boot([per_task[(t, v, r)][m] for t in test_tasks], [per_task[(t, "full", r)][m] for t in test_tasks])
                for m in METRICS}
    for v in ISSUE_VARIANTS:
        for r in RETRIEVERS:
            if r != "hybrid":
                paired[f"{v}|{r} - full|hybrid"] = {"hit@5": _boot(_task_table(per_task, v, r, test_tasks),
                                                                   _task_table(per_task, "full", "hybrid", test_tasks))}
    h5 = {(v, r): table[f"{v}|{r}"]["all"]["hit@5"] for v in ISSUE_VARIANTS for r in RETRIEVERS}
    report["test"] = {
        "n_tasks": len(test_tasks), "table": table, "paired": paired,
        "hit5_range_across_retrievers_per_query": {v: max(h5[(v, r)] for r in RETRIEVERS) - min(h5[(v, r)] for r in RETRIEVERS)
                                                   for v in ISSUE_VARIANTS},
        "hit5_range_across_queries_per_retriever": {r: max(h5[(v, r)] for v in ISSUE_VARIANTS) - min(h5[(v, r)] for v in ISSUE_VARIANTS)
                                                    for r in RETRIEVERS},
    }

    # B. CR-2 tasks: issue variants vs the model's own queries
    all_cr2_tasks = {r["task_id"] for r in json.loads((DOCS / "cr2-runs.json").read_text(encoding="utf-8"))["runs"]}
    cr2_tasks = sorted(t for t in all_cr2_tasks if (t, "full", "hybrid") in per_task)
    cr2 = {"n_tasks": len(cr2_tasks), "unretrievable_tasks": sorted(all_cr2_tasks - set(cr2_tasks)),
           "tables": {}, "paired": {}}
    for v in ISSUE_VARIANTS + MODEL_VARIANTS:
        tasks = [t for t in cr2_tasks if (t, v, "hybrid") in per_task]
        for r in RETRIEVERS:
            rows = [{"repo": per_task[(t, v, r)]["repo"], **{m: per_task[(t, v, r)][m] for m in METRICS}} for t in tasks]
            cr2["tables"][f"{v}|{r}"] = {"n_tasks": len(tasks), **_summary(rows)["all"]}
            if v in MODEL_VARIANTS:
                cr2["tables"][f"{v}|{r}"]["n_runs"] = sum(per_task[(t, v, r)]["n"] for t in tasks)
                cr2["tables"][f"full|{r}|same_tasks_as_{v}"] = {
                    "n_tasks": len(tasks), **{m: float(np.mean([per_task[(t, 'full', r)][m] for t in tasks])) for m in METRICS}}
                cr2["paired"][f"{v}|{r} - full|{r}"] = {
                    m: _boot([per_task[(t, v, r)][m] for t in tasks], [per_task[(t, "full", r)][m] for t in tasks])
                    for m in METRICS}
                cr2["paired"][f"{v}|{r} - full|hybrid"] = {"hit@5": _boot(
                    _task_table(per_task, v, r, tasks), _task_table(per_task, "full", "hybrid", tasks))}
    # Diagnostic: how many model_first queries came at or after the run's first gold touch.
    analysis = json.loads((DOCS / "cr2-analysis.json").read_text(encoding="utf-8"))
    gold_step = {(r["task_id"], f"{r['group']}/r{r['k']}"): r["first_gold_step"] for r in analysis["per_run"]}
    firsts = [q for q in queries.values() if q["variant"] == "model_first"]
    src = defaultdict(int)
    for q in firsts:
        src[q["source"]] += 1
    cr2["model_first_source"] = dict(src)
    cr2["model_first_step_median"] = float(np.median([q["step"] for q in firsts])) if firsts else None
    cr2["model_first_at_or_after_gold_touch"] = sum(1 for q in firsts if q["step"] >= gold_step[(q["instance_id"], q["run"])])
    cr2["model_first_runs"] = len(firsts)
    cr2["runs_without_search_query"] = 2 * 280 - len(firsts)
    report["cr2"] = cr2

    # C. CR-2e retrieve_code queries, one by one
    cr2e = []
    for row in sorted((q for q in queries.values() if q["set"] == "cr2e"), key=lambda q: q["qid"]):
        hits = {x["retriever"]: x for x in per_query if x["qid"] == row["qid"]}
        cr2e.append({"instance_id": row["instance_id"], "run": row["run"], "step": row["step"], "text": row["text"][:300],
                     **{f"{r}_gold_rank": hits[r]["gold_rank"] for r in RETRIEVERS if r in hits},
                     "full_hybrid_gold_rank": next((x["gold_rank"] for x in per_query if x["qid"] == f"{row['instance_id']}|full"
                                                    and x["retriever"] == "hybrid"), None)})
    report["cr2e_retrieve_code"] = cr2e

    # D. step-0 potential value in slow CR-2 N runs
    n_runs = [r for r in analysis["per_run"] if r["group"] == "n" and r["task_id"] in set(cr2_tasks)]
    steps = np.array([r["first_gold_step"] for r in n_runs])
    potential = {"n_runs": len(n_runs), "first_gold_step_quantiles": {
        q: float(np.percentile(steps, q)) for q in (50, 75, 90)}, "by_k": {}}
    for k in (SLOW_K,) + SLOW_K_SENSITIVITY:
        slow = [r for r in n_runs if r["first_gold_step"] > k]
        fast = [r for r in n_runs if r["first_gold_step"] <= k]
        slow_tasks = sorted({r["task_id"] for r in slow})
        cell = {"slow_runs": len(slow), "slow_tasks": len(slow_tasks),
                "slow_runs_never_touched": sum(r["first_gold_step"] == analysis["never"] for r in slow), "configs": {}}
        for v in ISSUE_VARIANTS:
            for r in RETRIEVERS:
                hit = lambda t: per_task[(t, v, r)]["hit@5"] == 1.0  # noqa: E731
                cell["configs"][f"{v}|{r}"] = {
                    "slow_runs_hit5": sum(hit(x["task_id"]) for x in slow) / len(slow) if slow else None,
                    "slow_tasks_hit5": sum(hit(t) for t in slow_tasks) / len(slow_tasks) if slow_tasks else None,
                    "fast_runs_hit5": sum(hit(x["task_id"]) for x in fast) / len(fast) if fast else None,
                }
        cell["slow_tasks_list"] = slow_tasks
        potential["by_k"][str(k)] = cell
    prefetch = json.loads((DOCS / "cr2-prefetch.json").read_text(encoding="utf-8"))["tasks"]
    in_prefetch = sum(bool({f["path"] for f in p["files"]} & set(lvs[p["task"]].gold_files))
                      for p in prefetch if p["task"] in set(cr2_tasks))
    potential["check_full_hybrid_vs_prefetch"] = {
        "prefetch_top5_has_gold": in_prefetch,
        "full_hybrid_hit5": int(sum(per_task[(t, "full", "hybrid")]["hit@5"] for t in cr2_tasks)), "tasks": len(cr2_tasks)}
    report["potential"] = potential

    # Decisions
    full_h = h5[("full", "hybrid")]
    d1 = [k for k in (f"{v}|{r}" for v in ISSUE_VARIANTS[1:] for r in RETRIEVERS)
          if paired[f"{k} - full|{k.split('|')[1]}"]["hit@5"]["diff"] >= GAIN
          and paired[f"{k} - full|{k.split('|')[1]}"]["hit@5"]["lo"] > 0 and table[k]["all"]["hit@5"] >= full_h]
    d2 = []
    for v in MODEL_VARIANTS:
        for r in RETRIEVERS:
            p = cr2["paired"].get(f"{v}|{r} - full|{r}")
            if p and p["hit@5"]["diff"] >= GAIN and p["hit@5"]["lo"] > 0 \
                    and cr2["tables"][f"{v}|{r}"]["hit@5"] >= cr2["tables"][f"full|hybrid|same_tasks_as_{v}"]["hit@5"]:
                d2.append(f"{v}|{r}")
    cfgs = potential["by_k"][str(SLOW_K)]["configs"]
    d3 = [k for k, c in cfgs.items() if k != "full|hybrid" and c["slow_runs_hit5"] is not None
          and c["slow_runs_hit5"] >= SLOW_SHARE]
    report["decision"] = {"D1": d1, "D2": d2, "D3": d3, "wire_into_experiment": bool(d1 or d2 or d3)}

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(_round(report), indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    texts = {qid: q for qid, q in queries.items()}
    _write_jsonl(PER_QUERY, ({**_round(r), "text": None if r["set"] == "test" else texts[r["qid"]]["text"][:500]}
                             for r in per_query))
    print(json.dumps(_round(report["decision"]), indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, fn in (("queries", cmd_queries), ("retrieve", cmd_retrieve), ("score", cmd_score)):
        sub.add_parser(name).set_defaults(fn=fn)
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
