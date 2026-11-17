"""Recover a completed, graded FIX-13 run after JSONL postprocessing failed.

Reads the original journal, candidate, grader receipt and settled ledger. It sends
no provider request and runs no grader. Recovery is restricted to a completed
agent and a complete, ordinary grader verdict. Unrecoverable timing metadata is
left null and identified explicitly.
"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts import run_swe_task as runner  # noqa: E402
from scripts.rebench import run_fix13 as launch  # noqa: E402
from src.eval.candidate_error import missing_count, timed_out_with_reason  # noqa: E402
from src.eval.tool_errors import harness_tool_errors, tool_schemas  # noqa: E402
from src.runtime.session import RunSession  # noqa: E402


def recover(task_id, group, repeat):
    slots, tasks = launch.load_inputs()
    slot = next(s for s in slots if s["task"] == task_id and s["group"] == group and s["repeat"] == repeat)
    output = launch.legacy.slot_dir(launch.OUT, slot)
    if (output / "summary.json").exists():
        raise ValueError("a summary already exists; preserve it")
    process = runner.load_json(output / "child-process.json")
    if process.get("finished") is not True:
        raise ValueError("the child process must be confirmed finished")
    ledger = runner.load_json(output / "cny-ledger.json")
    settlements = ledger.get("settlements") or []
    if (ledger.get("outstanding") != 0 or len(settlements) != ledger["calls_started"]
            or any(s["status"] != "provider_usage" for s in settlements)
            or sum((Decimal(s["cost_cny"]) for s in settlements), Decimal(0)) != Decimal(ledger["cost_committed"])):
        raise ValueError("the entire billed ledger must be settled and reconcilable")
    state = RunSession.replay(output / "session")
    terminal = state.terminal
    if not terminal or terminal.get("success") is not True or terminal.get("error_type"):
        raise ValueError("this recovery supports only a normally completed agent")
    events = runner.load_run_events(output / "events.jsonl")
    if events[-1]["event_type"] != "run.finished" or events[0]["task_id"] != task_id:
        raise ValueError("journal task identity or terminal boundary differs")
    evaluation = runner.load_json(output / "grader-result.json")
    if (evaluation.get("reason") or evaluation.get("receipt_complete") is not True
            or missing_count(evaluation) or timed_out_with_reason(evaluation)
            or evaluation.get("status") not in ("pass", "fail")):
        raise ValueError("a complete ordinary grader verdict is required")
    task, profile = tasks[task_id], launch.PROFILES[group]
    config = runner.load_config(profile)
    spec = runner.grader_spec(task)
    f2p = [n for n in spec.expected_nodeids if n not in spec.baseline_passed]
    delta = runner.load_json(output / "candidate-delta.json")
    stats = runner.summarize_events(events, terminal, None, output / "session/artifacts")
    lost = runner.delta_lost(stats, delta)
    errors = harness_tool_errors(events, tool_schemas())
    image, clean = runner.resolve_image(task, runner.load_json(ROOT / task["receipt"]), mock=False, legacy_image=False)
    if not clean or clean.get("answer_isolation_pass") is not True:
        raise ValueError("the original clean image identity must remain valid")
    attempts = terminal.get("llm_attempts") or []
    if len(attempts) != ledger["calls_started"]:
        raise ValueError("journal and billed request counts differ")
    summary = {
        "task_id": task_id, "variant": launch.VARIANTS[group], "mock": False, "profile": profile.stem,
        "model": {"name": config["llm"]["model"], "base_url_host": urlparse(config["llm"]["base_url"]).hostname},
        "issue_sha256": sha256(runner.issue_text(runner.load_json(Path(task["public_task"]))).encode()).hexdigest(),
        "prompt_sha256": sha256(runner.build_prompt(task, None).encode()).hexdigest(),
        "solved": False if lost else bool(evaluation["solved"]),
        "status": "delta_lost" if lost else evaluation["status"], "grader_status": evaluation["status"],
        "grader_ids": {"f2p": len(f2p), "p2p": len(spec.baseline_passed), "missing": 0, "reason": None},
        "grader_candidate_error": None, "missing_regrade": None, "timeout_regrade": None,
        "changed_files": runner.delta_changes(delta) or list(evaluation.get("changed_paths") or []),
        "first_edit_step": stats["first_edit_step"], "model_calls": len(attempts), "tool_calls": stats["tool_calls"],
        "harness_tool_errors": len(errors), "harness_tool_error_details": errors[:20],
        "peak_context": runner._peak_context(terminal, events),
        "timing": {"agent_wall_seconds": None, "session_record_seconds": None,
                   "recovered_loop_elapsed_seconds": terminal["total_elapsed_ms"] / 1000},
        "cost_cny": ledger["cost_committed"], "stop_reason": stats["stop_reason"], "run_error": None, "infra_reason": None,
        "docker_retries": [dict(stage="grader", **r) for r in evaluation.get("docker_retries", [])],
        "snapshot_error": None, "export_skipped_files": None, "answer_isolation_pass": True,
        "clean_image_digest": image, "memory": None,
        "git": {"coding-harness": runner.git_state(ROOT), "coding-agent": runner.git_state(ROOT.parent / "coding-agent")},
        "recovery": {"reason": "valid Unicode separator split by str.splitlines after grading",
                     "provider_replay": False, "grader_replay": False,
                     "metadata_unavailable": ["timing.agent_wall_seconds", "timing.session_record_seconds",
                                              "docker_retries.run", "export_skipped_files"],
                     "evidence_sha256": {name: sha256((output / name).read_bytes()).hexdigest()
                                         for name in ("events.jsonl", "candidate-delta.json", "grader-result.json", "cny-ledger.json")}},
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task")
    parser.add_argument("group", choices=launch.GROUPS)
    parser.add_argument("repeat", type=int)
    args = parser.parse_args()
    summary = recover(args.task, args.group, args.repeat)
    print(json.dumps({k: summary[k] for k in ("task_id", "variant", "status", "solved", "cost_cny", "model_calls", "recovery")}))


if __name__ == "__main__":
    main()
