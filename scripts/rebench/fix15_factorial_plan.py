"""Build the fixed-task FIX-15 three-arm plan without model calls."""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "scripts/rebench/out/fix13_tasks.json"
OUT = ROOT / "scripts/rebench/out/fix15_tasks.json"
GROUPS = ("U-new", "N", "E-new")
SEED = 20261114


def build_plan(base: dict) -> dict:
    primary = [row for rows in base["tasks"].values() for row in rows if row.get("role") == "primary"]
    if len(primary) != 30 or len({row["instance_id"] for row in primary}) != 30:
        raise ValueError("FIX-15 requires the existing 30 primary FIX-13 tasks")
    blocks = [row["instance_id"] for row in primary]
    random.Random(SEED).shuffle(blocks)
    run_order = []
    for block, task_id in enumerate(blocks, 1):
        slots = [(group, repeat) for group in GROUPS for repeat in (1, 2)]
        random.Random(SEED + block).shuffle(slots)
        for group, repeat in slots:
            run_order.append({"seq": len(run_order) + 1, "block": block,
                              "instance_id": task_id, "group": group, "repeat": repeat})
    return {
        "schema": "fix15.e_ablation_tasks.v1",
        "source_plan": "scripts/rebench/out/fix13_tasks.json",
        "source_plan_sha256": hashlib.sha256(BASE.read_bytes()).hexdigest(),
        "tasks": base["tasks"],
        "run_order": run_order,
        "rules": {
            "groups": list(GROUPS), "repeats": [1, 2], "tasks": 30,
            "slots": len(run_order), "seed": SEED,
            "paid_authorization": "not included; review design and preflight before execution",
        },
    }


def main() -> None:
    if OUT.exists():
        raise SystemExit("FIX-15 plan already exists. Preserve it and review any revision.")
    plan = build_plan(json.loads(BASE.read_text(encoding="utf-8")))
    OUT.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(OUT), "slots": len(plan["run_order"]),
                      "source_plan_sha256": plan["source_plan_sha256"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
