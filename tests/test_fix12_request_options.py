"""Per-request telemetry records the thinking options the request body actually carried."""
import json

import httpx
import pytest

from src.llm.client import LLMClient, OpenAICompatibleAdapter, request_options
from src.policy.budget import Budget
from src.policy.sandbox import Sandbox
from src.runtime.loop import AgentLoop
from src.runtime.retry import RetryConfig
from src.runtime.session import RunSession, compatibility
from src.tools.registry import ToolRegistry

USAGE = {"prompt_tokens": 40, "completion_tokens": 5, "total_tokens": 45, "prompt_cache_hit_tokens": 0,
         "prompt_cache_miss_tokens": 40, "completion_tokens_details": {"reasoning_tokens": 3}}


def test_request_options_read_the_body():
    adapter = OpenAICompatibleAdapter(base_url="https://x")
    off = adapter.build_payload(model="m", messages=[], tools=None, temperature=0, max_tokens=8, thinking="disabled")
    on = adapter.build_payload(model="m", messages=[], tools=None, temperature=0, max_tokens=32, stream=True,
                               thinking="enabled", reasoning_effort="high")
    none = adapter.build_payload(model="m", messages=[], tools=None, temperature=0, max_tokens=8)
    assert request_options(off) == {"model": "m", "max_tokens": 8, "thinking": "disabled", "reasoning_effort": None,
                                    "stream": False}
    assert request_options(on) == {"model": "m", "max_tokens": 32, "thinking": "enabled", "reasoning_effort": "high",
                                   "stream": True}
    assert request_options(none)["thinking"] is None


async def run(tmp_path, *, thinking, effort, telemetry=True):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "r", "model": "m", "usage": USAGE, "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}]})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = LLMClient("https://x", "k", "m", max_tokens=64, supports_streaming=False, http_client=http,
                    thinking=thinking, reasoning_effort=effort)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    loop = AgentLoop(llm, ToolRegistry(), Sandbox(workspace), Budget(max_steps=2),
                     retry_config=RetryConfig(max_retries=0, backoff_base_seconds=0), request_telemetry=telemetry)
    expected = compatibility(workspace=workspace, code_id="fix12", config={}, backend="fixture")
    with RunSession.create(tmp_path / "session", task_id="fix12", expected=expected) as session:
        await session.run(loop, "fixture task")
        events = session.store.read_events()
    await http.aclose()
    return bodies, [e["payload"]["record"] for e in events if e["event_type"] == "model.settled"]


@pytest.mark.asyncio
@pytest.mark.parametrize("thinking,effort", [("disabled", None), ("enabled", "high")])
async def test_settled_telemetry_carries_the_sent_options(tmp_path, thinking, effort):
    bodies, settled = await run(tmp_path, thinking=thinking, effort=effort)
    assert bodies[0]["thinking"] == {"type": thinking} and bodies[0].get("reasoning_effort") == effort
    request = settled[0]["telemetry"]["request"]
    assert request == {"model": "m", "max_tokens": 64, "thinking": thinking, "reasoning_effort": effort, "stream": False}
    assert settled[0]["telemetry"]["reasoning_tokens"] == 3


@pytest.mark.asyncio
async def test_no_telemetry_without_the_option(tmp_path):
    _, settled = await run(tmp_path, thinking="enabled", effort="high", telemetry=False)
    assert "telemetry" not in settled[0]
