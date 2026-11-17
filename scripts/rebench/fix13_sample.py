"""Freeze FIX-13's independent task draw. Local metadata only, no model or Docker calls."""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

import pandas as pd
import yaml

from scripts.rebench import unleash_sample as previous

ROOT = previous.ROOT
OUT = ROOT / "scripts/rebench/out/fix13_tasks.json"
EXPOSURE = ROOT / "scripts/rebench/out/fix13_exposure.json"
SEEDS = {"easy": 20261113, "other": 20261114, "blocks": 20261115, "within_block": 20261116}
GROUPS = ("U", "R", "E", "UT")


def exposure(inventory: list[dict]) -> dict:
    """Conservatively exclude repositories in existing structured task records."""
    by_id = {r["instance_id"]: r for r in inventory}
    task_ids, repos, sources = set(), set(), {}
    for path in sorted((ROOT / "configs").rglob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
            continue
        tasks = [t for t in data["tasks"] if isinstance(t, dict)]
        if not tasks:
            continue
        sources[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        task_ids.update(t["id"] for t in tasks if t.get("id"))
        repos.update(t["repo"].casefold() for t in tasks if t.get("repo"))
    ingest = ROOT / "scripts/rebench/out/unleash_pilot_ingest.json"
    sources[ingest.relative_to(ROOT).as_posix()] = hashlib.sha256(ingest.read_bytes()).hexdigest()
    for slot in json.loads(ingest.read_text(encoding="utf-8"))["slots"]:
        task_ids.update(a["instance_id"] for a in slot["attempts"])
    directories = {}
    for name in ("public", "private", "work"):
        folder = ROOT / "runs/rebench" / name
        names = sorted(p.stem if p.is_file() else p.name for p in folder.iterdir()) if folder.exists() else []
        directories[folder.relative_to(ROOT).as_posix()] = names
        task_ids.update(names)
    # Older run families may contain tasks no longer in the current manifests.
    run_ids = set()
    for family in (ROOT / "runs").iterdir():
        if family.is_dir():
            run_ids.update(p.name for p in family.iterdir() if p.name in by_id)
    task_ids.update(run_ids)
    repos.update(by_id[i]["repo"].casefold() for i in task_ids if i in by_id)
    return {"schema": "fix13.exposure.v1", "sources": sources, "directories": directories,
            "run_directory_task_ids": sorted(run_ids), "task_ids": sorted(task_ids), "repos": sorted(repos),
            "boundary": "Existing task manifests, FIX-12 intake attempts, rebench task files/work, and run task directories. "
                        "This does not certify absence from model training or all external human browsing."}


def draw(pool: list[dict], excluded_repos: set[str]) -> dict[str, list[dict]]:
    eligible = [r for r in pool if r["repo"].casefold() not in excluded_repos]
    easy = [r for r in eligible if previous.is_easy(r)]
    other = [r for r in eligible if not previous.is_easy(r)]
    used = set()
    walks = {"easy": previous.walk(easy, SEEDS["easy"], used),
             "other": previous.walk(other, SEEDS["other"], used)}
    if any(len(rows) < 15 for rows in walks.values()):
        raise ValueError("not enough independent repositories for 15 tasks per stratum")
    return {name: [previous.row(r, name, rank) for rank, r in enumerate(rows, 1)]
            for name, rows in walks.items()}


def build_plan(pool: list[dict], excluded_repos: set[str]) -> dict:
    tasks = draw(pool, excluded_repos)
    primary = [r for rows in tasks.values() for r in rows if r["role"] == "primary"]
    blocks = sorted(r["instance_id"] for r in primary)
    random.Random(SEEDS["blocks"]).shuffle(blocks)
    inner = random.Random(SEEDS["within_block"])
    order = []
    for block, task_id in enumerate(blocks, 1):
        slots = [(g, repeat) for g in GROUPS for repeat in (1, 2)]
        inner.shuffle(slots)
        for group, repeat in slots:
            order.append({"seq": len(order) + 1, "block": block, "instance_id": task_id,
                          "group": group, "repeat": repeat})
    by_id = {r["instance_id"]: r for r in primary}
    return {"schema": "fix13.tasks.v1", "tasks": tasks, "run_order": order,
            "first_five": [by_id[i] for i in blocks[:5]],
            "rules": {"seeds": SEEDS, "groups": list(GROUPS), "repeats": [1, 2],
                      "per_stratum": 15, "max_per_repo": 1, "exclude_exposed_repositories": True},
            "note": "Only the first five task positions are authorized for no-model intake. "
                    "No paid run is authorized by this plan."}


def main() -> None:
    if OUT.exists() or EXPOSURE.exists():
        raise SystemExit("FIX-13 draw already exists. Preserve it and review any proposed revision.")
    raw = previous.INVENTORY.read_bytes()
    inventory = json.loads(raw)
    exposed = exposure(inventory)
    patches = pd.read_parquet(previous.PARQUET, columns=["instance_id", "patch"])
    files = {i: len(set(previous.DIFF_FILE.findall(p or "")))
             for i, p in zip(patches["instance_id"], patches["patch"])}
    pool = [dict(r, patch_files=files[r["instance_id"]]) for r in inventory if previous.in_subset(r)]
    plan = build_plan(pool, set(exposed["repos"]))
    plan.update(dataset=json.loads(previous.DATASET.read_text(encoding="utf-8"))["dataset"],
                inventory_sha256=hashlib.sha256(raw).hexdigest(),
                exposure_path=EXPOSURE.relative_to(ROOT).as_posix(),
                exposure_sha256=hashlib.sha256((json.dumps(exposed, ensure_ascii=False, indent=2) + "\n").encode()).hexdigest())
    EXPOSURE.write_bytes((json.dumps(exposed, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    OUT.write_bytes((json.dumps(plan, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(json.dumps({"plan": str(OUT), "exposure": str(EXPOSURE), "first_five": plan["first_five"]}, indent=2))


if __name__ == "__main__":
    main()
