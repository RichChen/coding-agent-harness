"""Offline wire tests. These never read a real key or call a provider."""
import json

import httpx
import pytest
from pydantic import ValidationError

from src.config.schema import LLMConfig
from src.llm.client import LLMClient, normalize_usage


@pytest.mark.parametrize("streaming", [False, True])
async def test_nonthinking_tools_and_usage_survive_both_transports(streaming):
    requests = []
    usage = {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25,
             "prompt_cache_hit_tokens": 12, "prompt_cache_miss_tokens": 8,
             "completion_tokens_details": {"reasoning_tokens": 0}}
    call = {"id": "call1", "type": "function",
            "function": {"name": "read_file", "arguments": '{"path":"a.py"}'}}

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["thinking"] == {"type": "disabled"}
        assert body["tool_choice"] == "auto"
        if len(requests) == 2:
            assert body["messages"][-1]["tool_call_id"] == "call1"
        if streaming:
            delta = {"tool_calls": [{"index": 0, **call}]} if len(requests) == 1 else {"content": "done"}
            chunks = [{"choices": [{"delta": delta, "finish_reason": "tool_calls" if len(requests) == 1 else "stop"}]},
                      {"choices": [], "usage": usage}]
            sse = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"
            return httpx.Response(200, text=sse, headers={"Content-Type": "text/event-stream"})
        message = {"content": None, "tool_calls": [call]} if len(requests) == 1 else {"content": "done"}
        return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": "stop"}], "usage": usage})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = LLMClient("https://example.invalid/v1", "fake", "deepseek-flash",
                           thinking="disabled", http_client=http)
        messages = [{"role": "user", "content": "read a.py"}]
        tools = [{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}]
        for turn in range(2):
            if streaming:
                events = [e async for e in client.stream_chat(messages, tools)]
                assert events[-1].event == "llm_stream_done"
                result = events[-1].data["response"]
            else:
                result = await client.chat(messages, tools)
            assert result.usage == {**usage, "provenance": "provider"}
            if turn == 0:
                assert result.tool_calls[0].arguments == {"path": "a.py"}
                messages += [{"role": "assistant", "content": None, "tool_calls": [call]},
                             {"role": "tool", "tool_call_id": "call1", "content": "source"}]
            else:
                assert result.text == "done"


def test_thinking_is_opt_in_and_unknown_modes_are_rejected():
    config = LLMConfig(base_url="https://example.invalid")
    assert config.thinking is None
    with pytest.raises(ValidationError):
        LLMConfig(base_url="https://example.invalid", thinking="auto")
    with pytest.raises(ValueError, match="thinking"):
        LLMClient("https://example.invalid", "fake", "model", thinking="auto")


def test_usage_does_not_invent_cache_counts_or_keep_unrelated_fields():
    usage = normalize_usage({"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3,
                             "prompt_cache_hit_tokens": True, "prompt_cache_miss_tokens": -1,
                             "completion_tokens_details": {"reasoning_tokens": "bad"}, "secret": "ignored"})
    assert usage == {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3, "provenance": "provider"}


def test_demo_usage_reports_price_interval_not_an_invoice():
    from src.eval.deepseek_demo import usage_report
    attempt = {"usage": {"provenance": "provider", "prompt_tokens": 100,
               "completion_tokens": 10, "total_tokens": 110,
               "prompt_cache_hit_tokens": 80, "prompt_cache_miss_tokens": 20}}
    report = usage_report([attempt, attempt], "deepseek-flash")
    assert report["provider_totals"]["total_tokens"] == 220
    assert report["cost_usd"] is None
    assert report["estimated_cost_usd_range"] == pytest.approx([0.00001848, 0.00003696])
    report = usage_report([attempt, {"usage": {"provenance": "unknown"}}], "deepseek-flash")
    assert report["provider_totals"]["total_tokens"] is None
    assert report["known_subtotals"]["total_tokens"] == 110
    assert report["unknown_usage_attempts"] == 1
    assert report["estimated_cost_usd_range"] is None
    assert usage_report([], "deepseek-flash")["estimated_cost_usd_range"] is None
    assert usage_report([attempt], "unknown-model")["estimated_cost_usd_range"] is None


def test_demo_task_is_public_and_private_probes_are_fixed():
    from src.eval.deepseek_demo import make_task, make_spec, candidate_diff
    from src.eval.contracts import PublicTask
    base = {"boltons/strutils.py": b"original\n"}
    task = make_task(base)
    assert PublicTask.from_mapping(task.agent_payload()) == task
    spec = make_spec(base)
    assert len(spec.cases) == 18
    assert len({c.case_id for c in spec.cases}) == 18
    assert spec.editable_paths == ("boltons/strutils.py",)
    assert "cases" not in task.agent_payload()
    assert "private" not in json.dumps(task.agent_payload())
    patch = candidate_diff(base, {"boltons/strutils.py": b"changed\n"})
    assert "-original\n+changed\n" in patch


def test_factory_wires_deepseek_mode_and_resume_fingerprint(tmp_path):
    from src.harness.factory import build_agent
    from src.config.schema import HarnessConfig
    from src.runtime.session import runtime_fingerprint
    config = HarnessConfig.model_validate({"llm": {"base_url": "https://example.invalid",
                                          "thinking": "disabled"},
                                          "runtime": {"budget": {"max_model_calls": 5}}}).model_dump()
    agent = build_agent(config, tmp_path)
    assert agent.llm.thinking == "disabled"
    before = runtime_fingerprint(agent)
    agent.llm.thinking = None
    assert runtime_fingerprint(agent) != before


async def test_frozen_source_copy_rejects_modified_bytes_before_any_call(tmp_path):
    from src.eval.deepseek_demo import prepare
    seed = tmp_path / "seed"
    (seed / "boltons").mkdir(parents=True)
    (seed / "boltons" / "strutils.py").write_text("tampered")
    out = tmp_path / "attempt"
    with pytest.raises(ValueError, match="source hash mismatch"):
        await prepare(out, seed)
    failure = json.loads((out / "preparation.json").read_text())
    assert failure["status"] == "invalid_env" and failure["model_calls"] == 0
    before = (out / "preparation.json").read_bytes()
    with pytest.raises(FileExistsError):
        await prepare(out, seed)
    assert (out / "preparation.json").read_bytes() == before
