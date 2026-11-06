"""Opt-in calibrated admission estimate; the default byte estimate is unchanged."""
import math
from types import SimpleNamespace

import pytest

from src.harness.factory import build_agent, load_config
from src.runtime import window
from src.runtime.completion import CompletionController
from src.runtime.hooks import HookContext
from src.runtime.loop import FailureReason
from src.runtime.window import (CALIBRATED_ASCII_BYTES_PER_TOKEN as D, ContextOverflowError, WindowPolicy,
                                WindowPolicyHooks, admit, encoded, request_size)
from tests.fixtures.h4_worker import make_loop
from tests.test_fix1_config_lock import ENV, ROOT

TOOLS = [{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}]


def messages(text):
    return [{"role": "system", "content": "sys"}, {"role": "user", "content": text}]


def test_default_estimate_is_unchanged():
    msgs = messages("x" * 1000 + "é")
    size = len(encoded({"messages": msgs, "tools": TOOLS}))
    stats = request_size(msgs, TOOLS, 64)
    assert stats == request_size(msgs, TOOLS, 64, "utf8_bytes")
    assert stats["admission_input_tokens"] == size + 512 + 32 * 2 + 128
    assert stats["estimated_input_tokens"] == (size + 3) // 4
    assert stats["admission_method"] == "utf8_bytes_plus_protocol_allowance"


def test_calibrated_divides_ascii_and_counts_each_non_ascii_byte():
    ascii_only = messages("x" * 1000)
    size = len(encoded({"messages": ascii_only, "tools": TOOLS}))
    stats = request_size(ascii_only, TOOLS, 0, "calibrated_v1")
    assert stats["admission_input_tokens"] == math.ceil(size / D) + 512 + 64 + 128
    assert stats["admission_method"].startswith("ascii_bytes_calibrated_v1")
    # The ledger estimate is not part of admission and stays as before.
    assert stats["estimated_input_tokens"] == (size + 3) // 4
    cjk = messages("x" * 1000 + "你好")
    extra = request_size(cjk, TOOLS, 0, "calibrated_v1")["admission_input_tokens"] - stats["admission_input_tokens"]
    assert extra == 6


@pytest.mark.parametrize("text", ["", "x" * 50000, "日本語" * 3000, '{"a": [1, 2]}\n' * 999],
                         ids=["empty", "ascii", "cjk", "json"])
def test_calibrated_never_exceeds_the_byte_estimate(text):
    msgs = messages(text)
    assert (request_size(msgs, TOOLS, 0, "calibrated_v1")["admission_input_tokens"]
            <= request_size(msgs, TOOLS, 0)["admission_input_tokens"])


def test_admit_uses_the_selected_estimate():
    msgs = messages("x" * 20000)
    limit = request_size(msgs, TOOLS, 0, "calibrated_v1")["admission_input_tokens"] + 100
    with pytest.raises(ContextOverflowError) as caught:
        admit(msgs, TOOLS, 100, limit)
    assert caught.value.stats["overflow"]
    stats = admit(msgs, TOOLS, 100, limit, admission="calibrated_v1")
    assert stats["max_tokens"] == limit and "overflow" not in stats
    with pytest.raises(ContextOverflowError):
        admit(msgs, TOOLS, 101, limit, admission="calibrated_v1")
    with pytest.raises(ValueError):
        admit(msgs, TOOLS, 0, limit, admission="tokenizer")


@pytest.mark.asyncio
async def test_window_hook_admits_and_records_the_method():
    msgs = messages("x" * 20000)
    limit = request_size(msgs, [], 64, "calibrated_v1")["admission_input_tokens"] + 64
    context = HookContext(extra={"tools": [], "max_output_tokens": 64, "public_task": {"task_id": "t"}})
    with pytest.raises(ContextOverflowError):
        await WindowPolicyHooks(WindowPolicy("none", max_tokens=limit)).before_llm(msgs, context)
    hook = WindowPolicyHooks(WindowPolicy("none", max_tokens=limit), admission="calibrated_v1")
    assert await hook.before_llm(msgs, context) == msgs
    assert context.extra["context_stats"]["admission_method"].startswith("ascii_bytes_calibrated_v1")
    assert hook.fingerprint()["admission"] == {"estimate": "calibrated_v1", "ascii_bytes_per_token": D}
    assert "admission" not in WindowPolicyHooks(WindowPolicy("none", max_tokens=limit)).fingerprint()
    with pytest.raises(ValueError):
        WindowPolicyHooks(WindowPolicy("none"), admission="tokenizer")


@pytest.mark.asyncio
@pytest.mark.parametrize("admission", ["utf8_bytes", "calibrated_v1"])
async def test_loop_admission_follows_the_hook(tmp_path, admission):
    loop = make_loop(tmp_path)
    # Default: step 0 is ~41.4K bytes + 4096 output. Calibrated (D >= 2): step 1 is ~61K bytes / D + 4096.
    limit = 38000
    loop.hooks = WindowPolicyHooks(WindowPolicy("none", max_tokens=limit), admission=admission)
    result = await loop.run("a" * 40000)
    if admission == "utf8_bytes":
        assert result.failure_reason == FailureReason.CONTEXT_OVERFLOW
        assert loop.llm._call_count == 0
    else:
        assert result.success and loop.llm._call_count == 2
        assert all(e["admission_method"].startswith("ascii_bytes_calibrated_v1") for e in result.context_events)


def test_workspace_feedback_admission_follows_the_hook(monkeypatch):
    seen = []

    def spy(*args, **kwargs):
        seen.append(kwargs.get("admission"))
        return dict(estimated_input_tokens=1)

    monkeypatch.setattr(window, "admit", spy)
    controller = CompletionController.__new__(CompletionController)
    controller.agent_deadline, controller.output_reserve, controller.output_total = float("inf"), 0, 10**9
    controller.storage = SimpleNamespace(final_reserve=0, store=SimpleNamespace(used_bytes=lambda: 0, limit_bytes=10**9))
    for admission in ("utf8_bytes", "calibrated_v1"):
        controller.loop = SimpleNamespace(
            cancel_event=None, budget=SimpleNamespace(step_count=0, max_steps=9),
            run_output_meter=SimpleNamespace(bytes_kept=0), llm=SimpleNamespace(max_tokens=64),
            tools=SimpleNamespace(schemas=lambda enabled: []), enabled_tools=None, model_ledger=None,
            hooks=WindowPolicyHooks(WindowPolicy("none"), admission=admission))
        assert controller.can_feedback({"role": "user", "content": "x"}, [])
    assert seen == ["utf8_bytes", "calibrated_v1"]


def test_config_option_reaches_the_hook(tmp_path, monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    config = load_config(ROOT / "configs/workspace/p4-sphinx-deepseek-delayed-mask.yaml")
    assert build_agent(config, tmp_path / "off").hooks.admission == "utf8_bytes"
    config["runtime"]["context"]["admission_estimate"] = "calibrated_v1"
    assert build_agent(config, tmp_path / "on").hooks.admission == "calibrated_v1"
