"""Offline walkthrough: real harness, scripted model, retained teaching artifacts.

Read docs/tutorials/HARNESS-START-HERE.zh-CN.md first. This is a teaching entry,
not a replacement implementation of the agent loop or the SWE-bench grader.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.harness.factory import build_agent, load_config
from src.llm.client import LLMResponse, ToolCall
from src.runtime.session import RunSession, compatibility
from src.tracing.logger import TraceLogger
from src.tracing.models import TraceRun
from tests.fixtures.mock_llm import MockLLMClient

OUT = ROOT / "runs" / "tutorial-harness"
BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
TASK = "Fix calc.py: add(2, 3) should return 5, but currently returns -1."


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def new_run(label):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / (datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{label}-{uuid4().hex[:6]}")
    path.mkdir()
    return path


def latest():
    pointer = OUT / "latest.txt"
    if not pointer.exists():
        raise SystemExit("No tutorial run yet. Run: python scripts/tutorial_walkthrough.py run")
    return Path(pointer.read_text(encoding="utf-8").strip())


class RecordingModel(MockLLMClient):
    """Only the model is scripted; snapshots are deep-copied before loop mutation."""

    def __init__(self, responses):
        super().__init__(responses)
        self.exchanges = []

    async def chat(self, messages, tools=None):
        record = {"round": len(self.exchanges) + 1, "request_messages": copy.deepcopy(messages),
                  "tool_schemas": copy.deepcopy(tools)}
        response = await super().chat(messages, tools)
        record["response"] = {"text": response.text, "tool_calls": [asdict(c) for c in response.tool_calls]}
        self.exchanges.append(record)
        names = [c.name for c in response.tool_calls]
        print(f"  model round {record['round']}: {names or '[final answer]'}")
        return response


def model_for(case):
    responses = [LLMResponse(tool_calls=[ToolCall("read-before", "read_file", {"path": "calc.py"})])]
    if case != "no-fix":
        responses.extend([
            LLMResponse(tool_calls=[ToolCall("edit", "search_replace", {
                "path": "calc.py", "search": "return a - b" if case == "fix" else "NOT PRESENT",
                "replace": "return a + b"})]),
            LLMResponse(tool_calls=[ToolCall("read-after", "read_file", {"path": "calc.py"})]),
        ])
    responses.append(LLMResponse(text="I am done. The teaching check will independently inspect the file."))
    return RecordingModel(responses)


async def run(case):
    directory = new_run(case)
    workspace = directory / "workspace"
    workspace.mkdir()
    (workspace / "calc.py").write_text(BUGGY, encoding="utf-8")
    (directory / "before.py").write_text(BUGGY, encoding="utf-8")
    (directory / "task.txt").write_text(TASK, encoding="utf-8")
    config = load_config(ROOT / "configs" / "tutorial.yaml")
    save(directory / "config.json", config)
    tracer = TraceLogger(str(directory / "traces"))
    tracer.open_run("tutorial")
    agent = build_agent(config, workspace, tracer=tracer)
    # Factory is exercised unchanged. Close the unused HTTP transport before replacing it.
    await agent.llm.aclose()
    model = model_for(case)
    agent.llm = model
    expected = compatibility(workspace=workspace, code_id="tutorial-walkthrough-v1", config=config,
                             backend={"kind": "trusted-local-file-tools", "platform": sys.platform})
    print(f"RUN_DIR={directory}\nCASE={case}\nMODEL=scripted (no network / no GPU)")
    try:
        with RunSession.create(directory / "session", task_id="tutorial", expected=expected,
                               timeout_seconds=120) as session:
            result = await session.run(agent, TASK)
        tracer.log_run(TraceRun(task_id="tutorial", config_name="offline-teaching", steps=result.steps,
                              success=result.success, final_answer=result.final_answer,
                              failure_reason=result.failure_reason.value, tool_failures=result.tool_failures))
    finally:
        tracer.close()
        await model.aclose()
    after = (workspace / "calc.py").read_text(encoding="utf-8")
    # A deliberately simple independent *teaching* check; never claimed as SWE-bench grading.
    check = after == FIXED
    save(directory / "requests.json", model.exchanges)
    (directory / "after.py").write_text(after, encoding="utf-8")
    with (directory / "trace.jsonl").open("w", encoding="utf-8") as stream:
        for step in result.steps:
            stream.write(json.dumps(step.to_dict(), ensure_ascii=False) + "\n")
    summary = {"case": case, "runtime_success": result.success, "teaching_check_passed": check,
               "teaching_check": "exact source comparison, not SWE-bench or hidden-test grading",
               "model_rounds": len(model.exchanges), "tool_calls": result.total_tool_invocations,
               "tool_failures": result.tool_failures, "failure_reason": result.failure_reason.value,
               "workspace": str(workspace), "session": str(directory / "session"),
               "model_budget": result.model_budget}
    save(directory / "summary.json", summary)
    lines = ["# Offline harness walkthrough", "", f"Case: `{case}`. Model replies are scripted.", "",
             "## Input", "```python", BUGGY, "```", "## Model/tool flow"]
    for step in result.steps:
        d = step.to_dict()
        lines += [f"\n### Step {d['step']} / {d['phase']}", "```json", json.dumps(d, ensure_ascii=False, indent=2), "```"]
    lines += ["\n## Final file", "```python", after, "```", "## Independent teaching check", "```json",
              json.dumps(summary, ensure_ascii=False, indent=2), "```"]
    (directory / "walkthrough.md").write_text("\n".join(lines), encoding="utf-8")
    (OUT / "latest.txt").write_text(str(directory), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(r"NEXT: .\.venv\Scripts\python.exe scripts/tutorial_walkthrough.py show flow")
    if not result.success or check != (case == "fix") or result.tool_failures != int(case == "bad-edit"):
        raise SystemExit("Unexpected tutorial outcome: inspect summary.json and trace.jsonl")


def replay():
    directory = latest()
    state = RunSession.replay(directory / "session")
    print("Read-only replay: no model client and no tool execution.")
    print(json.dumps({"last_seq": state.last_seq, "terminated": state.terminal is not None,
                      "operations": {k: (v["result"] or {}).get("status", "pending") for k, v in state.operations.items()},
                      "budget": state.budget}, ensure_ascii=False, indent=2))


async def crash(window):
    # Reuse the project's already-existing real process-death fixture.
    from tests.fixtures.recovery_worker import expected, make_loop
    directory = new_run("crash-" + window)
    workspace = directory / "workspace"
    workspace.mkdir()
    process = subprocess.run([sys.executable, "-m", "tests.fixtures.recovery_worker", str(directory / "session"),
                              str(workspace), window, "append"], cwd=ROOT, capture_output=True, text=True, timeout=40)
    if process.returncode != 71:
        raise SystemExit(f"Expected fixture exit 71, got {process.returncode}: {process.stderr}")
    value = workspace / "value.txt"
    before = value.read_text() if value.exists() else ""
    with RunSession.resume(directory / "session", expected=expected(workspace)) as session:
        result = await session.run(make_loop(workspace, "append", resumed=True), "perform fixture write")
    after = value.read_text() if value.exists() else ""
    report = {"window": window, "child_exit": process.returncode, "before_resume": before,
              "after_resume": after, "runtime_success": result.success, "termination_kind": result.termination_kind,
              "session": str(directory / "session")}
    save(directory / "crash.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"CRASH_DIR={directory}\nThe latest normal run pointer was not changed.")
    wanted = "" if window == "after_intent" else "x"
    if after != wanted or result.success != (window in {"before_intent", "after_result"}):
        raise SystemExit("Unexpected recovery result")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    p = commands.add_parser("run")
    p.add_argument("--case", choices=["fix", "no-fix", "bad-edit"], default="fix")
    p = commands.add_parser("show")
    p.add_argument("part", choices=["summary", "flow", "requests", "trace", "files"], default="summary", nargs="?")
    commands.add_parser("where")
    commands.add_parser("replay")
    p = commands.add_parser("turn")
    p.add_argument("number", type=int, help="1-based model round")
    p = commands.add_parser("crash")
    p.add_argument("--window", choices=["before_intent", "after_intent", "after_effect", "after_result"], default="after_effect")
    args = parser.parse_args()
    if args.action == "run":
        asyncio.run(run(args.case))
    elif args.action == "replay":
        replay()
    elif args.action == "crash":
        asyncio.run(crash(args.window))
    elif args.action == "turn":
        records = json.loads((latest() / "requests.json").read_text(encoding="utf-8"))
        if not 1 <= args.number <= len(records):
            raise SystemExit(f"Choose a round between 1 and {len(records)}")
        record = {k: v for k, v in records[args.number - 1].items() if k != "tool_schemas"}
        print(json.dumps(record, ensure_ascii=False, indent=2))
    elif args.action == "where":
        print(latest())
    else:
        directory = latest()
        names = {"summary": "summary.json", "flow": "walkthrough.md", "requests": "requests.json", "trace": "trace.jsonl"}
        if args.part == "files":
            print("BEFORE:\n" + (directory / "before.py").read_text(encoding="utf-8"))
            print("AFTER:\n" + (directory / "after.py").read_text(encoding="utf-8"))
        else:
            print((directory / names[args.part]).read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
