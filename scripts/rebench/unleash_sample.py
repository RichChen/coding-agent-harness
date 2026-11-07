"""Task draw and run order for the unleash pilot (docs/analysis/UNLEASH-PILOT-DESIGN.zh-CN.md). No model calls, no Docker.

Reads runs/rebench/inventory.json (inventory.py) and the gold patches in runs/rebench/data/test-0.parquet, writes
scripts/rebench/out/unleash_pilot_tasks.json.

Pool: the 497-task static subset of inventory.subset(). Strata:
  easy   gold patch touches one file, gold patch <= EASY_PATCH_BYTES, no pre_install (141 tasks / 94 repos)
  other  the rest of the subset (356 tasks)
The file count comes from the gold patch's "diff --git" headers, not meta.num_modified_files: that field is missing
for the 110 subset tasks created 2026-03..05 and differs from the patch in 186 of the other 387, so a meta-based
stratum would put every newest task in "other". The gold patch is used here for stratification only.
Each stratum is sorted by instance_id and shuffled once with its own seed. Walking that order, a task is taken when
its repo is not yet used by any taken task; the first PER_STRATUM taken are primary, the rest of the walk is the
replacement order. The easy stratum is walked first, so repos taken there are skipped in the other stratum.
A primary that fails intake is replaced by the first unused replacement of the same stratum whose repo is not used.

Run order: task blocks in a seeded random order; inside a block the GROUPS x REPEATS runs in a seeded random order.
A replacement task takes over the block position of the task it replaces.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.rebench.inventory import DIRECT_PYTEST  # noqa: E402

INVENTORY = ROOT / "runs" / "rebench" / "inventory.json"
PARQUET = ROOT / "runs" / "rebench" / "data" / "test-0.parquet"
DIFF_FILE = re.compile(r"^diff --git a/(\S+) ", re.M)
DATASET = Path(__file__).resolve().parent / "out" / "inventory.json"
OUT = Path(__file__).resolve().parent / "out" / "unleash_pilot_tasks.json"

EASY_PATCH_BYTES = 2500
PER_STRATUM = 15
SEEDS = {"easy": 20261107, "other": 20261108, "blocks": 20261109, "within_block": 20261110}
GROUPS = ("A", "U", "UT")
REPEATS = (1, 2)
EXPECTED = {"subset": (497, 263), "easy": (141, 94)}


def in_subset(r: dict) -> bool:
    cmd = (r["test_cmd"] or "").strip()
    direct = cmd.startswith(DIRECT_PYTEST) or (cmd.startswith("env ") and " pytest " in cmd)
    return (direct and r["f2p_not_pytest"] == 0 and r["f2p"] > 0 and not r["env_yml_path"]
            and r["created_at"] >= "2025-06-01")


def is_easy(r: dict) -> bool:
    return r["patch_files"] == 1 and r["patch_bytes"] <= EASY_PATCH_BYTES and not r["pre_install"]


def check(name: str, rows: list[dict]) -> None:
    got = (len(rows), len({r["repo"] for r in rows}))
    if got != EXPECTED[name]:
        raise SystemExit(f"{name}: expected {EXPECTED[name]} (tasks, repos), got {got}")


def walk(rows: list[dict], seed: int, used: set[str]) -> list[dict]:
    order = sorted(rows, key=lambda r: r["instance_id"])
    random.Random(seed).shuffle(order)
    taken = []
    for r in order:
        if r["repo"] in used:
            continue
        used.add(r["repo"])
        taken.append(r)
    return taken


def row(r: dict, stratum: str, rank: int) -> dict:
    return dict(rank=rank, role="primary" if rank <= PER_STRATUM else "replacement", stratum=stratum,
                instance_id=r["instance_id"], repo=r["repo"], created_at=r["created_at"], month=r["month"],
                interface=r["instance_id"].endswith("_interface"), patch_files=r["patch_files"],
                meta_num_modified_files=r["num_modified_files"], patch_bytes=r["patch_bytes"], f2p=r["f2p"],
                p2p=r["p2p"], pre_install=bool(r["pre_install"]), python=r["python"], docker_image=r["docker_image"])


def run_order(primary: list[dict]) -> list[dict]:
    blocks = sorted(t["instance_id"] for t in primary)
    random.Random(SEEDS["blocks"]).shuffle(blocks)
    inner = random.Random(SEEDS["within_block"])
    runs = []
    for b, task in enumerate(blocks, 1):
        slots = [(g, k) for g in GROUPS for k in REPEATS]
        inner.shuffle(slots)
        runs += [dict(seq=len(runs) + i, block=b, instance_id=task, group=g, repeat=k)
                 for i, (g, k) in enumerate(slots, 1)]
    return runs


def main() -> None:
    raw = INVENTORY.read_bytes()
    rows = json.loads(raw)
    patches = pd.read_parquet(PARQUET, columns=["instance_id", "patch"])
    files = {i: len(set(DIFF_FILE.findall(p or ""))) for i, p in zip(patches["instance_id"], patches["patch"])}
    pool = [dict(r, patch_files=files[r["instance_id"]]) for r in rows if in_subset(r)]
    check("subset", pool)
    easy = [r for r in pool if is_easy(r)]
    check("easy", easy)
    easy_ids = {r["instance_id"] for r in easy}
    other = [r for r in pool if r["instance_id"] not in easy_ids]
    used: set[str] = set()
    walks = {"easy": walk(easy, SEEDS["easy"], used), "other": walk(other, SEEDS["other"], used)}
    for name, taken in walks.items():
        if len(taken) < PER_STRATUM:
            raise SystemExit(f"{name}: only {len(taken)} distinct repos")
    tasks = {name: [row(r, name, i) for i, r in enumerate(taken, 1)] for name, taken in walks.items()}
    primary = [t for name in walks for t in tasks[name] if t["role"] == "primary"]
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))["dataset"]
    result = dict(
        schema="unleash-pilot.tasks.v1",
        dataset=dict(name=dataset["dataset"], revision=dataset["revision"]),
        inventory_sha256=hashlib.sha256(raw).hexdigest(),
        pool=dict(subset=len(pool), subset_repos=len({r["repo"] for r in pool}), easy=len(easy),
                  easy_repos=len({r["repo"] for r in easy}), other=len(other),
                  other_repos=len({r["repo"] for r in other})),
        rules=dict(easy_patch_bytes=EASY_PATCH_BYTES, per_stratum=PER_STRATUM, max_per_repo=1, seeds=SEEDS,
                   groups=list(GROUPS), repeats=list(REPEATS)),
        primary_summary=dict(
            by_stratum=dict(Counter(t["stratum"] for t in primary)),
            repos=len({t["repo"] for t in primary}),
            by_month=dict(sorted(Counter(t["month"] for t in primary).items())),
            by_owner=dict(Counter(t["repo"].split("/")[0] for t in primary).most_common()),
            patch_bytes_median={s: sorted(t["patch_bytes"] for t in primary if t["stratum"] == s)[PER_STRATUM // 2]
                                for s in walks},
            pre_install=sum(t["pre_install"] for t in primary),
            interface=sum(t["interface"] for t in primary),
            created_after_2026_02=sum(t["created_at"] >= "2026-03-01" for t in primary),
        ),
        tasks=tasks,
        run_order=run_order(primary),
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for t in primary:
        print(f'{t["stratum"]:5} {t["rank"]:2} {t["instance_id"]:55} {t["month"]} {t["patch_bytes"]:6} '
              f'files={t["patch_files"]} apt={int(t["pre_install"])} iface={int(t["interface"])}')
    print(json.dumps(result["pool"]), json.dumps(result["primary_summary"], ensure_ascii=False))
    print("replacements:", {s: sum(t["role"] == "replacement" for t in tasks[s]) for s in tasks})


if __name__ == "__main__":
    main()
