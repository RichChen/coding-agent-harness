"""Apply the empty-delta regrade rule (scripts/sprint6_missing_regrade.py) to a finished unleash pilot run.
No model calls. The task comes from the pilot manifest; the run keeps its summary unless the rule clears it.

    regrade_missing.py runs/unleash-pilot/<task>/<group>/r<k>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.rebench.run_unleash import load_inputs  # noqa: E402
from scripts.sprint6_missing_regrade import regrade_run  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir if args.run_dir.is_absolute() else ROOT / args.run_dir
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    task = load_inputs()[3][summary["task_id"]]
    result = asyncio.run(regrade_run(run_dir, task))
    print(json.dumps(result, indent=2))
    if result["grader_candidate_error"] is None:
        raise SystemExit("the empty regrade does not clear this run; the stop stands")


if __name__ == "__main__":
    main()
