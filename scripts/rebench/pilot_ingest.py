"""Ingest the 30 primary tasks of the unleash pilot, with the replacement rule of the design (section 3.3).

Each primary task goes through prepare.py (export rule export_keep_original_files on). A task that fails is
replaced by the first unused task of the same stratum's replacement queue whose repository is not taken; the
replacement goes through the same ingestion. Every substitution is recorded with the original task, the
failed clauses and the replacement. Resumable: prepare.py keeps per-task state. No model calls.

    pilot_ingest.py [--plan scripts/rebench/out/unleash_pilot_tasks.json] [--only ID ...]
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.rebench import prepare  # noqa: E402

PLAN = ROOT / "scripts" / "rebench" / "out" / "unleash_pilot_tasks.json"
MANIFEST = ROOT / "configs" / "rebench" / "unleash-pilot-tasks.yaml"
RECORD = ROOT / "scripts" / "rebench" / "out" / "unleash_pilot_ingest.json"


def choose_replacement(queue: list[dict], tried: set[str], taken_repos: set[str]) -> dict | None:
    """First task of the stratum's replacement queue not tried yet and whose repository is not taken."""
    for task in queue:
        if task["instance_id"] not in tried and task["repo"] not in taken_repos:
            return task
    return None


def attempt(instance_id: str, repeats: int, ingest) -> dict:
    try:
        state = ingest(instance_id, repeats)
    except prepare.GraderInfraError as exc:
        return {"instance_id": instance_id, "accepted": False, "failed_clauses": [], "pending": True,
                "error": type(exc).__name__ + ": " + str(exc)[-500:]}
    except Exception as exc:
        traceback.print_exc()
        return {"instance_id": instance_id, "accepted": False, "failed_clauses": [1],
                "error": type(exc).__name__ + ": " + str(exc)[-500:]}
    cal = state["calibration"]
    return {"instance_id": instance_id, "accepted": bool(state["accepted"]), "failed_clauses": cal["failed_clauses"],
            "memory_bytes": (state.get("memory") or {}).get("memory_bytes"), **cal["summary"],
            "p2p_excluded": len(cal["p2p_excluded"]), "error": None}


def ingest_plan(plan: dict, ingest, repeats: int = 3, only: set[str] | None = None) -> dict:
    """ingest(instance_id, repeats) -> prepare state. Returns slots in plan order and the substitutions."""
    slots, substitutions = [], []
    taken = {task["repo"] for stratum in ("easy", "other") for task in plan["tasks"][stratum]
             if task["role"] == "primary"}
    for stratum in ("easy", "other"):
        tasks = plan["tasks"][stratum]
        queue = [task for task in tasks if task["role"] == "replacement"]
        tried = set()
        for primary in (task for task in tasks if task["role"] == "primary"):
            if only and primary["instance_id"] not in only:
                continue
            attempts = [attempt(primary["instance_id"], repeats, ingest)]
            tried.add(primary["instance_id"])
            current = primary
            while not attempts[-1]["accepted"] and not attempts[-1].get("pending"):
                taken.discard(current["repo"])
                substitute = choose_replacement(queue, tried, taken)
                if substitute is None:
                    break
                substitutions.append({"stratum": stratum, "rank": primary["rank"], "original": primary["instance_id"],
                                      "replaced": current["instance_id"],
                                      "failed_clauses": attempts[-1]["failed_clauses"],
                                      "error": attempts[-1]["error"], "replacement": substitute["instance_id"]})
                tried.add(substitute["instance_id"])
                taken.add(substitute["repo"])
                current = substitute
                attempts.append(attempt(substitute["instance_id"], repeats, ingest))
            final = attempts[-1]["instance_id"] if attempts[-1]["accepted"] else None
            slots.append({"stratum": stratum, "rank": primary["rank"], "original": primary["instance_id"],
                          "final": final, "attempts": attempts})
    return {"slots": slots, "substitutions": substitutions,
            "accepted": sum(1 for slot in slots if slot["final"]),
            "pending": [s["attempts"][-1]["instance_id"] for s in slots if s["attempts"][-1].get("pending")],
            "unfilled": [s["original"] for s in slots if not s["final"] and not s["attempts"][-1].get("pending")]}


def task_map(record: dict) -> dict[str, str]:
    """Original instance id -> ingested instance id. A replacement inherits the original's run-order slots."""
    return {slot["original"]: slot["final"] for slot in record["slots"] if slot["final"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plan", type=Path, default=PLAN)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--record", type=Path, default=RECORD)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--only", action="append", default=[])
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))

    def ingest(instance_id, repeats):
        print("==", instance_id, flush=True)
        state = prepare.prepare_task(instance_id, repeats, keep_original_files=True, manifest=args.manifest)
        print(instance_id, json.dumps({"accepted": state["accepted"],
                                       "failed_clauses": state["calibration"]["failed_clauses"],
                                       "memory_bytes": (state.get("memory") or {}).get("memory_bytes")}), flush=True)
        return state

    record = ingest_plan(plan, ingest, args.repeats, set(args.only) or None)
    record = {"schema": "unleash-pilot.ingest.v1", "plan_sha256": prepare.hashlib.sha256(args.plan.read_bytes()).hexdigest(),
              "manifest": prepare.rel(args.manifest), "export_keep_original_files": True, "model_calls": 0, **record}
    if not args.only:
        args.record.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: record[k] for k in ("accepted", "pending", "unfilled", "substitutions")}, indent=2))


if __name__ == "__main__":
    main()
