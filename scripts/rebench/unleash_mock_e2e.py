"""Zero-model end-to-end check of the three unleash pilot profiles on one prepared task (design section 4.1, item 4).

Each group runs through run_swe_task.run_task with its profile and the launcher's CNY ledger. The agent keeps the
LLMClient the factory builds from the profile; only its HTTP transport is replaced by a scripted SSE server, so
the request bodies are exactly what a real run would send, and nothing leaves the machine. Script per run:

    1  list_dir "."                                   (U+T: with reasoning text)
    2  the gold patch through the agent's file tools  (U+T: empty reasoning, as seen with tools in the smoke test)
    3  finish                                         (U+T: with reasoning text)

Checks per group:
    thinking   every request body carries the group's thinking options (A, U: disabled; U+T: enabled, high)
               and the profile's max_tokens; reasoning_content is replayed only in U+T
    injected   no user message besides the task prompt; no message text about call limits, budgets or remaining
               counts; system prompt and task prompt identical across groups
    telemetry  every settled request has the section 5.6 fields and the options it was sent with; every prepared
               context has request telemetry; the launcher's stop rules find nothing
    graded     the gold patch is exported and graded solved

    unleash_mock_e2e.py [--manifest configs/rebench/unleash-pilot-tasks.yaml] [--task ID]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.rebench.mock_e2e import gold_tool_calls  # noqa: E402
from scripts.rebench.run_unleash import (  # noqa: E402
    GROUPS, MANIFEST, PROFILES, THINKING, VARIANTS, inspect_run, ledger_options, settled_records)
from scripts.run_a5_main import context_payloads  # noqa: E402

OUT = ROOT / "runs" / "unleash-mock-e2e"
RESULT = ROOT / "docs" / "analysis" / "fix12" / "unleash-mock-e2e.json"
LIMIT_TEXT = re.compile(r"remaining|budget|model calls|calls? left|call limit|max_model_calls|"
                        r"\b(?:40|100)\s*(?:calls|requests|steps)\b|剩余|预算|调用上限", re.I)
REASONING = {1: "Look at the repository layout first.", 2: "", 3: "The patch is applied."}


class ScriptedServer:
    """Answers chat completions as SSE in the DeepSeek format and keeps every request body."""

    def __init__(self, group: str, gold_calls):
        self.group, self.gold_calls, self.bodies = group, gold_calls, []

    def usage(self, body: bytes, reasoning: str) -> dict:
        prompt = max(1, len(body) // 4)
        hit = min(prompt, self.bodies[-2]["_prompt"]) if len(self.bodies) > 1 else 0
        self.bodies[-1]["_prompt"] = prompt
        usage = {"prompt_tokens": prompt, "completion_tokens": 20, "total_tokens": prompt + 20,
                 "prompt_cache_hit_tokens": hit, "prompt_cache_miss_tokens": prompt - hit}
        if self.group == "UT":
            reasoning_tokens = len(reasoning.split())
            usage["completion_tokens"] += reasoning_tokens
            usage["total_tokens"] += reasoning_tokens
            usage["completion_tokens_details"] = {"reasoning_tokens": reasoning_tokens}
        return usage

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        turn = len(self.bodies)
        reasoning = REASONING.get(turn, "") if self.group == "UT" else None
        delta = {"role": "assistant"}
        if reasoning is not None:
            delta["reasoning_content"] = reasoning
        calls = []
        if turn == 1:
            calls = [("mock-0", "list_dir", {"path": "."})]
        elif turn == 2:
            calls = [(c.id, c.name, c.arguments) for c in self.gold_calls]
        else:
            delta["content"] = "Done."
        chunks = [{"choices": [{"index": 0, "delta": delta, "finish_reason": None}]}]
        for index, (cid, name, arguments) in enumerate(calls):
            chunks.append({"choices": [{"index": 0, "finish_reason": None, "delta": {"tool_calls": [
                {"index": index, "id": cid, "type": "function",
                 "function": {"name": name, "arguments": json.dumps(arguments)}}]}}]})
        chunks.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if calls else "stop"}]})
        chunks.append({"choices": [], "usage": self.usage(request.content, reasoning or "")})
        common = {"id": f"mock-{turn}", "object": "chat.completion.chunk", "model": "deepseek-flash-mock",
                  "system_fingerprint": "fp_mock"}
        text = "".join("data: " + json.dumps({**common, **chunk}) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=text.encode("utf-8"))


def scripted_llm(group: str, task: dict, config: dict):
    """The LLMClient the factory builds from the profile, with the scripted server as its transport."""
    from src.harness.factory import build_agent

    server = ScriptedServer(group, gold_tool_calls(task))
    with tempfile.TemporaryDirectory() as tmp:
        llm = build_agent(json.loads(json.dumps(config)), Path(tmp)).llm
    llm._http = httpx.AsyncClient(transport=httpx.MockTransport(server.handle))
    llm._owns_http_client = True
    return llm, server


def messages_text(message: dict) -> str:
    content = message.get("content")
    return content if isinstance(content, str) else json.dumps(content or "")


def check_group(group: str, config: dict, bodies: list[dict], run_dir: Path, summary: dict) -> dict:
    thinking, effort = THINKING[group]
    problems = []
    for n, body in enumerate(bodies, 1):
        sent = (body.get("thinking") or {}).get("type")
        if sent != thinking or body.get("reasoning_effort") != effort:
            problems.append(f"request {n}: thinking={sent} effort={body.get('reasoning_effort')}")
        if body.get("max_tokens") != config["llm"]["max_tokens"]:
            problems.append(f"request {n}: max_tokens {body.get('max_tokens')}")
        replayed = [m for m in body["messages"] if m.get("role") == "assistant" and "reasoning_content" in m]
        if group == "UT" and n > 1 and not replayed:
            problems.append(f"request {n}: reasoning_content not replayed")
        if group != "UT" and replayed:
            problems.append(f"request {n}: reasoning_content sent with thinking disabled")
    injected = []
    for n, body in enumerate(bodies, 1):
        users = [m for m in body["messages"] if m.get("role") == "user"]
        if len(users) != 1:
            injected.append(f"request {n}: {len(users)} user messages")
        for index, message in enumerate(body["messages"]):
            if message.get("role") in ("system", "user", "tool"):
                hit = LIMIT_TEXT.search(messages_text(message))
                if hit:
                    injected.append(f"request {n} message {index} ({message['role']}): {hit.group(0)!r}")
    records = settled_records(run_dir)
    payloads = context_payloads(run_dir) or []
    prepared_missing = [i for i, p in enumerate(payloads)
                        if not isinstance((p.get("stats") or {}).get("request_telemetry"), dict)]
    inspection = inspect_run(run_dir, group)
    admission = {(r.get("telemetry") or {}).get("admission_method") for r in records}
    return {"requests": len(bodies), "settled": len(records), "context_prepared": len(payloads),
            "thinking_problems": problems, "injected_messages": injected,
            "telemetry_problems": inspection["problems"]
            + ([f"context.prepared without request telemetry: {prepared_missing}"] if prepared_missing else [])
            + ([] if len(records) == len(bodies) else [f"{len(records)} settled for {len(bodies)} requests"]),
            "admission_methods": sorted(m for m in admission if m),
            "request_options": [(r.get("telemetry") or {}).get("request") for r in records],
            "reasoning_tokens": [(r.get("telemetry") or {}).get("reasoning_tokens") for r in records],
            "first_messages": [m for m in bodies[0]["messages"] if m.get("role") in ("system", "user")] if bodies else [],
            "status": summary.get("status"), "solved": summary.get("solved"), "changed_files": summary.get("changed_files"),
            "cost_cny": summary.get("cost_cny"), "stop_reason": summary.get("stop_reason"),
            "ledger": json.loads((run_dir / "cny-ledger.json").read_text(encoding="utf-8")).get("hard_caps"),
            "ledger_settlement": json.loads((run_dir / "cny-ledger.json").read_text(encoding="utf-8")).get("settlement")}


async def run_all(task: dict) -> dict:
    from scripts.run_swe_task import run_task
    from src.harness.factory import load_config

    results = {}
    for group in GROUPS:
        config = load_config(PROFILES[group])
        llm, server = scripted_llm(group, task, config)
        output = OUT / task["id"] / group
        if output.exists():
            shutil.rmtree(output)
        summary = await run_task(task, PROFILES[group], output, mock=True, variant=VARIANTS[group], mock_llm=llm,
                                 ledger_options=ledger_options(config))
        bodies = [{k: v for k, v in b.items() if k != "_prompt"} for b in server.bodies]
        results[group] = check_group(group, config, bodies, output, summary)
        print(group, summary.get("status"), summary.get("solved"), len(bodies), flush=True)
    first = {g: results[g].pop("first_messages") for g in GROUPS}
    same_prompt = all(first[g] == first["A"] for g in GROUPS)
    ok = same_prompt and all(not r["thinking_problems"] and not r["injected_messages"] and not r["telemetry_problems"]
                             and r["solved"] is True for r in results.values())
    return {"schema": "unleash-pilot.mock-e2e.v1", "task": task["id"], "model_calls": 0,
            "system_and_task_prompt_identical": same_prompt, "ok": ok, "groups": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--task", default=None)
    parser.add_argument("--out", type=Path, default=RESULT)
    args = parser.parse_args()
    os.environ["LLM_API_KEY"] = "mock-placeholder"
    tasks = yaml.safe_load(args.manifest.read_text(encoding="utf-8"))["tasks"]
    task = next(t for t in tasks if t["id"] == args.task) if args.task else tasks[0]
    result = asyncio.run(run_all(task))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"task": result["task"], "ok": result["ok"]}))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
