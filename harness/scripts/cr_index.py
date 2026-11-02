"""CR-1: build the blob-keyed chunk and token cache for every base commit.

    cr_index.py                       chunk/token cache for all 500 instances (runs/cr/index-stats.json)
    cr_index.py --embed MODEL --split dev [--sample N --estimate-split dev]
                                      vector cache for one model (test needs --allow-test, CR-1c only)
    cr_index.py --count-texts --split test --minus-split dev
                                      unique chunk-text counts only (no model, no retrieval)
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.retrieval import dataset, repo_cache  # noqa: E402
from src.retrieval.chunking import (  # noqa: E402
    CACHE_VERSION,
    ChunkStore,
    build_blob_record,
    is_corpus_path,
    tree_entries,
)

RUNS = ROOT / "runs" / "cr"
STATS_PATH = RUNS / "index-stats.json"


def index_repo(repo: str, commits_by_instance: dict[str, str]) -> dict:
    t0 = time.time()
    git_dir = repo_cache.repo_dir(repo)
    store = ChunkStore(repo)
    meta: dict[str, tuple[int, bool, str]] = {}  # blob -> (n_chunks, parse_ok, first path)
    built = 0
    file_instances = 0
    per_commit: dict[str, dict] = {}
    try:
        with repo_cache.BlobReader(git_dir) as reader:
            for commit in sorted(set(commits_by_instance.values())):
                entries = [e for e in tree_entries(store, git_dir, commit) if is_corpus_path(e.path)]
                file_instances += len(entries)
                n_chunks = n_fail = 0
                for e in entries:
                    if e.blob_sha not in meta:
                        rec = store.get(e.blob_sha)
                        if rec is None:
                            rec = build_blob_record(reader.read(e.blob_sha), store.vocab)
                            store.put(e.blob_sha, rec)
                            built += 1
                            if built % 2000 == 0:
                                store.flush()
                        meta[e.blob_sha] = (len(rec.spans), rec.parse_ok, e.path)
                    n, ok, _ = meta[e.blob_sha]
                    n_chunks += n
                    n_fail += not ok
                per_commit[commit] = {"files": len(entries), "chunks": n_chunks, "ast_failed_files": n_fail}
        store.flush()
        vocab_size = len(store.vocab)
    finally:
        store.close()
    failed = sorted(p for n, ok, p in meta.values() if not ok)
    return {
        "repo": repo,
        "seconds": round(time.time() - t0, 1),
        "commits": len(per_commit),
        "unique_blobs": len(meta),
        "built_now": built,
        "file_instances": file_instances,
        "ast_failed_blobs": len(failed),
        "ast_failed_paths_sample": failed[:30],
        "ast_failed_file_instances": sum(c["ast_failed_files"] for c in per_commit.values()),
        "unique_chunks": sum(n for n, _, _ in meta.values()),
        "vocab_size": vocab_size,
        "per_instance": {iid: {**per_commit[c], "commit": c} for iid, c in commits_by_instance.items()},
        "sqlite_bytes": (repo_cache.cache_dir() / "index" / f"{repo_cache.repo_slug(repo)}.sqlite").stat().st_size,
    }


SPLIT_PATH = ROOT / "docs" / "retrieval" / "cr1-split.json"
SAMPLE_SEED = 20261018


def split_queries(split: str, sample: int | None = None) -> list:
    ids = json.loads(SPLIT_PATH.read_text(encoding="utf-8"))[f"{split}_ids"]
    if sample:
        ids = random.Random(SAMPLE_SEED).sample(sorted(ids), sample)
    ids = set(ids)
    return [q for q in dataset.load_query_views() if q.instance_id in ids]


def snapshot_chunk_texts(queries) -> dict[str, list[str]]:
    """instance_id -> chunk texts (header + body) of its base-commit snapshot."""
    from src.retrieval.chunking import load_snapshot

    out = {}
    by_repo: dict[str, list] = {}
    for q in queries:
        by_repo.setdefault(q.repo, []).append(q)
    for repo, qs in sorted(by_repo.items()):
        store = ChunkStore(repo)
        git_dir = repo_cache.repo_dir(repo)
        try:
            for q in qs:
                snap = load_snapshot(store, git_dir, q.base_commit)
                out[q.instance_id] = [snap.chunk(i).text for i in range(len(snap.chunk_spans))]
        finally:
            store.close()
    return out


def cmd_embed(args) -> int:
    from src.retrieval.embed import MODELS, Encoder, VectorStore, doc_text, encode_missing, query_text, text_sha

    spec = MODELS[args.embed]
    queries = split_queries(args.split, args.sample)
    t0 = time.time()
    chunk_texts = snapshot_chunk_texts(queries)
    docs = list(dict.fromkeys(doc_text(spec, t) for ts in chunk_texts.values() for t in ts))
    qtexts = [query_text(spec, q.problem_statement) for q in queries]
    collect_s = time.time() - t0
    print(f"{spec.key}: {len(queries)} instances, {len(docs)} unique chunk texts, collected in {collect_s:.0f}s", flush=True)

    store = VectorStore(spec, args.max_len)
    t_load = time.time()
    enc = Encoder(spec, args.max_len, args.batch_size)
    load_s = time.time() - t_load
    enc.reset_peak()
    d = encode_missing(enc, store, docs, log=lambda m: print(m, flush=True))
    peak = enc.peak_memory_mib()
    qd = encode_missing(enc, store, qtexts)
    doc_shas = [text_sha(t) for t in docs]
    q_shas = [text_sha(t) for t in qtexts]
    trunc_d, n_d = store.truncation_stats(doc_shas)
    trunc_q, n_q = store.truncation_stats(q_shas)
    report = {
        "model": spec.name, "revision": spec.revision, "max_len": args.max_len, "batch_size": args.batch_size,
        "split": args.split, "sample": args.sample, "instances": sorted(q.instance_id for q in queries),
        "unique_chunk_texts": len(docs), "model_load_seconds": round(load_s, 1),
        "docs": d, "queries": qd, "peak_vram_mib": round(peak, 1),
        "chunk_truncated": trunc_d, "chunk_total": n_d, "query_truncated": trunc_q, "query_total": n_q,
    }
    if d["seconds"]:
        report["tokens_per_second"] = d["tokens"] / d["seconds"]
        report["texts_per_second"] = d["encoded"] / d["seconds"]
    if args.estimate_split:
        est_q = split_queries(args.estimate_split)
        est_texts = snapshot_chunk_texts(est_q)
        est_docs = list(dict.fromkeys(doc_text(spec, t) for ts in est_texts.values() for t in ts))
        todo = store.missing([text_sha(t) for t in est_docs])
        todo_set = set(todo)
        todo_texts = [t for t in est_docs if text_sha(t) in todo_set]
        lengths = enc.token_lengths(todo_texts) if todo_texts else np.zeros(0)
        tok = int(np.minimum(lengths, args.max_len).sum())
        rate = report.get("tokens_per_second")
        report["estimate"] = {
            "split": args.estimate_split, "unique_chunk_texts": len(est_docs), "missing": len(todo),
            "missing_tokens": tok, "truncated_fraction": float((lengths > args.max_len).mean()) if len(lengths) else 0.0,
            "seconds": tok / rate if rate else None,
        }
    store.close()
    out = RUNS / f"embed-{spec.key}-L{args.max_len}-{args.split}{'-s' + str(args.sample) if args.sample else ''}.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "instances"}, indent=2))
    return 0


def cmd_count_texts(args) -> int:
    """Unique chunk-text statistics for a split (no model, no vectors)."""
    queries = split_queries(args.split)
    texts = snapshot_chunk_texts(queries)
    uniq = set()
    for ts in texts.values():
        uniq.update(ts)
    other = set()
    if args.minus_split:
        for ts in snapshot_chunk_texts(split_queries(args.minus_split)).values():
            other.update(ts)
    new = uniq - other
    report = {
        "split": args.split, "instances": len(queries), "unique_chunk_texts": len(uniq),
        "minus_split": args.minus_split, "not_in_minus_split": len(new),
        "chars_not_in_minus_split": sum(len(t) for t in new), "chars_all": sum(len(t) for t in uniq),
    }
    (RUNS / f"count-texts-{args.split}.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--repos", nargs="*")
    ap.add_argument("--embed", choices=["qwen3-0.6b", "jina-code", "coderank"])
    ap.add_argument("--split", default="dev", choices=["dev", "test"])
    ap.add_argument("--sample", type=int)
    ap.add_argument("--max-len", type=int, default=512)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--estimate-split", choices=["dev"])
    ap.add_argument("--count-texts", action="store_true")
    ap.add_argument("--minus-split", choices=["dev"])
    ap.add_argument("--allow-test", action="store_true", help="embed test chunks (CR-1c only)")
    args = ap.parse_args()
    if args.embed:
        if args.split != "dev" and not args.allow_test:
            print("refusing to embed test before CR-1c (pass --allow-test only in CR-1c)")
            return 2
        return cmd_embed(args)
    if args.count_texts:
        return cmd_count_texts(args)

    queries = dataset.load_query_views()
    by_repo: dict[str, dict[str, str]] = {}
    for q in queries:
        by_repo.setdefault(q.repo, {})[q.instance_id] = q.base_commit
    repos = sorted(args.repos or by_repo, key=lambda r: -len(set(by_repo[r].values())))

    t0 = time.time()
    results = {}
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(index_repo, r, by_repo[r]): r for r in repos}
        for fut in as_completed(futs):
            res = fut.result()
            results[res["repo"]] = res
            print(
                f"{res['repo']}: {res['commits']} commits, {res['unique_blobs']} blobs "
                f"({res['built_now']} built), {res['file_instances']} file instances, "
                f"{res['ast_failed_blobs']} AST failures, {res['seconds']}s",
                flush=True,
            )
    wall = round(time.time() - t0, 1)

    tot_blobs = sum(r["unique_blobs"] for r in results.values())
    tot_inst = sum(r["file_instances"] for r in results.values())
    tot_fail = sum(r["ast_failed_blobs"] for r in results.values())
    tot_fail_inst = sum(r["ast_failed_file_instances"] for r in results.values())
    summary = {
        "cache_version": CACHE_VERSION,
        "wall_seconds": wall,
        "workers": args.workers,
        "unique_blobs": tot_blobs,
        "file_instances_over_unique_commits": tot_inst,
        "blob_reuse_rate": 1 - tot_blobs / tot_inst if tot_inst else None,
        "ast_failure_rate_blobs": tot_fail / tot_blobs if tot_blobs else None,
        "ast_failure_rate_file_instances": tot_fail_inst / tot_inst if tot_inst else None,
        "by_repo": {r: {k: v for k, v in results[r].items() if k != "per_instance"} for r in sorted(results)},
        "per_instance": {iid: v for r in results.values() for iid, v in r["per_instance"].items()},
    }
    if STATS_PATH.exists() and args.repos:
        old = json.loads(STATS_PATH.read_text(encoding="utf-8"))
        old["by_repo"].update(summary["by_repo"])
        old["per_instance"].update(summary["per_instance"])
        summary = old
    STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATS_PATH.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k not in ("by_repo", "per_instance")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
