"""Read-only reconciliation of the FIX-14 batch against the filesystem. No model calls.

The launcher's progress.json is a derived view built from the rows it holds in memory.
A resume rebuilds those rows from disk, so progress.json and the disk can disagree by a
small amount while a slot is being rerun. This script is the independent check: it walks
the frozen slot list and reports final slots, retired attempts, and slot directories that
exist without a summary. Use it before accepting the final batch report.

    .venv/Scripts/python.exe scripts/analysis/fix14_reconcile.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.rebench import run_fix14 as launcher  # noqa: E402
from scripts.rebench import run_unleash as legacy  # noqa: E402

WRITE = False


def main() -> dict:
    slots, _ = launcher.load_inputs()
    finals, retired, without_summary, matrix = [], [], [], []
    for slot in slots:
        final = legacy.slot_dir(launcher.OUT, slot)
        state = "missing"
        for path in [*sorted(final.parent.glob(final.name + ".infra*")), final]:
            if not path.exists():
                continue
            entry = {"task": slot["task"], "group": slot["group"], "repeat": slot["repeat"],
                     "dir": str(path.relative_to(launcher.OUT)).replace("\\", "/")}
            if "." in path.name:
                summary = path / "summary.json"
                entry["infra_reason"] = (json.loads(summary.read_text(encoding="utf-8")).get("infra_reason")
                                         if summary.is_file() else None)
                retired.append(entry)
                state = "infra_retry" if state != "done" else state
            elif (path / "summary.json").is_file():
                finals.append(entry)
                state = "done"
            else:
                without_summary.append(entry)
                state = "in_flight"
        matrix.append({"task": slot["task"], "group": slot["group"], "repeat": slot["repeat"],
                       "state": state})
    progress = (json.loads((launcher.OUT / "progress.json").read_text(encoding="utf-8"))
                if (launcher.OUT / "progress.json").is_file() else {})
    batch_report = launcher.OUT / "batch-report.json"
    report = {"planned_slots": launcher.EXPECTED_RUNS,
              "final_slots_with_summary": len(finals),
              "retired_attempts": len(retired), "retired": retired,
              "slot_dirs_without_summary": len(without_summary), "without_summary": without_summary,
              "slots_by_state": {state: sum(1 for row in matrix if row["state"] == state)
                                 for state in ("done", "in_flight", "infra_retry", "missing")},
              "matrix": matrix,
              "progress_final_runs": progress.get("final_runs"),
              "progress_known_settled_cny": progress.get("known_settled_cny"),
              "progress_recovery_reserve_cny": progress.get("recovery_reserve_cny"),
              "progress_halt_reason": progress.get("halt_reason"),
              "batch_report_is_current": (batch_report.is_file()
                                          and json.loads(batch_report.read_text(encoding="utf-8"))
                                          .get("final_runs") == len(finals)),
              "batch_report_last_write": (datetime.fromtimestamp(batch_report.stat().st_mtime).isoformat()
                                          if batch_report.is_file() else None),
              "consistent": len(finals) == progress.get("final_runs"),
              "complete": len(finals) == launcher.EXPECTED_RUNS}
    if WRITE:
        (launcher.OUT / "status.json").write_text(
            json.dumps({k: v for k, v in report.items() if k != "matrix"}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "matrix"}, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    WRITE = "--write" in sys.argv
    main()
