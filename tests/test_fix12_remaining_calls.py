"""Opt-in: no "remaining model calls" message; the default still appends it."""
from types import SimpleNamespace

from src.config.schema import RuntimeBudgetConfig
from src.harness.factory import build_agent, load_config
from src.runtime.loop import AgentLoop
from src.runtime.session import runtime_fingerprint
from tests.test_fix1_config_lock import ENV, ROOT


def loop(announce):
    agent = AgentLoop.__new__(AgentLoop)
    agent.model_ledger = SimpleNamespace(max_model_calls=40, calls_started=3)
    agent.announce_remaining_calls = announce
    return agent


def test_default_appends_the_counter_and_the_option_hides_it():
    view = [{"role": "user", "content": "task"}]
    assert loop(True)._with_remaining_calls(view)[-1] == {"role": "user", "content": "remaining model calls: 37/40"}
    assert loop(False)._with_remaining_calls(view) == view


def test_config_default_is_omitted_and_reaches_the_loop(tmp_path, monkeypatch):
    assert "hide_remaining_calls" not in RuntimeBudgetConfig().model_dump()
    assert RuntimeBudgetConfig(hide_remaining_calls=True).model_dump()["hide_remaining_calls"] is True
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    config = load_config(ROOT / "configs/sprint/b0-v2.yaml")
    off = build_agent(config, tmp_path / "off")
    assert off.announce_remaining_calls is True
    config["runtime"]["budget"]["hide_remaining_calls"] = True
    on = build_agent(config, tmp_path / "on")
    assert on.announce_remaining_calls is False
    assert runtime_fingerprint(on) != runtime_fingerprint(off)
