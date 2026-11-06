"""Context protection rules v2 behind `runtime.context.protection_rules` (fix plan 1, F3)."""
import asyncio
import copy

import pytest

from src.config.schema import HarnessConfig, RuntimeContextConfig
from src.runtime import compact as rules
from src.runtime.compact import (MicroCompactHooks, _protected_tool_indices, _tool_content_indicates_failure,
                                 micro_compact_openai_messages_with_stats)
from src.runtime.context import ContextPolicyConfig, build_context_hooks
from src.runtime.hooks import HookContext
from src.runtime.window import WindowPolicy, WindowPolicyHooks, view_hash
from tests.fixtures.fix1_context import (GREP_NO_MATCH, LISTING, PYTEST_FAILURE, STATUS_ONLY_FAILURE, TRACEBACK,
                                         call_ids, history, tool_positions)

# Recorded from the unmodified code at 7aa535f (.tmp-fix1/record_context.py).
HEAD = {
    "protected_v1": ["c7"],
    "micro_compact_default": "24205e8ed2441530eef9b6f885d4b65f44f17b82ede226912ae9350d1b334f11",
    "micro_compact_masking": "d498b92460aeca4497914079d910cfff83fa7e7045aa3deb0e113317780d8a6c",
    "window": {
        "masking": "d498b92460aeca4497914079d910cfff83fa7e7045aa3deb0e113317780d8a6c",
        "summary": "6564325baaa54caea5d5e15e51b102aa807974fb4958e3e83970777973c5b0ec",
        "mask-then-summary": "d498b92460aeca4497914079d910cfff83fa7e7045aa3deb0e113317780d8a6c",
    },
}
WINDOWS = (("masking", 131072), ("summary", 7000), ("mask-then-summary", 7000))


@pytest.mark.parametrize("content, v1, v2", [
    (GREP_NO_MATCH, True, False),
    (STATUS_ONLY_FAILURE, True, False),
    ("Error: nonzero_exit", True, False),
    (PYTEST_FAILURE, True, True),
    (TRACEBACK, True, True),
    ("Ran 4 tests in 0.010s\n\nFAILED (failures=1)", True, True),
    ("E       assert 5 == 4", False, True),
    (LISTING, False, False),
], ids=["grep-no-match", "exit-2-no-output", "error-line-only", "pytest-failure", "traceback", "unittest-summary",
        "assert-detail", "listing"])
def test_failure_rules(content, v1, v2):
    assert _tool_content_indicates_failure(content) is v1
    assert rules._shows_substantive_failure(content) is v2


@pytest.mark.parametrize("command", [
    "cat > pkg/widget.py <<'EOF'\nx = 1\nEOF",
    "echo 'x = 1' >> pkg/widget.py",
    "printf 'x' | tee pkg/widget.py",
    "sed -i 's/a/b/' pkg/widget.py",
    "sed -e 's/a/b/' -i.bak pkg/widget.py",
    "git apply fix.diff",
    "python gen.py > out.txt 2>&1",
    "pytest -q &> log.txt",
    "perl -pi -e 's/a/b/' f.py",
    "patch -p1 < fix.diff",
])
def test_v2_counts_file_writing_commands(command):
    assert rules._command_writes_files(command)


@pytest.mark.parametrize("command", [
    "ls -la pkg",
    "grep -rn needle . 2>&1",
    "python -m pytest -q 2>/dev/null",
    "python -c 'print(1)'",
    "git diff",
    "sed -n '1,5p' pkg/widget.py",
    "cat pkg/widget.py | head -n 5",
    "find . -name '*.py' -newer setup.py",
])
def test_v2_ignores_read_only_commands(command):
    assert not rules._command_writes_files(command)


def test_v1_protection_is_unchanged_and_v2_protects_real_failure_and_edits():
    messages = history()
    positions = tool_positions(messages)
    assert call_ids(messages, _protected_tool_indices(messages, positions)) == HEAD["protected_v1"]
    assert call_ids(messages, _protected_tool_indices(messages, positions, rules="v1")) == HEAD["protected_v1"]
    assert call_ids(messages, _protected_tool_indices(messages, positions, rules="v2")) == ["c2", "c3", "c5"]


def test_unknown_rules_are_rejected():
    with pytest.raises(ValueError):
        _protected_tool_indices(history(), tool_positions(history()), rules="v3")
    with pytest.raises(ValueError):
        RuntimeContextConfig(protection_rules="v3")


def compact(**kwargs):
    view, _ = micro_compact_openai_messages_with_stats(copy.deepcopy(history()), keep_recent=2, **kwargs)
    return view


def test_micro_compact_default_matches_head():
    assert view_hash(compact()) == HEAD["micro_compact_default"]
    assert view_hash(compact(protection_rules="v1")) == HEAD["micro_compact_default"]
    assert view_hash(compact(preserve_tools=set())) == HEAD["micro_compact_masking"]
    assert view_hash(compact(preserve_tools=set(), protection_rules="v1")) == HEAD["micro_compact_masking"]


def test_micro_compact_v2_keeps_real_failure_and_edits_verbatim():
    original = history()
    view = compact(preserve_tools=set(), protection_rules="v2")
    kept = {m["tool_call_id"] for m, o in zip(view, original) if m.get("role") == "tool" and m == o}
    assert {"c2", "c3", "c5", "c8", "c9"} <= kept
    assert "c4" not in kept and "c6" not in kept


async def window_view(strategy, max_tokens, **kwargs):
    hooks = WindowPolicyHooks(WindowPolicy(strategy=strategy, max_tokens=max_tokens, keep_recent=2), **kwargs)
    ctx = HookContext(step_number=1, workspace_root=".", extra={"tools": [], "max_output_tokens": 64})
    return hooks, await hooks.before_llm(history(), ctx)


@pytest.mark.parametrize("strategy, max_tokens", WINDOWS)
def test_window_default_matches_head(strategy, max_tokens):
    for kwargs in ({}, {"protection_rules": "v1"}):
        hooks, view = asyncio.run(window_view(strategy, max_tokens, **kwargs))
        assert view_hash(view) == HEAD["window"][strategy]
        assert "protection_rules" not in hooks.fingerprint()


def test_window_v2_summary_does_not_swallow_failure_or_edits():
    hooks, view = asyncio.run(window_view("summary", 7000, protection_rules="v2"))
    assert view_hash(view) != HEAD["window"]["summary"]
    kept = {m.get("tool_call_id") for m in view if m.get("role") == "tool"}
    assert {"c2", "c3", "c5"} <= kept
    assert hooks.fingerprint()["protection_rules"] == "v2"


def test_config_option_is_omitted_from_dump_unless_enabled():
    assert "protection_rules" not in RuntimeContextConfig().model_dump()
    assert "protection_rules" not in RuntimeContextConfig(protection_rules="v1").model_dump()
    assert RuntimeContextConfig(protection_rules="v2").model_dump()["protection_rules"] == "v2"
    dumped = HarnessConfig.model_validate({"llm": {"base_url": "http://x"},
                                          "runtime": {"context": {"protection_rules": "v2"}}}).model_dump()
    assert dumped["runtime"]["context"]["protection_rules"] == "v2"
    assert HarnessConfig.model_validate(dumped).runtime.context.protection_rules == "v2"


def test_rules_reach_both_hook_types():
    micro = build_context_hooks(ContextPolicyConfig.from_runtime_config(
        {"micro_compact": True, "protection_rules": "v2"}))
    assert type(micro) is MicroCompactHooks and micro.protection_rules == "v2"
    window = build_context_hooks(ContextPolicyConfig.from_runtime_config(
        {"strategy": "summary", "protection_rules": "v2"}))
    assert type(window) is WindowPolicyHooks and window.protection_rules == "v2"
    legacy = build_context_hooks(ContextPolicyConfig.from_runtime_config({"micro_compact": True}))
    assert legacy.protection_rules == "v1"
    assert "protection_rules" not in ContextPolicyConfig.from_runtime_config({}).to_trace_metadata()
