"""Per-request telemetry: opt-in fields on context.prepared and model.settled."""
import pytest

from src.config.schema import HarnessConfig, TracingConfig
from src.llm.client import LLMResponse
from src.policy.budget import Budget
from src.policy.sandbox import Sandbox
from src.runtime.loop import AgentLoop
from src.runtime.retry import RetryConfig
from src.runtime.session import RunSession, compatibility
from src.runtime.telemetry import request_context, settled_telemetry
from src.tools.registry import ToolRegistry
from tests.fixtures.mock_llm import MockLLMClient

SYSTEM = {"role": "system", "content": "s"}
USER = {"role": "user", "content": "fix it"}


def call(cid):
    return {"role": "assistant", "content": None,
            "tool_calls": [{"id": cid, "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]}


def tool(cid, text):
    return {"role": "tool", "tool_call_id": cid, "content": text}


RAW = [SYSTEM, USER, call("c1"), tool("c1", "x" * 500), call("c2"), tool("c2", "y" * 500)]


def test_schema_default_omits_the_option():
    assert "request_telemetry" not in TracingConfig().model_dump()
    assert TracingConfig(request_telemetry=True).model_dump()["request_telemetry"] is True
    config = HarnessConfig(llm={"base_url": "http://x"}, tracing={"request_telemetry": True})
    assert config.tracing.request_telemetry is True


def test_masked_messages_are_listed():
    view = list(RAW)
    view[3] = tool("c1", "[masked]")
    stats = {"step": 4, "admission_input_tokens": 900, "admission_method": "utf8_bytes_plus_protocol_allowance",
             "actually_injected": "masking", "summary_records": []}
    context = request_context(RAW, view, stats)
    assert context == {
        "step": 4, "admission_input_tokens": 900, "admission_method": "utf8_bytes_plus_protocol_allowance",
        "masked": True, "summarized": False, "message_count_raw": 6, "message_count_view": 6,
        "replaced": [{"index": 3, "role": "tool", "tool_call_id": "c1", "kind": "masked"}],
    }


def test_summarized_messages_are_listed():
    view = [SYSTEM, USER, {"role": "assistant", "content": "summary"}, call("c2"), tool("c2", "y" * 500)]
    stats = {"step": 5, "admission_input_tokens": 700, "admission_method": "m", "actually_injected": "summary",
             "summary_records": [{"source_start": 2, "source_end": 4}]}
    context = request_context(RAW, view, stats)
    assert context["summarized"] is True and context["masked"] is False
    assert context["replaced"] == [{"index": 2, "role": "assistant", "tool_call_id": "c1", "kind": "summarized"},
                                   {"index": 3, "role": "tool", "tool_call_id": "c1", "kind": "summarized"}]


def test_legacy_hook_compaction_counts_as_masking():
    view = list(RAW)
    view[3] = tool("c1", "[compacted]")
    context = request_context(RAW, view, {"step": 1, "did_compact": True, "strategy": "legacy-hook"})
    assert context["masked"] is True and context["replaced"][0]["kind"] == "masked"


def test_no_change_has_nothing_replaced():
    context = request_context(RAW, list(RAW), {"step": 0, "admission_input_tokens": 1000})
    assert context["replaced"] == [] and context["masked"] is False and context["summarized"] is False


def test_settled_telemetry_pairs_admission_with_provider_usage():
    context = {"step": 2, "admission_input_tokens": 1200, "admission_method": "m", "masked": True,
               "summarized": False, "replaced": [{"index": 3}], "message_count_raw": 6, "message_count_view": 6}
    usage = {"prompt_tokens": 1000, "completion_tokens": 300, "total_tokens": 1300, "prompt_cache_hit_tokens": 768,
             "prompt_cache_miss_tokens": 232, "completion_tokens_details": {"reasoning_tokens": 120},
             "provenance": "provider"}
    assert settled_telemetry(context, usage) == {
        "step": 2, "admission_input_tokens": 1200, "admission_method": "m", "prompt_tokens": 1000,
        "admission_minus_prompt": 200, "admission_over_prompt": 1.2, "prompt_cache_hit_tokens": 768,
        "prompt_cache_miss_tokens": 232, "completion_tokens": 300, "reasoning_tokens": 120,
        "masked": True, "summarized": False, "replaced_count": 1,
    }


def test_settled_telemetry_with_unknown_usage():
    context = {"step": 0, "admission_input_tokens": 50, "admission_method": "m", "masked": False,
               "summarized": False, "replaced": []}
    out = settled_telemetry(context, {"provenance": "unknown"})
    assert out["prompt_tokens"] is None and out["admission_minus_prompt"] is None
    assert out["admission_over_prompt"] is None and out["reasoning_tokens"] is None


USAGE = {"prompt_tokens": 40, "completion_tokens": 5, "total_tokens": 45, "prompt_cache_hit_tokens": 0,
         "prompt_cache_miss_tokens": 40, "completion_tokens_details": {"reasoning_tokens": 3}}


async def run(tmp_path, *, telemetry):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    llm = MockLLMClient([LLMResponse(text="done", usage=dict(USAGE))])
    options = {"request_telemetry": True} if telemetry else {}
    loop = AgentLoop(llm, ToolRegistry(), Sandbox(workspace), Budget(max_steps=2),
                     retry_config=RetryConfig(max_retries=0, backoff_base_seconds=0), **options)
    expected = compatibility(workspace=workspace, code_id="fix10", config={}, backend="fixture")
    with RunSession.create(tmp_path / "session", task_id="fix10", expected=expected) as session:
        await session.run(loop, "fixture task")
        events = session.store.read_events()
    prepared = [e["payload"] for e in events if e["event_type"] == "context.prepared"]
    settled = [e["payload"] for e in events if e["event_type"] == "model.settled"]
    return prepared, settled


@pytest.mark.asyncio
async def test_events_are_unchanged_by_default(tmp_path):
    prepared, settled = await run(tmp_path, telemetry=False)
    assert prepared and settled
    assert "request_telemetry" not in prepared[0]["stats"]
    assert "telemetry" not in settled[0]["record"]


@pytest.mark.asyncio
async def test_events_carry_telemetry_when_enabled(tmp_path):
    prepared, settled = await run(tmp_path, telemetry=True)
    context = prepared[0]["stats"]["request_telemetry"]
    assert context["step"] == 0 and context["replaced"] == []
    assert context["admission_input_tokens"] == prepared[0]["stats"]["admission_input_tokens"]
    telemetry = settled[0]["record"]["telemetry"]
    assert telemetry["step"] == 0
    assert telemetry["prompt_tokens"] == 40 and telemetry["reasoning_tokens"] == 3
    assert telemetry["admission_minus_prompt"] == context["admission_input_tokens"] - 40
    assert telemetry["prompt_cache_hit_tokens"] == 0


@pytest.mark.asyncio
async def test_recovered_attempts_still_rebuild(tmp_path):
    from src.runtime.recovery import reduce_events
    from src.runtime.retry import AttemptRecord

    workspace = tmp_path / "ws"
    workspace.mkdir()
    llm = MockLLMClient([LLMResponse(text="done", usage=dict(USAGE))])
    loop = AgentLoop(llm, ToolRegistry(), Sandbox(workspace), Budget(max_steps=2),
                     retry_config=RetryConfig(max_retries=0, backoff_base_seconds=0), request_telemetry=True)
    expected = compatibility(workspace=workspace, code_id="fix10", config={}, backend="fixture")
    with RunSession.create(tmp_path / "session", task_id="fix10", expected=expected) as session:
        await session.run(loop, "fixture task")
        events = session.store.read_events()
    attempts = reduce_events(events).attempts
    assert attempts and [AttemptRecord(**a) for a in attempts]


def test_factory_passes_the_option(tmp_path):
    from src.harness.factory import build_agent
    config = HarnessConfig(llm={"base_url": "http://127.0.0.1:9", "api_key": "k", "trust_env": False},
                           tracing={"request_telemetry": True}).model_dump()
    agent = build_agent(config, tmp_path)
    assert agent.request_telemetry is True
    config["tracing"].pop("request_telemetry")
    assert build_agent(config, tmp_path).request_telemetry is False
