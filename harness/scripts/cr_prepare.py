"""CR-1: clone repos, verify base commits, write the dev/test split, corpus stats.

No model calls. Writes docs/retrieval/cr1-split.json and runs/cr/prepare-stats.json.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.retrieval import dataset, repo_cache  # noqa: E402
from src.retrieval.chunking import ChunkStore, is_test_path, tree_entries  # noqa: E402

SEED = 20261018
DEV_SIZE = 100
CLEAN_IMAGES = ROOT / "docs" / "sprint" / "clean-images"
MAIN_TARGETS = ROOT / "configs" / "sprint" / "main-targets.yaml"
SPLIT_PATH = ROOT / "docs" / "retrieval" / "cr1-split.json"
STATS_PATH = ROOT / "runs" / "cr" / "prepare-stats.json"


def holdout_ids(verified: set[str]) -> dict:
    images = sorted(p.stem for p in CLEAN_IMAGES.glob("*.json"))
    targets = sorted(t["id"] for t in yaml.safe_load(MAIN_TARGETS.read_text(encoding="utf-8"))["tasks"])
    union = sorted(set(images) | set(targets))
    return {
        "clean_images": len(images),
        "clean_images_in_verified": len(set(images) & verified),
        "main_targets": len(targets),
        "main_targets_in_verified": len(set(targets) & verified),
        "union": len(union),
        "ids": sorted(set(union) & verified),
        "not_in_verified": sorted(set(union) - verified),
    }


def allocate(pool_by_repo: dict[str, list[str]], total: int) -> dict[str, int]:
    """Proportional allocation with largest remainder; ties by repo name."""
    n = sum(len(v) for v in pool_by_repo.values())
    exact = {r: total * len(v) / n for r, v in pool_by_repo.items()}
    quota = {r: int(x) for r, x in exact.items()}
    left = total - sum(quota.values())
    for r in sorted(exact, key=lambda r: (-(exact[r] - quota[r]), r))[:left]:
        quota[r] += 1
    return quota


def make_split(queries: list[dataset.QueryView], parquet_sha: str) -> dict:
    verified = {q.instance_id for q in queries}
    repo_of = {q.instance_id: q.repo for q in queries}
    hold = holdout_ids(verified)
    held = set(hold["ids"])
    pool: dict[str, list[str]] = defaultdict(list)
    for iid in sorted(verified - held):
        pool[repo_of[iid]].append(iid)
    quota = allocate(pool, DEV_SIZE)
    rng = random.Random(SEED)
    dev: list[str] = []
    for repo in sorted(pool):
        dev.extend(rng.sample(pool[repo], quota[repo]))
    dev = sorted(dev)
    test = sorted(verified - set(dev))
    counts = {}
    for repo in sorted({q.repo for q in queries}):
        ids = [i for i in verified if repo_of[i] == repo]
        counts[repo] = {
            "total": len(ids),
            "holdout": sum(i in held for i in ids),
            "dev": sum(i in set(dev) for i in ids),
            "test": sum(i in set(test) for i in ids),
        }
    return {
        "schema": "cr1-split.v1",
        "parquet": str(dataset.PARQUET_PATH).replace("\\", "/"),
        "parquet_sha256": parquet_sha,
        "seed": SEED,
        "dev_size": DEV_SIZE,
        "method": (
            "holdout = (docs/sprint/clean-images/*.json stems | configs/sprint/main-targets.yaml ids) & Verified, "
            "all in test. Remaining instances grouped by repo; dev quota per repo = largest-remainder "
            "proportional allocation of 100 (ties by repo name); random.Random(seed).sample over each repo's "
            "sorted ids, repos visited in sorted order. Everything not in dev is test."
        ),
        "holdout_sources": {k: v for k, v in hold.items() if k != "ids"},
        "holdout_ids": hold["ids"],
        "counts_by_repo": counts,
        "dev_ids": dev,
        "test_ids": test,
    }


def dist(xs) -> dict:
    a = np.asarray(xs, np.float64)
    if a.size == 0:
        return {}
    return {
        "n": int(a.size), "min": float(a.min()), "p10": float(np.percentile(a, 10)),
        "median": float(np.median(a)), "mean": float(a.mean()), "p90": float(np.percentile(a, 90)),
        "max": float(a.max()),
    }


def dir_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-clone", action="store_true")
    args = ap.parse_args()

    queries = dataset.load_query_views()
    parquet_sha = dataset.parquet_sha256()
    repos = sorted({q.repo for q in queries})
    print(f"{len(queries)} instances, {len(repos)} repos, {len({q.base_commit for q in queries})} base commits")

    clone_info = {}
    if not args.skip_clone:
        for repo in repos:
            t0 = time.time()
            path, now = repo_cache.ensure_clone(repo)
            clone_info[repo] = {"cloned_now": now, "seconds": round(time.time() - t0, 1)}
            print(f"clone {repo}: {'cloned' if now else 'present'} ({clone_info[repo]['seconds']}s)")

    sizes = {repo: dir_size(repo_cache.repo_dir(repo)) for repo in repos}
    unresolved = []
    for repo in repos:
        shas = sorted({q.base_commit for q in queries if q.repo == repo})
        res = repo_cache.resolve_commits(repo_cache.repo_dir(repo), shas)
        unresolved += [{"repo": repo, "commit": s} for s, ok in res.items() if not ok]
    n_commits = len({q.base_commit for q in queries})
    print(f"commits resolved: {n_commits - len(unresolved)}/{n_commits}; unresolved: {unresolved}")

    split = make_split(queries, parquet_sha)
    SPLIT_PATH.write_text(json.dumps(split, indent=2) + "\n", encoding="utf-8")
    print(f"split: holdout={len(split['holdout_ids'])} dev={len(split['dev_ids'])} test={len(split['test_ids'])}")

    per_instance = {}
    excluded_test_py = Counter()
    unresolved_set = {u["commit"] for u in unresolved}
    for repo in repos:
        store = ChunkStore(repo)
        git_dir = repo_cache.repo_dir(repo)
        try:
            for q in (q for q in queries if q.repo == repo):
                if q.base_commit in unresolved_set:
                    continue
                entries = tree_entries(store, git_dir, q.base_commit)
                py = [e for e in entries if e.path.endswith(".py")]
                corpus = [e for e in py if not is_test_path(e.path)]
                per_instance[q.instance_id] = {
                    "repo": repo, "files": len(entries), "py_files": len(py), "corpus_files": len(corpus),
                }
                excluded_test_py[repo] += len(py) - len(corpus)
        finally:
            store.close()

    dev = set(split["dev_ids"])
    stats = {
        "parquet_sha256": parquet_sha,
        "clone": clone_info,
        "repo_cache_bytes": sizes,
        "repo_cache_total_bytes": sum(sizes.values()),
        "commits_total": n_commits,
        "commits_unresolved": unresolved,
        "corpus_files_per_instance": {
            "all": dist([v["corpus_files"] for v in per_instance.values()]),
            "dev": dist([v["corpus_files"] for k, v in per_instance.items() if k in dev]),
            "by_repo": {
                r: dist([v["corpus_files"] for v in per_instance.values() if v["repo"] == r]) for r in repos
            },
        },
        "test_py_files_excluded_per_instance_mean": {
            r: excluded_test_py[r] / max(1, sum(v["repo"] == r for v in per_instance.values())) for r in repos
        },
        "per_instance": per_instance,
    }
    STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATS_PATH.write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
    print(f"repo cache: {sum(sizes.values()) / 2**30:.2f} GiB")
    print(json.dumps(stats["corpus_files_per_instance"]["all"]))
    return 0 if not unresolved else 1


if __name__ == "__main__":
    raise SystemExit(main())
