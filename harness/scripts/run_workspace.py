#!/usr/bin/env python3
"""Normal workspace-v1 entry, plus an explicitly scripted offline Sphinx demo.

The demo makes an artificial source fault and scripted correction through the
same registered tools, AgentLoop, session, public checks and completion gate.
It is not autonomous model repair or evidence of repair-rate improvements.
"""
from __future__ import annotations

import argparse
import asyncio
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.harness.factory import build_agent, load_config
from src.llm.client import LLMResponse, ToolCall
from src.runtime.session import RunSession, compatibility, source_fingerprint, plain
from src.runtime.workspace_io import inspect_workspace_session, export_workspace_session
from src.tracing.store import StoreError


def scripted_demo_acceptance(directory, exported):
    """Require the durable fail/feedback/fix/pass chain, not just a green test."""
    from src.tracing.store import EventStore
    state = RunSession.replay(directory)
    workspace = state.workspace
    records = [p["record"] for p in workspace.get("records", {}).values()]
    baselines = [r for r in records if r["trigger"]=="baseline"]
    candidates = [r for r in records if r["trigger"]!="baseline"]
    failed, passed = (candidates if len(candidates)==2 else ({}, {}))
    with EventStore(directory, readonly=True) as events:
        kinds = [event["event_type"] for event in events.read_events()]
    exported = exported or {}
    baseline = workspace.get("baseline") or {}
    terminal = state.terminal or {}
    report = terminal.get("workspace_v1") or {}
    checks = dict(
        baseline_pass=len(baselines)==1 and baselines[0]["status"]=="passed",
        failed_then_passed=len(candidates)==2 and failed.get("status")=="behavior_failed" and passed.get("status")=="passed",
        different_candidates=bool(failed and passed and failed["candidate_id"]!=passed["candidate_id"]),
        single_host_feedback=bool(failed and workspace.get("feedback")==[failed["record_id"]]
            and any(m.get("role")=="user" and "[Harness public validation observation]" in m.get("content", "")
                    for m in state.messages)),
        frozen_binding=bool(baselines and baseline.get("record_id")==baselines[0]["record_id"]
            and baseline.get("check_id")==failed.get("check_id")==passed.get("check_id")
            and failed.get("environment_id")==passed.get("environment_id")),
        final_export=bool(passed and exported.get("final") and exported.get("candidate_id")==passed["candidate_id"]
            and exported.get("record_id")==passed["record_id"]
            and report.get("development_validation", {}).get("record_id")==passed["record_id"]),
        no_pending=not state.pending_model and not workspace.get("pending_validation")
            and all(op["result"] is not None for op in state.operations.values()),
        single_finish=bool(kinds and kinds[-1]=="run.finished" and kinds.count("run.finished")==1),
        six_scripted_calls=bool(terminal.get("model_budget", {}).get("calls_started")==6),
        cleanup=bool(report.get("run", {}).get("cleanup_ok")),
    )
    return dict(accepted=all(checks.values()), checks=checks)


class ScriptedSphinxDemo:
    """Finite host-authored replies; never contacts a provider or reads a grader."""
    model, base_url, temperature, max_tokens, thinking = "scripted-offline-demo", "offline", 0, 256, None

    def __init__(self):
        self.calls = 0
        self.original = None
        self.feedback_observed = False

    async def chat(self, messages, tools):
        step = self.calls
        self.calls += 1
        calls = []
        text = None
        path = "sphinx/util/__init__.py"
        fault = "    return '__p3_scripted_mechanism_fault__'"
        if step == 0:
            calls = [ToolCall("demo-retrieve", "retrieve_code", dict(query="display_chunk", k=3))]
        elif step == 1:
            calls = [ToolCall("demo-read", "read_file", dict(path=path, offset=640, limit=8))]
        elif step == 2:
            observed = next(m["content"] for m in reversed(messages) if m.get("tool_call_id") == "demo-read")
            lines = [match.group(1) for line in observed.splitlines()
                     if (match := re.match(r"\d+\|(.*)", line))]
            start = next((i for i,line in enumerate(lines) if line.startswith("    return ")), None)
            if start is None:
                raise RuntimeError("scripted demo could not locate the public display_chunk return line")
            body = lines[start:]
            end = next((i for i,line in enumerate(body) if not line.strip()), len(body))
            self.original = "\n".join(body[:end])
            ast.parse("def probe():\n"+self.original)
            calls = [ToolCall("demo-fault", "search_replace", dict(path=path, search=self.original, replace=fault))]
        elif step == 3:
            text = "Scripted mechanism probe requests completion of the artificial fault candidate."
        elif step == 4:
            if not any(m.get("role")=="user" and "[Harness public validation observation]" in m.get("content", "")
                       for m in messages):
                raise RuntimeError("scripted demo requires real host failure feedback before correction")
            self.feedback_observed = True
            corrected = self.original + "\n    # P3 scripted mechanism probe: public regression behavior restored."
            calls = [ToolCall("demo-correction", "search_replace", dict(path=path, search=fault, replace=corrected))]
        elif step == 5:
            text = "Scripted correction submitted for frozen public regression checks; target issue remains unevaluated."
        else:
            raise RuntimeError("scripted demo exceeded its finite reply sequence")
        return LLMResponse(text=text, tool_calls=calls,
            usage=dict(prompt_tokens=1, completion_tokens=1, total_tokens=2, provenance="provider"))


async def run_entry(output_dir, *, config_path, task, scripted=False):
    output_dir = Path(output_dir).absolute()
    output_dir.mkdir(parents=True, exist_ok=False)
    anchor = output_dir / "workspace-anchor"
    anchor.mkdir()
    config = load_config(config_path)
    if not (config.get("workspace") or {}).get("enabled"):
        raise ValueError("run_workspace requires explicit workspace-v1 configuration")
    model = ScriptedSphinxDemo() if scripted else None
    loop = build_agent(config, anchor, llm_client=model)
    expected = compatibility(workspace=anchor, code_id=source_fingerprint(ROOT), config=config,
                             backend=loop.execution_backend.capabilities)
    with RunSession.create(output_dir / "session", task_id="workspace-scripted-demo" if scripted else "workspace-task",
            expected=expected, timeout_seconds=config["runtime"]["total_timeout_seconds"],
            artifact_item_limit_bytes=8*1024**2) as session:
        try:
            result = await session.run(loop, task)
        finally:
            if model is None:
                await loop.llm.aclose()
    summary = dict(scripted=scripted, autonomous_model_repair=False if scripted else None,
                   result=plain(result), exported=None)
    if scripted:
        summary["scripted_sequence_complete"] = model.calls == 6 and model.feedback_observed
    try:
        summary["exported"] = export_workspace_session(output_dir / "session", output_dir / "delivery")
    except (StoreError, OSError) as exc:
        summary["export_error"] = str(exc)
    if scripted:
        summary["scripted_acceptance"] = scripted_demo_acceptance(output_dir / "session", summary["exported"])
        summary["scripted_sequence_complete"] = summary["scripted_sequence_complete"] and summary["scripted_acceptance"]["accepted"]
    (output_dir / "result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/workspace/sphinx-bm25.yaml")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--task")
    parser.add_argument("--scripted-demo", action="store_true")
    parser.add_argument("--inspect", type=Path, help="Read committed evidence without expiry/resume or container access")
    parser.add_argument("--export", type=Path, help="Read-only export; requires a new --output-dir")
    args = parser.parse_args()
    try:
        if args.inspect:
            value = inspect_workspace_session(args.inspect)
        elif args.export:
            if args.output_dir is None: parser.error("--export needs --output-dir")
            value = export_workspace_session(args.export, args.output_dir)
        else:
            if args.output_dir is None: parser.error("a new --output-dir is required")
            if not args.scripted_demo and not args.task: parser.error("--task or --scripted-demo is required")
            value = asyncio.run(run_entry(args.output_dir, config_path=args.config,
                task=args.task or "Scripted public Sphinx mechanism demo; no autonomous repair or solved claim.",
                scripted=args.scripted_demo))
        print(json.dumps(value, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(json.dumps(dict(status="failed", reason=type(exc).__name__, detail=str(exc))), file=sys.stderr)
        return 1
    if "result" in value:
        workspace = value["result"].get("workspace_v1") or {}
        if (value.get("export_error") or workspace.get("development_validation", {}).get("status") != "passed"
                or value.get("scripted") and not value.get("scripted_sequence_complete")):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
