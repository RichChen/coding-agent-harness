"""CR-1: build the blob-keyed chunk and token cache for every base commit.

No vectors here (CR-1b). Writes runs/cr/index-stats.json.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

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

STATS_PATH = ROOT / "runs" / "cr" / "index-stats.json"


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--repos", nargs="*")
    args = ap.parse_args()

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
