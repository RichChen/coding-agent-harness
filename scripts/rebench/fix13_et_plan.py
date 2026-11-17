"""Build the frozen 60-slot ET addendum plan from the FIX-13 30-task order. No model calls."""
from __future__ import annotations

import argparse
import json
import random
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "scripts/rebench/out/fix13_tasks.json"
INGEST = ROOT / "scripts/rebench/out/fix13_ingest.json"
OUT = ROOT / "scripts/rebench/out/fix13_et_tasks.json"
SEED_WITHIN_BLOCK = 20261124


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write the plan (default: print only)")
    args = parser.parse_args()

    base = json.loads(BASE.read_text(encoding="utf-8"))
    record = json.loads(INGEST.read_text(encoding="utf-8"))
    if record.get("plan_sha256") != sha256(BASE.read_bytes()).hexdigest():
        raise SystemExit("ingest does not match the frozen FIX-13 plan")
    if record.get("pending") or record.get("unfilled"):
        raise SystemExit("ingest still has pending or unfilled positions")

    order = sorted(base["run_order"], key=lambda row: row["seq"])
    blocks = []
    for block in range(1, 31):
        rows = [row for row in order if row["block"] == block]
        if len(rows) != 8 or len({row["instance_id"] for row in rows}) != 1:
            raise SystemExit(f"block {block} is not one task with eight slots")
        blocks.append(rows[0]["instance_id"])
    if len(blocks) != 30 or len(set(blocks)) != 30:
        raise SystemExit("the FIX-13 plan does not contain 30 distinct task blocks")

    accepted = {slot["original"]: slot["final"] for slot in record["slots"]}
    missing = [task for task in blocks if task not in accepted]
    if missing:
        raise SystemExit("task has no accepted ingest: " + ", ".join(missing))
    if len(set(accepted[task] for task in blocks)) != 30:
        raise SystemExit("two original tasks map to the same accepted task")

    inner = random.Random(SEED_WITHIN_BLOCK)
    run_order = []
    for block, instance_id in enumerate(blocks, 1):
        repeats = [1, 2]
        inner.shuffle(repeats)
        for repeat in repeats:
            run_order.append({"seq": len(run_order) + 1, "block": block,
                              "instance_id": instance_id, "group": "ET", "repeat": repeat})

    plan = {
        "schema": "fix13et.tasks.v1",
        "source_plan": BASE.relative_to(ROOT).as_posix(),
        "source_plan_sha256": sha256(BASE.read_bytes()).hexdigest(),
        "run_order": run_order,
        "rules": {"group": "ET", "tasks": 30, "repeats": [1, 2], "slots": 60,
                  "block_order": "inherited from the frozen FIX-13 plan",
                  "within_block_seed": SEED_WITHIN_BLOCK},
        "note": "Post-hoc ET addendum on the same 30 tasks; two runs per task, no other arm in this batch.",
    }
    text = json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
    if args.write:
        OUT.write_text(text, encoding="utf-8")
        print(json.dumps({"path": OUT.relative_to(ROOT).as_posix(), "sha256": sha256(OUT.read_bytes()).hexdigest(),
                          "slots": len(run_order), "blocks": len(blocks)}, ensure_ascii=False))
    else:
        print(text)


if __name__ == "__main__":
    main()
