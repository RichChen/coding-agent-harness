"""F1: each model-call stop is labelled by its cause, and run.finished carries the error."""
from decimal import Decimal

import pytest

from scripts import run_cr2
from scripts.run_swe_task import summarize_events
from src.llm.client import LLMError, LLMErrorKind, LLMResponse
from src.policy.budget import Budget
from src.policy.cny_budget import A5BudgetPlan, CnyBudgetLedger, PriceSnapshot
from src.policy.sandbox import Sandbox
from src.runtime.loop import AgentLoop, FailureReason
from src.runtime.retry import RetryConfig
from src.runtime.session import RunSession, compatibility, decode_result
from src.tools.registry import ToolRegistry
from tests.fixtures.mock_llm import MockLLMClient


class RaisingLLM(MockLLMClient):
    def __init__(self, error, api_key=None):
        super().__init__([])
        self.error = error
        self.api_key = api_key

    async def chat(self, messages, tools=None):
        self.calls.append({"messages": messages})
        raise self.error


def price():
    return PriceSnapshot("DeepSeek", "deepseek-flash", "snapshot-test", "CNY",
                         Decimal("0.010"), Decimal("2.000"), Decimal("8.000"), "https://provider/pricing",
                         "2026-11-06", True)


async def run(tmp_path, llm, *, retry=None, cny_plan=None):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    loop = AgentLoop(llm, ToolRegistry(), Sandbox(workspace), Budget(max_steps=3),
                     retry_config=retry or RetryConfig(max_retries=0, backoff_base_seconds=0))
    if cny_plan is not None:
        loop.retry.cny_ledger = CnyBudgetLedger(price(), plan=cny_plan)
    expected = compatibility(workspace=workspace, code_id="fix1", config={}, backend="fixture")
    with RunSession.create(tmp_path / "session", task_id="fix1", expected=expected) as session:
        result = await session.run(loop, "fixture task")
        events = session.store.read_events()
        session.store.export_jsonl(tmp_path / "run" / "events.jsonl")
    finished = [e["payload"] for e in events if e["event_type"] == "run.finished"]
    assert len(finished) == 1
    return result, finished[0]


def tiny_plan(*, total="0.010", calls=1440):
    total = Decimal(total)
    return A5BudgetPlan(total_cny=total, build_cny=Decimal("0.000"), pilot_cny=total,
                        reserve_cny=Decimal("0.000"), pilot_calls=calls)


@pytest.mark.asyncio
async def test_cny_hard_cap_is_budget_exhausted(tmp_path):
    llm = MockLLMClient([LLMResponse(text="never reached")])
    result, finished = await run(tmp_path, llm, cny_plan=tiny_plan())
    assert llm.calls == []
    assert result.failure_reason == FailureReason.BUDGET_EXHAUSTED
    assert result.termination_kind == "cny_hard_cap"
    assert finished["failure_reason"] == "budget_exhausted"
    assert finished["termination_kind"] == "cny_hard_cap"
    assert finished["error_type"] == "CnyBudgetError"
    assert finished["error_message"] == "CNY hard cap would be exceeded"


@pytest.mark.asyncio
async def test_cny_call_cap_is_budget_exhausted(tmp_path):
    result, finished = await run(tmp_path, MockLLMClient([]), cny_plan=tiny_plan(total="50.000", calls=0))
    assert result.failure_reason == FailureReason.BUDGET_EXHAUSTED
    assert finished["termination_kind"] == "cny_call_cap"
    assert finished["error_type"] == "CnyBudgetError"
    assert finished["error_message"] == "pilot call cap reached"


@pytest.mark.asyncio
async def test_billing_unknown_stop_is_not_a_parse_error(tmp_path):
    llm = MockLLMClient([LLMResponse(text="answer without provider usage")])
    result, finished = await run(tmp_path, llm, retry=RetryConfig(max_retries=0, stop_on_unknown_usage=True))
    assert result.failure_reason == FailureReason.PAID_CALL_STOPPED
    assert finished["failure_reason"] == "paid_call_stopped"
    assert finished["termination_kind"] == "billing_usage_unknown"
    assert finished["error_type"] == "PaidCallStopped"
    assert finished["error_message"] == "billing_usage_unknown"


@pytest.mark.asyncio
async def test_provider_http_error_after_retries_is_provider_error(tmp_path):
    error = LLMError("LLM API error 503: upstream overloaded for key sk-test-123", kind=LLMErrorKind.HTTP,
                     status_code=503)
    llm = RaisingLLM(error, api_key="sk-test-123")
    result, finished = await run(tmp_path, llm, retry=RetryConfig(max_retries=2, backoff_base_seconds=0))
    assert len(llm.calls) == 3
    assert result.failure_reason == FailureReason.PROVIDER_ERROR
    assert finished["failure_reason"] == "provider_error"
    assert finished["termination_kind"] == "provider_http_503"
    assert finished["error_type"] == "LLMError"
    assert finished["error_message"] == "LLM API error 503: upstream overloaded for key [redacted]"
    # The CR-2 rerun rule reads model.settled, not the label, so this run is still rerun as before.
    assert run_cr2.needs_rerun(tmp_path / "run", {"status": "fail"}) == "provider http 503"


@pytest.mark.asyncio
async def test_provider_network_error_is_provider_error(tmp_path):
    error = LLMError("LLM network error: connection reset", kind=LLMErrorKind.NETWORK)
    result, finished = await run(tmp_path, RaisingLLM(error))
    assert result.failure_reason == FailureReason.PROVIDER_ERROR
    assert finished["termination_kind"] == "provider_network"


@pytest.mark.asyncio
async def test_real_parse_failure_stays_model_parse_error(tmp_path):
    error = LLMError("Unable to parse LLM response: 'choices'", kind=LLMErrorKind.PARSE)
    result, finished = await run(tmp_path, RaisingLLM(error))
    assert result.failure_reason == FailureReason.MODEL_PARSE_ERROR
    assert finished["failure_reason"] == "model_parse_error"
    assert finished["termination_kind"] == "model_parse_error"
    assert finished["error_type"] == "LLMError"
    assert finished["error_message"] == "Unable to parse LLM response: 'choices'"
    assert run_cr2.needs_rerun(tmp_path / "run", {"status": "fail"}) is None


@pytest.mark.asyncio
async def test_unclassified_exception_is_unknown_not_parse_error(tmp_path):
    result, finished = await run(tmp_path, RaisingLLM(RuntimeError("plugin exploded")))
    assert result.failure_reason == FailureReason.UNKNOWN
    assert finished["termination_kind"] == "model_call_error"
    assert finished["error_type"] == "RuntimeError"
    assert finished["error_message"] == "plugin exploded"


@pytest.mark.asyncio
async def test_error_message_is_truncated_to_512_utf8_bytes(tmp_path):
    error = LLMError("错" * 400, kind=LLMErrorKind.PARSE)
    _, finished = await run(tmp_path, RaisingLLM(error))
    data = finished["error_message"].encode("utf-8")
    assert len(data) <= 512 and data.decode("utf-8") == "错" * 170


def test_old_summary_without_error_fields_still_decodes():
    old = {"task_id": "t", "success": False, "final_answer": None, "failure_reason": "model_parse_error",
           "steps": [], "termination_kind": None}
    result = decode_result(old)
    assert result.failure_reason == FailureReason.MODEL_PARSE_ERROR
    assert result.error_type is None and result.error_message is None


def test_stop_reason_reports_the_specific_cause(tmp_path):
    events = [{"event_type": "run.finished",
               "payload": {"failure_reason": "budget_exhausted", "termination_kind": "cny_hard_cap"}}]
    assert summarize_events(events, {}, None, tmp_path)["stop_reason"] == "cny_hard_cap"
