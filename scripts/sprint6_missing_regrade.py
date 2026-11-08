"""Apply the empty-delta regrade rule to a finished run: grade its image again with an empty delta. No model calls.

Two triggers: the grader reported missing ids (missing_regrade), or test execution timed out and left a
grader reason (timeout_regrade). When the regrade has no missing id and matches calibration, the run becomes
an ordinary failure: summary.json gets grader_candidate_error ("execution" or "execution_timeout") and the
regrade verdict, and the old summary is kept as summary.pre-regrade.json. Otherwise nothing in the run changes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_a5_main import accepted_tasks, load_targets, materialize_runner_task  # noqa: E402
from scripts.run_swe_task import delta_changes, empty_calibration, empty_regrade, prerun_check  # noqa: E402
from src.eval.candidate_error import (  # noqa: E402
    classify_missing,
    classify_timeout,
    expected_empty,
    missing_count,
    regrade_verdict,
    timed_out_with_reason,
)


async def regrade_run(run_dir: Path, prepared: dict | None = None) -> dict:
    """prepared: the runner task the run used; by default looked up among the sprint targets."""
    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    evaluation = json.loads((run_dir / "grader-result.json").read_text(encoding="utf-8"))
    missing = missing_count(evaluation)
    timed_out = not missing and timed_out_with_reason(evaluation)
    if not missing and not timed_out:
        raise SystemExit("the grader reported neither missing ids nor a timed-out execution for " + str(run_dir))
    if summary.get("status") == "infra_error":
        raise SystemExit("infra_error runs are not regraded: " + str(run_dir))
    if summary.get("grader_candidate_error"):
        raise SystemExit("already classified: " + str(run_dir))
    if prepared is None:
        task = next(task for task in accepted_tasks(load_targets()) if task["id"] == summary["task_id"])
        prepared = materialize_runner_task(task)
    spec, image, clean, failure = prerun_check(prepared, mock=False)
    if failure:
        raise SystemExit("pre-run check failed: " + failure)
    if clean is not None and summary.get("clean_image_digest") not in (None, image):
        raise SystemExit("image differs from the run: " + str(image) + " vs " + str(summary.get("clean_image_digest")))
    f2p = [node for node in spec.expected_nodeids if node not in spec.baseline_passed]
    regrade = await empty_regrade(prepared, image, spec, f2p, bool(prepared.get("export_keep_original_files")))
    (run_dir / "grader-regrade-empty.json").write_text(json.dumps(regrade, indent=2, default=str), encoding="utf-8")
    verdict = regrade_verdict(regrade, expected_empty(empty_calibration(prepared)))
    if missing:
        delta_path = run_dir / "candidate-delta.json"
        changed = delta_changes(json.loads(delta_path.read_text(encoding="utf-8"))) if delta_path.is_file() else []
        error = classify_missing(evaluation, changed, verdict)
    else:
        error = classify_timeout(evaluation, verdict)
    field = "missing_regrade" if missing else "timeout_regrade"
    result = {"run": str(run_dir.relative_to(ROOT).as_posix()), "image": image, field: verdict, "grader_candidate_error": error}
    if error is not None:
        (run_dir / "summary.pre-regrade.json").write_text(summary_path.read_text(encoding="utf-8"), encoding="utf-8")
        summary["grader_candidate_error"] = error
        summary[field] = verdict
        summary["solved"] = False
        if error == "execution_timeout":
            summary["status"] = "fail"
        summary["docker_retries"] = list(summary.get("docker_retries") or []) + [
            dict(stage="regrade", **row) for row in regrade.get("docker_retries") or []]
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir")
    args = parser.parse_args()
    run_dir = Path(args.run_dir)
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir
    result = asyncio.run(regrade_run(run_dir))
    print(json.dumps(result, indent=2))
    if result["grader_candidate_error"] is None:
        raise SystemExit("the empty regrade does not clear this run; the stop stands")


if __name__ == "__main__":
    main()
