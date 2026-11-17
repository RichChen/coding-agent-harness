"""Local-only FIX-13 intervention boundaries and normal loop entry points."""
import copy

import pytest

from src.config.schema import HarnessConfig
from src.llm.client import LLMResponse, ToolCall
from src.policy.budget import Budget
from src.policy.sandbox import Sandbox
from src.runtime.loop import AgentLoop
from src.runtime.tool_intervention import (
    INTERVENTION_SCHEMA,
    DynamicInterventionPolicy,
    InterventionContext,
    InterventionDecision,
    NEXT_ACTION,
    RepeatCallDetector,
    REPEAT_REMINDER,
    RESULT_EXPLANATION,
    intervention_spec,
)
from src.runtime.intervention_fixtures import showcase_policy
from src.runtime.session import RunSession, compatibility
from src.tools.base import Tool, ToolResult
from src.tools.files import ReadFileTool, WriteFileTool
from src.tools.registry import ToolRegistry
from tests.fixtures.mock_llm import MockLLMClient


def call(name="run_command", args=None, call_id="id"):
    return ToolCall(call_id, name, args if args is not None else {"command": "cat source.py"})


def result(output="source\n[exit_code=0 status=succeeded truncated=false duration_ms=1]", **kw):
    return ToolResult("id", "run_command", True, output, exit_code=0, **kw)


def test_third_same_call_once_and_restored():
    detector = RepeatCallDetector()
    c = call()
    assert detector.observe([c], request=1) is None
    assert detector.observe([c], request=2) is None
    event = detector.observe([c], request=3)
    assert event["request"] == 3 and "run_command" in event["call_key"]
    restored = RepeatCallDetector(**detector.snapshot())
    assert restored.observe([c], request=4) is None
    assert restored.observe([c], request=5) is None
    assert "Repeating it is allowed when you have a concrete reason." in REPEAT_REMINDER


@pytest.mark.parametrize("kind", ["different_tool", "different_args", "parallel", "empty", "non_json"])
def test_breaks_consecutive_streak(kind):
    detector = RepeatCallDetector()
    c = call()
    for i in range(2):
        detector.observe([c], request=i)
    calls = [copy.deepcopy(c)]
    if kind == "different_tool":
        calls[0].name = "write_file"
    elif kind == "different_args":
        calls[0].arguments = {"command": "cat  source.py"}
    elif kind == "parallel":
        calls *= 2
    elif kind == "empty":
        calls = []
    else:
        calls[0].arguments = {"command": float("nan")}
    assert detector.observe(calls, request=3) is None
    assert detector.observe([c], request=4) is None
    assert detector.observe([c], request=5) is None
    assert detector.observe([c], request=6) is not None


def test_sorted_keys_preserve_all_values():
    detector = RepeatCallDetector()
    a = call("read_file", {"path": "a", "offset": 1})
    b = call("read_file", {"offset": 1, "path": "a"})
    assert detector.observe([a], request=1) is None
    assert detector.observe([b], request=2) is None
    assert detector.observe([a], request=3)


@pytest.mark.parametrize("mode", ["none", "repeat_reminder", "next_action", "result_explanation"])
def test_intervention_spec_is_versioned_and_compatible(mode):
    spec = intervention_spec(mode)
    data = spec.as_dict()
    assert data["schema"] == INTERVENTION_SCHEMA
    assert data["mode"] == mode
    assert data["prompt_injected"] == (mode in {"next_action", "result_explanation"})
    assert data["policy_version"] == "1"
    assert len(data["config_hash"]) == 64


def test_intervention_spec_rejects_unknown_mode():
    with pytest.raises(ValueError, match="unknown tool intervention"):
        intervention_spec("future_mode")


class ReadCommandFixture(Tool):
    name = "run_command"
    parameters = {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}

    def __init__(self):
        self.executions = 0

    async def execute(self, params, ctx):
        self.executions += 1
        r = result()
        r.call_id = ctx.call_id
        return r


def make_loop(workspace, mode, responses):
    registry = ToolRegistry()
    fixture = ReadCommandFixture()
    registry.register(fixture)
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    loop = AgentLoop(MockLLMClient(responses), registry, Sandbox(workspace), Budget(max_steps=12),
                     tool_intervention=mode, announce_remaining_calls=False)
    return loop, fixture


def repeated_responses(count):
    return [LLMResponse(tool_calls=[call(call_id=str(i))], usage={"total_tokens": 1}) for i in range(count)]


async def test_reminder_is_normal_history_and_never_blocks_later_tools(tmp_path):
    loop, tool = make_loop(tmp_path, "repeat_reminder", repeated_responses(6) + [LLMResponse(text="done")])
    outcome = await loop.run("fixture")
    assert outcome.success and tool.executions == 6 and loop.llm._call_count == 7
    histories = [request["messages"] for request in loop.llm.calls]
    assert not any(m.get("content") == REPEAT_REMINDER for m in histories[2])
    assert sum(m.get("content") == REPEAT_REMINDER for m in histories[3]) == 1
    assert sum(m.get("content") == REPEAT_REMINDER for m in histories[-1]) == 1
    assert outcome.context_events[0]["request"] == 3


@pytest.mark.parametrize("command", ["pytest", "python -c 'print(1)'", "cat file > changed"])
async def test_reminder_counts_calls_despite_failure_truncation_and_changing_outputs(tmp_path, command):
    responses = [LLMResponse(tool_calls=[call(args={"command": command}, call_id=str(i))]) for i in range(4)]
    loop, tool = make_loop(tmp_path, "repeat_reminder", responses + [LLMResponse(text="done")])

    async def failure(params, ctx):
        tool.executions += 1
        return ToolResult(ctx.call_id, tool.name, False, f"different result {tool.executions}",
                          error="fixture failure", exit_code=1, truncated=True)

    tool.execute = failure
    outcome = await loop.run("fixture")
    assert outcome.success and outcome.tool_failures == 4 and tool.executions == 4
    assert outcome.context_events[0]["request"] == 3
    assert sum(m.get("content") == REPEAT_REMINDER for m in loop.llm.calls[-1]["messages"]) == 1


async def test_repeated_write_call_can_trigger_without_blocking_edits(tmp_path):
    responses = [LLMResponse(tool_calls=[call("write_file", {"path": "value", "content": "new"}, str(i))])
                 for i in range(4)] + [LLMResponse(text="done")]
    loop, _ = make_loop(tmp_path, "repeat_reminder", responses)
    outcome = await loop.run("fixture")
    assert outcome.success and outcome.total_tool_invocations == 4
    assert (tmp_path / "value").read_text() == "new"
    assert outcome.context_events[0]["request"] == 3


@pytest.mark.parametrize("content", [None, "The file shows the current setting, so I will update it.", "word " * 45])
async def test_explanation_content_tools_and_missing_sentence_never_add_calls(tmp_path, content):
    responses = [LLMResponse(tool_calls=[call("read_file", {"path": "value"})]),
                 LLMResponse(text=content, tool_calls=[call("write_file", {"path": "value", "content": "new"})]),
                 LLMResponse(text="done")]
    (tmp_path / "value").write_text("old")
    loop, _ = make_loop(tmp_path, "result_explanation", responses)
    outcome = await loop.run("fixture")
    assert outcome.success and loop.llm._call_count == 3
    assert (tmp_path / "value").read_text() == "new"
    history = loop.llm.calls[-1]["messages"]
    assistant = [m for m in history if m["role"] == "assistant"][-1]
    assert assistant["content"] == content and assistant["tool_calls"]
    assert history[0]["content"].endswith(RESULT_EXPLANATION)
    assert not any(m.get("content") == REPEAT_REMINDER for m in history)


async def test_explanation_plain_text_still_finishes(tmp_path):
    loop, tool = make_loop(tmp_path, "result_explanation", repeated_responses(1) + [LLMResponse(text="I am finished.")])
    assert (await loop.run("fixture")).success
    assert loop.llm._call_count == 2 and tool.executions == 1


@pytest.mark.parametrize("mode, prompt", [("next_action", NEXT_ACTION), ("result_explanation", RESULT_EXPLANATION)])
async def test_text_interventions_append_only_their_fixed_prompt(tmp_path, mode, prompt):
    loop, _ = make_loop(tmp_path, mode, [LLMResponse(text="done")])
    assert (await loop.run("fixture")).success
    assert loop.system_prompt.endswith("\n\n" + prompt)


async def test_default_has_no_intervention(tmp_path):
    loop, tool = make_loop(tmp_path, "none", repeated_responses(4) + [LLMResponse(text="done")])
    outcome = await loop.run("fixture")
    assert outcome.success
    assert tool.executions == 4
    assert not any(m.get("content") == REPEAT_REMINDER for m in loop.llm.calls[-1]["messages"])
    assert RESULT_EXPLANATION not in loop.system_prompt
    assert outcome.intervention_events[0]["event"] == "intervention_configured"
    assert outcome.intervention_events[0]["mode"] == "none"


async def test_dynamic_policy_sees_tool_round_and_emits_structured_event(tmp_path):
    state_seen = []

    def decide(context: InterventionContext, state: dict):
        state["rounds"] = state.get("rounds", 0) + 1
        state_seen.append((context.step, len(context.calls), len(context.results)))
        if context.results and context.results[0].success:
            return InterventionDecision(
                prompt="The tool result is available; choose the next action deliberately.",
                trigger="result_available",
                metadata={"round": state["rounds"]},
            )
        return None

    policy = DynamicInterventionPolicy("dynamic_test", decide)
    registry = ToolRegistry()
    fixture = ReadCommandFixture()
    registry.register(fixture)
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    loop = AgentLoop(
        MockLLMClient(repeated_responses(1) + [LLMResponse(text="done")]),
        registry,
        Sandbox(tmp_path),
        Budget(max_steps=8),
        intervention_policy=policy,
        announce_remaining_calls=False,
    )
    outcome = await loop.run("fixture")
    assert outcome.success
    assert state_seen == [(0, 1, 1)]
    triggered = [e for e in outcome.intervention_events if e["event"] == "intervention_triggered"]
    assert len(triggered) == 1
    assert triggered[0]["trigger"] == "result_available"
    assert triggered[0]["metadata"] == {"round": 1}
    assert any(m.get("content") == "The tool result is available; choose the next action deliberately."
               for m in loop.llm.calls[-1]["messages"])


async def test_showcase_policy_runs_through_loop_and_records_evidence(tmp_path):
    from src.runtime.intervention_fixtures import showcase_policy

    responses = [
        LLMResponse(tool_calls=[call("read_file", {"path": "value"}, "1")]),
        LLMResponse(tool_calls=[call("run_command", {"command": "pwd"}, "2")]),
        LLMResponse(text="done"),
    ]
    (tmp_path / "value").write_text("old")
    registry = ToolRegistry()
    registry.register(ReadCommandFixture())
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    loop = AgentLoop(MockLLMClient(responses), registry, Sandbox(tmp_path), Budget(max_steps=8),
                     intervention_policy=showcase_policy(idle_rounds=1),
                     announce_remaining_calls=False)
    outcome = await loop.run("fixture")
    triggered = [e for e in outcome.intervention_events if e["event"] == "intervention_triggered"]
    assert len(triggered) == 1
    assert triggered[0]["trigger"] == "candidate_no_edit_after_read"
    assert outcome.intervention["trigger_count"] == 1
    assert outcome.intervention["prompt_injection_count"] == 1


async def test_empty_decision_prompt_records_trigger_without_injecting_message(tmp_path):
    policy = DynamicInterventionPolicy(
        "empty_prompt",
        lambda context, state: InterventionDecision(prompt=None, trigger="observation_only"),
    )
    loop, _ = make_loop(tmp_path, "none", [LLMResponse(tool_calls=[call()]), LLMResponse(text="done")])
    loop.intervention_policy = policy
    loop.intervention = policy.spec
    loop.tool_intervention = policy.spec.mode
    outcome = await loop.run("fixture")
    triggered = [e for e in outcome.intervention_events if e["event"] == "intervention_triggered"]
    assert triggered[0]["prompt_injected"] is False
    assert not any(message.get("content") == "observation_only"
                   for message in loop.llm.calls[-1]["messages"])


def test_dynamic_policy_state_round_trips():
    policy = DynamicInterventionPolicy("dynamic_test", lambda context, state: None, state={"rounds": 2})
    snapshot = policy.snapshot()
    restored = DynamicInterventionPolicy("dynamic_test", lambda context, state: None)
    restored.restore(snapshot)
    assert restored.snapshot() == {"state": {"rounds": 2}}


def test_context_exposes_only_tool_round_facts_and_decision_schema():
    c = call()
    failed = ToolResult("id", "run_command", False, "truncated", truncated=True)
    context = InterventionContext(request=2, step=1, calls=(c,), results=(failed,))
    assert context.tool_count == 1
    assert context.success_count == 0
    assert context.failure_count == 1
    assert context.truncated_count == 1
    assert not hasattr(context, "gold")
    assert not hasattr(context, "grader")
    decision = InterventionDecision(prompt=None, trigger="observed")
    assert decision.as_dict()["schema"] == "intervention_decision.v1"


def test_showcase_policy_does_not_claim_failed_read_found_target():
    policy = showcase_policy(idle_rounds=1)
    failed = ToolResult("id", "read_file", False, "missing", truncated=True)
    assert policy.observe(InterventionContext(1, 0, (call("read_file"),), (failed,))) is None


def test_showcase_policy_handles_multi_tool_round_and_emits_candidate_after_idle():
    policy = showcase_policy(idle_rounds=1)
    read = ToolResult("id", "read_file", True, "source")
    assert policy.observe(InterventionContext(1, 0,
                                               (call("read_file"), call("run_command")),
                                               (read, read))) is None
    decision = policy.observe(InterventionContext(2, 1, (), ()))
    assert decision is not None
    assert decision.trigger == "candidate_no_edit_after_read"
    assert decision.metadata["candidate"] is True


async def test_text_intervention_is_reported_without_changing_context_events(tmp_path):
    loop, _ = make_loop(tmp_path, "result_explanation", [LLMResponse(text="done")])
    outcome = await loop.run("fixture")
    assert outcome.success
    assert outcome.intervention_events[0]["mode"] == "result_explanation"
    assert outcome.intervention_events[0]["prompt_injected"] is True
    assert outcome.context_events == []


async def test_repeat_intervention_has_structured_event_and_legacy_context_event(tmp_path):
    loop, _ = make_loop(tmp_path, "repeat_reminder", repeated_responses(3) + [LLMResponse(text="done")])
    outcome = await loop.run("fixture")
    assert outcome.success
    assert outcome.context_events[0]["event"] == "repeat_call_reminder"
    triggered = [e for e in outcome.intervention_events if e["event"] == "intervention_triggered"]
    assert len(triggered) == 1
    assert triggered[0]["trigger"] == "three_identical_single_tool_calls"


async def test_session_resume_preserves_once_only_reminder(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    directory = tmp_path / "session"
    expected = compatibility(workspace=workspace, code_id="fix13-test", config={}, backend="fixture")
    loop, _ = make_loop(workspace, "repeat_reminder", repeated_responses(3))

    class Crash(BaseException):
        pass

    with RunSession.create(directory, task_id="fixture", expected=expected) as session:
        append = session.append

        def interrupt(kind, payload, **metadata):
            append(kind, payload, **metadata)
            if kind == "context.prepared" and payload["step"] == 3:
                raise Crash()

        session.append = interrupt
        with pytest.raises(Crash):
            await session.run(loop, "fixture")
        assert session.state.loop["repeat_call"]["triggered"]["request"] == 3
    resumed, tool = make_loop(workspace, "repeat_reminder", repeated_responses(3) + [LLMResponse(text="done")])
    with RunSession.resume(directory, expected=expected) as session:
        assert (await session.run(resumed, "fixture")).success
    assert tool.executions == 3
    assert sum(m.get("content") == REPEAT_REMINDER for m in resumed.llm.calls[-1]["messages"]) == 1


def test_legacy_config_dump_omits_new_default():
    config = HarnessConfig.model_validate({"llm": {"base_url": "http://localhost"}})
    assert "tool_intervention" not in config.model_dump()["runtime"]


async def test_factory_profiles_route_intervention_to_only_assigned_group(tmp_path):
    from scripts.rebench.run_fix13 import PROFILES
    from src.harness.factory import build_agent, load_config

    prompts = {}
    for group, profile in PROFILES.items():
        config = load_config(profile)
        config["llm"].update(base_url="http://localhost", api_key="fixture")
        config["policy"]["command"]["execution_profile"] = "legacy_host"
        config["tracing"]["output_dir"] = str(tmp_path / group / "trace")
        agent = build_agent(config, tmp_path / group)
        try:
            expected = {"R": "repeat_reminder", "E": "result_explanation"}.get(group, "none")
            assert agent.tool_intervention == expected
            prompts[group] = agent.system_prompt
        finally:
            await agent.llm.aclose()
    assert prompts["U"] == prompts["R"] == prompts["UT"]
    assert prompts["E"] == prompts["U"] + "\n\n" + RESULT_EXPLANATION
