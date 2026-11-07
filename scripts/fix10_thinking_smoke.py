"""DeepSeek thinking-mode protocol smoke: at most 10 requests and 0.5 CNY, hard-capped by a CNY ledger.

Each case checks one documented rule (api-docs.deepseek.com/guides/thinking_mode and the chat completion
reference). The output keeps request shapes, usage and whitelisted provider metadata only; no key, header or
message text beyond short excerpts of the model's own output.

    fix10_thinking_smoke.py --out runs/fix10/e5-smoke [--config configs/deepseek.yaml]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.llm.client import LLMClient, LLMError  # noqa: E402
from src.llm.redact import redact_secrets  # noqa: E402
from src.policy.cny_budget import A5BudgetPlan, CnyBudgetLedger  # noqa: E402

MODEL = "deepseek-flash"
MAX_REQUESTS = 10
MAX_CNY = Decimal("0.500")
MAX_TOKENS = 2048
GET_DATE = {"type": "function", "function": {"name": "get_date", "description": "Get the current date",
                                             "parameters": {"type": "object", "properties": {}}}}
EXCERPT = 120


def make_ledger_for_smoke(price, state_path=None):
    plan = A5BudgetPlan(total_cny=MAX_CNY, build_cny=Decimal("0.000"), pilot_cny=MAX_CNY,
                        reserve_cny=Decimal("0.000"), build_calls=0, pilot_calls=MAX_REQUESTS)
    return CnyBudgetLedger(price, plan, state_path=state_path, settlement="provider_usage", time_of_day=True)


def shape(messages, tools, client, thinking, max_tokens, stream):
    return {"model": client.model, "thinking": thinking, "reasoning_effort": client.reasoning_effort,
            "max_tokens": max_tokens, "stream": stream, "tools": [t["function"]["name"] for t in tools or []],
            "messages": [{"role": m["role"], "has_reasoning_content": "reasoning_content" in m,
                          "tool_calls": len(m.get("tool_calls") or [])} for m in messages]}


def outcome(response):
    meta = response.provider_metadata or {}
    return {"status": 200, "finish_reason": response.finish_reason, "usage": response.usage,
            "reasoning_chars": None if response.reasoning_content is None else len(response.reasoning_content),
            "reasoning_excerpt": (response.reasoning_content or "")[:EXCERPT] or None,
            "content_excerpt": (response.text or "")[:EXCERPT] or None,
            "tool_calls": [c.name for c in response.tool_calls],
            "provider": {k: meta.get(k) for k in ("response_model", "system_fingerprint", "response_id",
                                                  "provider_request_id", "started_at", "finished_at")}}


async def call(ledger, client, records, case, messages, tools=None, *, thinking=None, max_tokens=MAX_TOKENS,
               stream=False):
    """One paid request: reserve at the miss price, call, settle at provider usage."""
    estimate = len(json.dumps({"messages": messages, "tools": tools or []}).encode()) + 512
    reservation = ledger.reserve(phase="pilot", operation_id=case, component="smoke", prompt_tokens=estimate,
                                 max_output_tokens=max_tokens)
    mode = client.thinking if thinking is None else thinking
    record = {"case": case, "request": shape(messages, tools, client, mode, max_tokens, stream)}
    response = None
    try:
        if stream:
            saved = (client.thinking, client.max_tokens)
            client.thinking, client.max_tokens = mode, max_tokens
            try:
                events = [e async for e in client.stream_chat(messages, tools)]
            finally:
                client.thinking, client.max_tokens = saved
            last = events[-1]
            if last.event != "llm_stream_done":
                raise LLMError(str(last.data.get("message")), status_code=last.data.get("status_code"))
            response = last.data["response"]
        else:
            response = await client.chat(messages, tools, max_tokens=max_tokens, thinking=thinking)
        record["response"] = outcome(response)
        record["settlement"] = ledger.settle(reservation.reservation_id, response.usage)
    except LLMError as exc:
        record["response"] = {"status": exc.status_code, "error": redact_secrets(str(exc), (client.api_key,))[:400]}
        record["settlement"] = ledger.settle(reservation.reservation_id, None)
    records.append(record)
    return response


def assistant_message(response, *, keep_reasoning=True):
    message = {"role": "assistant", "content": response.text or "",
               "tool_calls": [{"id": c.id, "type": "function",
                               "function": {"name": c.name, "arguments": c.raw_arguments or "{}"}}
                              for c in response.tool_calls]}
    if not message["tool_calls"]:
        message.pop("tool_calls")
    if keep_reasoning and response.reasoning_content is not None:
        message["reasoning_content"] = response.reasoning_content
    return message


def tool_results(response, text="2026-11-07"):
    return [{"role": "tool", "tool_call_id": c.id, "content": text} for c in response.tool_calls]


async def agent_loop_case(client, ledger, records):
    """The harness loop itself: read a file through the real tool, then answer (two requests)."""
    from src.policy.budget import Budget
    from src.policy.model_budget import ModelBudgetLedger
    from src.policy.sandbox import Sandbox
    from src.runtime.loop import AgentLoop
    from src.runtime.retry import RetryConfig
    from src.tools.files import ReadFileTool
    from src.tools.registry import ToolRegistry

    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "note.txt").write_text("The launch code is 7341.", encoding="utf-8")
        tools = ToolRegistry()
        tools.register(ReadFileTool())
        loop = AgentLoop(client, tools, Sandbox(Path(tmp)), Budget(max_steps=3), enabled_tools=["read_file"],
                         retry_config=RetryConfig(max_retries=0, backoff_base_seconds=0),
                         model_ledger=ModelBudgetLedger(max_model_calls=3), use_llm_stream=True)
        loop.retry.cny_ledger = ledger
        loop.retry.cny_component = "agent_loop"
        before = len(ledger.settlements)
        result = await loop.run("Read note.txt with the read_file tool and reply with the number only.",
                                task_id="fix10-thinking-smoke")
    records.append({"case": "H_agent_loop", "success": result.success, "final_answer": (result.final_answer or "")[:EXCERPT],
                    "failure_reason": getattr(result.failure_reason, "value", None),
                    "termination_kind": result.termination_kind,
                    "attempts": [{"success": a.success, "error_kind": a.error_kind, "usage": a.usage}
                                 for a in loop.attempt_sink.attempts],
                    "settlements": ledger.settlements[before:]})


async def run_smoke(client: LLMClient, ledger: CnyBudgetLedger) -> list[dict]:
    records: list[dict] = []
    ask = [{"role": "user", "content": "What is today's date? Use the get_date tool."}]
    # A: thinking + tools returns reasoning_content next to the tool call
    first = await call(ledger, client, records, "A_tools_thinking", ask, [GET_DATE])
    if first is not None and first.tool_calls:
        turn = ask + [assistant_message(first)] + tool_results(first)
        # B: the documented replay (reasoning_content passed back) is accepted
        await call(ledger, client, records, "B_replay_with_reasoning", turn, [GET_DATE])
        # C: the same request without reasoning_content is rejected with 400
        bad = ask + [assistant_message(first, keep_reasoning=False)] + tool_results(first)
        await call(ledger, client, records, "C_replay_without_reasoning", bad, [GET_DATE])
        # F: an extra plain assistant turn without reasoning_content (what a fixture summary would insert)
        summary_like = turn + [{"role": "assistant", "content": "Summary: the date tool returned 2026-11-07."},
                               {"role": "user", "content": "Reply with the date once more."}]
        await call(ledger, client, records, "F_plain_assistant_without_reasoning", summary_like, [GET_DATE])
    # D: streaming carries reasoning in delta.reasoning_content
    await call(ledger, client, records, "D_stream_tools_thinking", ask, [GET_DATE], stream=True)
    # E: max_tokens bounds reasoning + answer together
    riddle = [{"role": "user", "content": "How many prime numbers are there between 100 and 200? Think it through."}]
    await call(ledger, client, records, "E_small_max_tokens", riddle, max_tokens=32)
    # G: thinking disabled returns no reasoning_content
    await call(ledger, client, records, "G_thinking_disabled", ask, [GET_DATE], thinking="disabled")
    # H: the harness loop, two requests
    await agent_loop_case(client, ledger, records)
    return records


def summary(records, ledger):
    paid = [s for r in records for s in ([r["settlement"]] if "settlement" in r else r.get("settlements", []))]
    return {"requests": ledger.calls_started, "max_requests": MAX_REQUESTS,
            "ledger_cny_with_held_reservations": str(ledger.cost_committed),
            "provider_usage_cny": str(sum((Decimal(s["cost_cny"]) for s in paid if s.get("status") == "provider_usage"),
                                          Decimal(0))),
            "failed_requests_holding_reservation": sum(1 for s in paid if s.get("status") != "provider_usage"),
            "price_periods": sorted({s.get("price_period") for s in paid if s.get("price_period")})}


async def main_async(args):
    from scripts.run_swe_task import make_ledger
    from src.harness.factory import load_config

    llm = load_config(ROOT / args.config)["llm"]
    if not llm.get("api_key") or llm["api_key"].startswith("${"):
        raise SystemExit("no API key in the config environment")
    args.out.mkdir(parents=True, exist_ok=True)
    ledger = make_ledger_for_smoke(make_ledger(None).price, args.out / "cny-ledger.json")
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0), trust_env=llm.get("trust_env", True)) as http:
        client = LLMClient(llm["base_url"], llm["api_key"], MODEL, temperature=0.0, max_tokens=MAX_TOKENS,
                           thinking="enabled", http_client=http, audit_metadata=True)
        records = await run_smoke(client, ledger)
    result = {"run_at": datetime.now(timezone.utc).isoformat(), "model": MODEL, "base_url": llm["base_url"],
              "summary": summary(records, ledger), "cases": records}
    text = json.dumps(result, indent=2, ensure_ascii=False, default=str)
    if llm["api_key"] in text:
        raise SystemExit("refusing to write output that contains the key")
    (args.out / "smoke.json").write_text(text + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="configs/deepseek.yaml")
    parser.add_argument("--out", type=Path, default=ROOT / "runs/fix10/e5-smoke")
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
