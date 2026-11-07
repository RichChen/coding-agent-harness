"""Opt-in masking trigger measured with the admission estimate; the default stays bytes / 4."""
import pytest

from src.config.schema import RuntimeContextConfig
from src.harness.factory import build_agent, load_config
from src.runtime.hooks import HookContext
from src.runtime.window import WindowPolicy, WindowPolicyHooks, request_size
from tests.test_fix1_config_lock import ENV, ROOT


def conversation(results=3, size=20000):
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "task"}]
    for i in range(results):
        call = {"id": f"c{i}", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}
        msgs += [{"role": "assistant", "content": None, "tool_calls": [call]},
                 {"role": "tool", "tool_call_id": f"c{i}", "content": "x" * size}]
    return msgs


async def masked(hook, msgs):
    context = HookContext(extra={"tools": [], "max_output_tokens": 64, "public_task": {"task_id": "t"}})
    await hook.before_llm(msgs, context)
    return context.extra["context_stats"]["actually_injected"] == "masking"


@pytest.mark.asyncio
async def test_threshold_between_the_two_estimates_only_fires_on_admission():
    msgs = conversation()
    quarter = request_size(msgs)["estimated_input_tokens"]
    calibrated = request_size(msgs, [], 0, "calibrated_v1")["admission_input_tokens"]
    assert quarter < calibrated
    policy = WindowPolicy("masking", max_tokens=10**6, keep_recent=1, compact_token_threshold=(quarter + calibrated) // 2)
    assert not await masked(WindowPolicyHooks(policy, admission="calibrated_v1"), msgs)
    assert await masked(WindowPolicyHooks(policy, admission="calibrated_v1", compact_trigger="admission"), msgs)
    below = WindowPolicy("masking", max_tokens=10**6, keep_recent=1, compact_token_threshold=calibrated + 1)
    assert not await masked(WindowPolicyHooks(below, admission="calibrated_v1", compact_trigger="admission"), msgs)


def test_fingerprint_and_validation():
    policy = WindowPolicy("masking", compact_token_threshold=10)
    assert "compact_trigger" not in WindowPolicyHooks(policy).fingerprint()
    assert WindowPolicyHooks(policy, compact_trigger="admission").fingerprint()["compact_trigger"] == "admission"
    with pytest.raises(ValueError):
        WindowPolicyHooks(policy, compact_trigger="tokens")


def test_config_default_is_omitted_and_reaches_the_hook(tmp_path, monkeypatch):
    assert "compact_trigger_estimate" not in RuntimeContextConfig().model_dump()
    assert RuntimeContextConfig(compact_trigger_estimate="admission").model_dump()["compact_trigger_estimate"] == "admission"
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    config = load_config(ROOT / "configs/workspace/p4-sphinx-deepseek-delayed-mask.yaml")
    assert build_agent(config, tmp_path / "off").hooks.compact_trigger == "bytes_div_4"
    config["runtime"]["context"]["compact_trigger_estimate"] = "admission"
    assert build_agent(config, tmp_path / "on").hooks.compact_trigger == "admission"
