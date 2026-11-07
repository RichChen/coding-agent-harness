"""Thinking mode: payload, reasoning_content parsing, replay in the tool loop, persistence. Offline only."""
import json
from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError

from src.config.schema import HarnessConfig, LLMConfig
from src.llm.client import LLMClient, LLMResponse, OpenAICompatibleAdapter, ToolCall
from src.llm.stream import OpenAIStreamAggregator
from src.policy.budget import Budget
from src.policy.sandbox import Sandbox
from src.runtime.loop import AgentLoop
from src.runtime.recovery import reduce_events
from src.runtime.retry import RetryConfig
from src.runtime.session import RunSession, compatibility, runtime_fingerprint
from src.tools.files import ReadFileTool
from src.tools.registry import ToolRegistry
from tests.fixtures.mock_llm import MockLLMClient

TOOLS = [{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}]


# ---- config and payload ----

def test_schema_accepts_enabled_and_keeps_old_dumps():
    assert "reasoning_effort" not in LLMConfig(base_url="http://x").model_dump()
    assert LLMConfig(base_url="http://x", thinking="enabled").thinking == "enabled"
    config = LLMConfig(base_url="http://x", thinking="enabled", reasoning_effort="max")
    assert config.model_dump()["reasoning_effort"] == "max"
    with pytest.raises(ValidationError):
        LLMConfig(base_url="http://x", thinking="on")
    with pytest.raises(ValidationError):
        LLMConfig(base_url="http://x", thinking="disabled", reasoning_effort="high")
    with pytest.raises(ValidationError):
        LLMConfig(base_url="http://x", reasoning_effort="none")


def test_payload():
    adapter = OpenAICompatibleAdapter("https://example.invalid")
    common = dict(model="m", messages=[], tools=TOOLS, temperature=0.0, max_tokens=100)
    assert adapter.build_payload(**common, thinking="enabled")["thinking"] == {"type": "enabled"}
    assert "reasoning_effort" not in adapter.build_payload(**common, thinking="enabled")
    payload = adapter.build_payload(**common, thinking="enabled", reasoning_effort="high")
    assert payload["reasoning_effort"] == "high" and payload["tool_choice"] == "auto"
    assert adapter.build_payload(**common, thinking="disabled") == {
        "model": "m", "messages": [], "temperature": 0.0, "max_tokens": 100, "stream": False,
        "tools": TOOLS, "tool_choice": "auto", "thinking": {"type": "disabled"}}
    with pytest.raises(ValueError):
        adapter.build_payload(**common, thinking="maybe")
    with pytest.raises(ValueError):
        adapter.build_payload(**common, thinking="disabled", reasoning_effort="high")


def test_client_options():
    client = LLMClient("https://example.invalid", "k", "deepseek-flash", thinking="enabled", reasoning_effort="max")
    assert client.thinking == "enabled" and client.reasoning_effort == "max"
    with pytest.raises(ValueError):
        LLMClient("https://example.invalid", "k", "m", thinking="maybe")
    with pytest.raises(ValueError):
        LLMClient("https://example.invalid", "k", "m", reasoning_effort="high")


def test_runtime_fingerprint_changes_only_when_effort_is_set(tmp_path):
    from src.harness.factory import build_agent
    base = {"base_url": "http://127.0.0.1:9", "api_key": "k", "trust_env": False, "thinking": "enabled"}
    runtime = {"budget": {"max_model_calls": 5}}
    agent = build_agent(HarnessConfig(llm=base, runtime=runtime).model_dump(), tmp_path)
    assert agent.llm.thinking == "enabled" and agent.llm.reasoning_effort is None
    plain = runtime_fingerprint(agent)
    agent.llm.reasoning_effort = "max"
    assert runtime_fingerprint(agent) != plain
    agent2 = build_agent(HarnessConfig(llm=dict(base, reasoning_effort="max"), runtime=runtime).model_dump(), tmp_path)
    assert agent2.llm.reasoning_effort == "max"


# ---- parsing ----

def test_non_stream_parse_keeps_reasoning():
    client = LLMClient("https://example.invalid", "k", "m", thinking="enabled")
    data = {"choices": [{"message": {"content": "", "reasoning_content": "think", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
        "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 5, "completion_tokens": 9, "total_tokens": 14,
                                                 "completion_tokens_details": {"reasoning_tokens": 7}}}
    response = client._parse_response(data)
    assert response.reasoning_content == "think" and response.text is None
    assert response.usage["completion_tokens_details"] == {"reasoning_tokens": 7}
    data["choices"][0]["message"].pop("reasoning_content")
    assert client._parse_response(data).reasoning_content is None


def sse(chunks):
    return ("".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n").encode()


def test_stream_aggregates_reasoning_deltas():
    aggregator = OpenAIStreamAggregator()
    observations = aggregator.feed_bytes(sse([
        {"choices": [{"delta": {"role": "assistant", "reasoning_content": "Let me "}}]},
        {"choices": [{"delta": {"reasoning_content": "check."}}]},
        {"choices": [{"delta": {"content": "Done"}, "finish_reason": "stop"}]},
    ]))
    result = aggregator.finish()
    assert result.reasoning_content == "Let me check." and result.text == "Done"
    assert [o["event"] for o in observations] == ["message_delta"]


def test_stream_without_reasoning_reports_none():
    aggregator = OpenAIStreamAggregator()
    aggregator.feed_bytes(sse([{"choices": [{"delta": {"content": "x"}, "finish_reason": "stop"}]}]))
    assert aggregator.finish().reasoning_content is None


# ---- the tool loop against a provider fixture that enforces the replay rule ----

def provider(requests, *, streaming):
    """DeepSeek thinking mode: with tools, every earlier assistant message must carry reasoning_content."""
    usage = {"prompt_tokens": 30, "completion_tokens": 12, "total_tokens": 42, "prompt_cache_hit_tokens": 0,
             "prompt_cache_miss_tokens": 30, "completion_tokens_details": {"reasoning_tokens": 8}}

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        if body.get("thinking") == {"type": "enabled"} and body.get("tools"):
            if any(m["role"] == "assistant" and not m.get("reasoning_content") for m in body["messages"]):
                return httpx.Response(400, json={"error": {"message": "reasoning_content must be passed back"}})
        first = len(requests) == 1
        reasoning = "R1: read the file first." if first else "R2: answer."
        call = {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"a.txt"}'}}
        if streaming:
            delta = {"tool_calls": [{"index": 0, **call}]} if first else {"content": "done"}
            chunks = [{"choices": [{"delta": {"reasoning_content": reasoning}}]},
                      {"choices": [{"delta": delta, "finish_reason": "tool_calls" if first else "stop"}]},
                      {"choices": [], "usage": usage}]
            return httpx.Response(200, content=sse(chunks), headers={"Content-Type": "text/event-stream"})
        message = {"content": "", "reasoning_content": reasoning}
        message.update({"tool_calls": [call]} if first else {"content": "done"})
        return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": "tool_calls" if first else "stop"}],
                                         "usage": usage})
    return handle


@pytest.mark.parametrize("streaming", [False, True])
async def test_reasoning_is_replayed_in_the_tool_loop(tmp_path, streaming):
    (tmp_path / "a.txt").write_text("hello")
    requests = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider(requests, streaming=streaming))) as http:
        llm = LLMClient("https://example.invalid/v1", "fake", "deepseek-flash", thinking="enabled", http_client=http)
        tools = ToolRegistry()
        tools.register(ReadFileTool())
        loop = AgentLoop(llm, tools, Sandbox(tmp_path), Budget(max_steps=4), enabled_tools=["read_file"],
                         retry_config=RetryConfig(max_retries=0, backoff_base_seconds=0), use_llm_stream=streaming)
        result = await loop.run("read a.txt", task_id="t")
    assert result.success is True and result.final_answer == "done"
    assert len(requests) == 2
    replayed = [m for m in requests[1]["messages"] if m["role"] == "assistant"]
    assert replayed[0]["reasoning_content"] == "R1: read the file first."
    assert replayed[0]["tool_calls"][0]["id"] == "c1"


async def test_the_fixture_rejects_a_loop_that_drops_reasoning(tmp_path):
    requests = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider(requests, streaming=False))) as http:
        llm = LLMClient("https://example.invalid/v1", "fake", "deepseek-flash", thinking="enabled", http_client=http)
        messages = [{"role": "user", "content": "x"},
                    {"role": "assistant", "content": None, "tool_calls": [
                        {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
                    {"role": "tool", "tool_call_id": "c1", "content": "y"}]
        with pytest.raises(Exception, match="400"):
            await llm.chat(messages, TOOLS)


# ---- persistence ----

def response(reasoning=None, *, final=False):
    usage = {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30,
             "completion_tokens_details": {"reasoning_tokens": 6}}
    if final:
        return LLMResponse(text="done", usage=usage, reasoning_content=reasoning)
    return LLMResponse(text=None, tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "a.txt"})],
                       finish_reason="tool_calls", usage=usage, reasoning_content=reasoning)


async def run_session(tmp_path, responses):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True)
    (workspace / "a.txt").write_text("hello")
    tools = ToolRegistry()
    tools.register(ReadFileTool())
    llm = MockLLMClient(responses)
    loop = AgentLoop(llm, tools, Sandbox(workspace), Budget(max_steps=4), enabled_tools=["read_file"],
                     retry_config=RetryConfig(max_retries=0, backoff_base_seconds=0))
    expected = compatibility(workspace=workspace, code_id="fix10", config={}, backend="fixture")
    with RunSession.create(tmp_path / "session", task_id="fix10", expected=expected) as session:
        result = await session.run(loop, "read a.txt")
        events = session.store.read_events()
        resolved = []
        for event in events:
            payload = event["payload"]
            if "artifact_ref" in payload:
                payload = json.loads(session.artifacts.read(payload["artifact_ref"]))
            resolved.append(dict(event, payload=payload))
    return llm, result, events, resolved


async def test_without_reasoning_nothing_new_is_persisted(tmp_path):
    _, result, events, resolved = await run_session(tmp_path, [response(), response(final=True)])
    assert result.success
    saved = [e["payload"] for e in resolved if e["event_type"] == "state.saved"]
    assert all("reasoning_content" not in (s.get("response") or {}) for s in saved)
    assert all("reasoning_content" not in m for s in saved for m in s["messages"])


async def test_reasoning_is_persisted_and_recovered(tmp_path):
    llm, result, events, resolved = await run_session(tmp_path, [response("R1"), response("R2", final=True)])
    assert result.success
    assistant = [m for m in llm.calls[1]["messages"] if m["role"] == "assistant"]
    assert assistant[0]["reasoning_content"] == "R1"
    saved = [e["payload"] for e in resolved if e["event_type"] == "state.saved" and e["payload"].get("response")]
    assert saved[0]["response"]["reasoning_content"] == "R1"
    recovered = reduce_events(resolved).messages
    assert [m.get("reasoning_content") for m in recovered if m["role"] == "assistant"][0] == "R1"


async def test_large_reasoning_goes_to_an_artifact(tmp_path):
    big = "r" * 40_000
    _, result, events, resolved = await run_session(tmp_path, [response(big), response("R2", final=True)])
    assert result.success
    raw = [e for e in events if e["event_type"] == "state.saved" and "artifact_ref" in e["payload"]]
    assert raw, "a state.saved payload over 16 KiB is stored as an artifact"
    restored = [e["payload"] for e in resolved if e["event_type"] == "state.saved"]
    assert any(m.get("reasoning_content") == big for s in restored for m in s["messages"])


async def test_admission_counts_replayed_reasoning(tmp_path):
    _, _, _, plain = await run_session(tmp_path / "a", [response("x"), response(final=True)])
    _, _, _, think = await run_session(tmp_path / "b", [response("x" * 4000), response(final=True)])

    def second_step(resolved):
        return [e["payload"]["stats"]["admission_input_tokens"] for e in resolved
                if e["event_type"] == "context.prepared"][1]
    assert second_step(think) - second_step(plain) >= 3999


def deepseek_fixture(requests):
    """Enough of DeepSeek thinking mode for the smoke script: replay rule, streaming, max_tokens cut-off."""
    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        thinking = body.get("thinking", {}).get("type", "enabled") == "enabled"
        if thinking and body.get("tools") and any(m["role"] == "assistant" and "reasoning_content" not in m
                                                  for m in body["messages"]):
            return httpx.Response(400, json={"error": {"message": "reasoning_content missing; key sk-abcdefghijklmnopqrstu"}})
        tools = bool(body.get("tools"))
        answered = any(m["role"] == "tool" for m in body["messages"])
        reasoning = "Need the tool." if thinking else None
        completion = min(body["max_tokens"], 40)
        usage = {"prompt_tokens": 50, "completion_tokens": completion, "total_tokens": 50 + completion,
                 "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 50,
                 "completion_tokens_details": {"reasoning_tokens": completion // 2 if thinking else 0}}
        name = body["tools"][0]["function"]["name"] if tools else None
        call = {"id": "c1", "type": "function",
                "function": {"name": name, "arguments": '{"path":"note.txt"}' if name == "read_file" else "{}"}}
        finish = "length" if body["max_tokens"] < 40 else ("tool_calls" if tools and not answered else "stop")
        if body.get("stream"):
            chunks = ([{"choices": [{"delta": {"reasoning_content": reasoning}}]}] if reasoning else []) + [
                {"choices": [{"delta": {"tool_calls": [{"index": 0, **call}]} if finish == "tool_calls"
                              else {"content": "7341"}, "finish_reason": finish}]},
                {"choices": [], "usage": usage}]
            return httpx.Response(200, content=sse(chunks), headers={"Content-Type": "text/event-stream"})
        message = {"content": "" if finish == "tool_calls" else "2026-11-07"}
        if reasoning:
            message["reasoning_content"] = reasoning
        if finish == "tool_calls":
            message["tool_calls"] = [call]
        return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": finish}], "usage": usage,
                                         "model": "deepseek-flash", "id": "resp-1"})
    return handle


async def test_smoke_script_against_the_fixture():
    from scripts import fix10_thinking_smoke as smoke
    from scripts.run_swe_task import make_ledger
    requests = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(deepseek_fixture(requests))) as http:
        client = LLMClient("https://example.invalid/v1", "sk-test-secret-0123456789", smoke.MODEL,
                           max_tokens=smoke.MAX_TOKENS, thinking="enabled", http_client=http, audit_metadata=True)
        ledger = smoke.make_ledger_for_smoke(make_ledger(None).price)
        records = await smoke.run_smoke(client, ledger)
    cases = {r["case"]: r for r in records}
    assert len(requests) == ledger.calls_started <= smoke.MAX_REQUESTS
    assert cases["A_tools_thinking"]["response"]["reasoning_chars"] > 0
    assert cases["B_replay_with_reasoning"]["response"]["status"] == 200
    assert cases["C_replay_without_reasoning"]["response"]["status"] == 400
    assert cases["D_stream_tools_thinking"]["response"]["reasoning_chars"] > 0
    assert cases["E_small_max_tokens"]["response"]["finish_reason"] == "length"
    assert cases["G_thinking_disabled"]["response"]["reasoning_chars"] is None
    assert cases["H_agent_loop"]["success"] is True and cases["H_agent_loop"]["final_answer"] == "7341"
    text = json.dumps(records, default=str)
    assert "sk-test-secret" not in text and "sk-abcdefghijklmnopqrstu" not in text
    total = smoke.summary(records, ledger)
    assert Decimal(total["ledger_cny_with_held_reservations"]) <= smoke.MAX_CNY


async def test_summary_calls_run_without_thinking():
    from src.runtime.summary import LLMSummaryProvider
    seen = []

    def handle(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "s"}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        for thinking in ("enabled", "disabled", None):
            llm = LLMClient("https://example.invalid", "k", "m", thinking=thinking, http_client=http)
            await LLMSummaryProvider(llm).generate([{"role": "user", "content": "x"}], max_tokens=64)
    assert seen[0]["thinking"] == {"type": "disabled"}
    assert seen[1]["thinking"] == {"type": "disabled"}
    assert "thinking" not in seen[2]
