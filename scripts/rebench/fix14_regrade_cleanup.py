"""Regrade a run that only failed container cleanup, from its captured candidate patch.

A run whose agent phase finished and whose candidate delta was exported, but whose
``docker rm`` timed out, is stored as ``status=infra_error`` with ``stop_reason=cleanup_failed``.
The model work exists in ``candidate-delta.json`` and was never graded. This script grades that
exact delta against the same image, test overlay and node ids as the normal entry point, so the
slot becomes a real measurement without another model call.

It refuses anything else: the summary must be an infra error caused by cleanup, and the delta must
be present. The original grader result and summary are kept beside the run as ``*.infra.json``.

    .venv/Scripts/python.exe scripts/rebench/fix14_regrade_cleanup.py runs/fix14/<task>/<group>/r<k>
    .venv/Scripts/python.exe scripts/rebench/fix14_regrade_cleanup.py --apply <same paths>

Without --apply it grades and prints the outcome but writes nothing.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.rebench import run_fix14 as launcher  # noqa: E402
from scripts.run_swe_task import (  # noqa: E402
    classify_missing, classify_timeout, delta_changes, empty_calibration, empty_regrade,
    expected_empty, grader_infra_reason, grader_options, missing_count, prerun_check,
    regrade_verdict, timed_out_with_reason,
)
from src.eval.image_grader import ImageGrader  # noqa: E402
from src.harness.factory import load_config  # noqa: E402


def keep_original_files(run_dir: Path) -> bool:
    group = run_dir.parent.name
    config = load_config(launcher.PROFILES[group])
    return bool(config["policy"]["command"].get("export_keep_original_files", False))


def preserve(run_dir: Path, name: str) -> None:
    path = run_dir / name
    backup = run_dir / name.replace(".json", ".infra.json")
    if path.is_file() and not backup.exists():
        backup.write_bytes(path.read_bytes())


async def regrade(run_dir: Path, *, apply: bool) -> dict:
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    if summary.get("status") != "infra_error" or summary.get("stop_reason") != "cleanup_failed":
        raise ValueError(f"not a cleanup-only infra error: status={summary.get('status')} "
                         f"stop={summary.get('stop_reason')}")
    delta_path = run_dir / "candidate-delta.json"
    if not delta_path.is_file():
        raise ValueError("candidate-delta.json is missing; the model work is not recoverable here")
    delta = json.loads(delta_path.read_text(encoding="utf-8"))
    if not isinstance(delta.get("changed"), dict):
        raise ValueError("candidate delta has no changed mapping")

    _, tasks = launcher.load_inputs()
    task = tasks[summary["task_id"]]
    spec, image, clean, failure = prerun_check(task, mock=False)
    if failure:
        raise ValueError("pre-run check failed: " + str(failure))
    if not clean:
        raise ValueError("the clean image identity could not be verified")
    keep_original = keep_original_files(run_dir)
    f2p = [node for node in spec.expected_nodeids if node not in spec.baseline_passed]
    evaluation = await ImageGrader().evaluate(
        image=image, delta=delta, test_overlay=spec.test_overlay, f2p=f2p,
        p2p=list(spec.baseline_passed), django=task.get("family") == "django", sympy=False,
        timeout=float(task["grader_timeout_seconds"]), keep_original_files=keep_original,
        **grader_options(task),
    )
    infra_reason = grader_infra_reason(evaluation)
    missing = missing_count(evaluation)
    timed_out = not infra_reason and not missing and timed_out_with_reason(evaluation)
    verdict, candidate_error = None, None
    if (missing or timed_out) and not infra_reason:
        regrade_result = await empty_regrade(task, image, spec, f2p, keep_original)
        if apply:
            (run_dir / "grader-regrade-empty.json").write_text(
                json.dumps(regrade_result, indent=2, default=str), encoding="utf-8")
        verdict = regrade_verdict(regrade_result, expected_empty(empty_calibration(task)))
        candidate_error = (classify_missing(evaluation, delta_changes(delta), verdict) if missing
                           else classify_timeout(evaluation, verdict))
    outcome = {
        "kind": "cleanup_failed_candidate_regrade",
        "at": datetime.now(timezone.utc).isoformat(),
        "candidate_delta_sha256": sha256(delta_path.read_bytes()).hexdigest(),
        "original_status": summary.get("status"),
        "original_stop_reason": summary.get("stop_reason"),
        "grader_status": evaluation.get("status"),
        "solved": bool(evaluation.get("solved")) and not (missing or timed_out or infra_reason),
        "missing": missing,
        "timed_out": timed_out,
        "grader_infra_reason": infra_reason,
        "verdict": verdict,
        "candidate_error": candidate_error,
        "changed_files": delta_changes(delta) or list(evaluation.get("changed_paths") or []),
    }
    if not apply:
        return {"applied": False, **outcome}
    if infra_reason:
        raise ValueError("the regrade itself hit an infrastructure error: " + infra_reason)
    (run_dir / "regrade-cleanup.json").write_text(json.dumps(outcome, indent=2, default=str) + "\n",
                                                  encoding="utf-8")
    preserve(run_dir, "grader-result.json")
    preserve(run_dir, "summary.json")
    (run_dir / "grader-result.json").write_text(json.dumps(evaluation, indent=2, default=str), encoding="utf-8")
    summary["solved"] = outcome["solved"]
    summary["status"] = ("fail" if candidate_error == "execution_timeout" else evaluation.get("status"))
    summary["grader_status"] = evaluation.get("status")
    summary["grader_ids"] = {"f2p": len(f2p), "p2p": len(spec.baseline_passed),
                             "missing": missing, "reason": evaluation.get("reason")}
    summary["grader_candidate_error"] = candidate_error
    summary["missing_regrade"] = verdict if missing else None
    summary["timeout_regrade"] = verdict if timed_out else None
    summary["infra_reason"] = None
    summary["changed_files"] = outcome["changed_files"]
    summary["regrade"] = {key: outcome[key] for key in ("kind", "at", "candidate_delta_sha256",
                                                       "original_status", "original_stop_reason")}
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return {"applied": True, **outcome}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", type=Path, nargs="+")
    parser.add_argument("--apply", action="store_true", help="write the regraded summary and grader result")
    args = parser.parse_args()
    failed = 0
    for run_dir in args.run_dir:
        path = run_dir if run_dir.is_absolute() else ROOT / run_dir
        try:
            result = asyncio.run(regrade(path, apply=args.apply))
        except (ValueError, SystemExit) as exc:
            failed += 1
            result = {"error": str(exc)}
        print(json.dumps({"run_dir": str(path.relative_to(ROOT)), **result}, ensure_ascii=False))
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
