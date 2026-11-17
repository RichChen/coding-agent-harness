"""Configurable tool-loop interventions used by the controlled evaluations.

The public constants and :class:`RepeatCallDetector` are kept compatible with
the FIX-13/FIX-15 runners.  The small spec helpers below provide a stable,
structured description for newer runners without changing the old prompts or
trigger rules.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol


INTERVENTION_SCHEMA = "tool_intervention.v1"
INTERVENTION_POLICY_SCHEMA = "intervention_policy.v1"
INTERVENTION_CONTEXT_SCHEMA = "intervention_context.v1"
INTERVENTION_DECISION_SCHEMA = "intervention_decision.v1"
INTERVENTION_POLICY_VERSION = "1"


REPEAT_REMINDER = (
    "The same tool call has occurred in the last three single-tool turns. "
    "If it is no longer adding useful information, consider a different next action. "
    "Repeating it is allowed when you have a concrete reason."
)
RESULT_EXPLANATION = (
    "Before each next tool-use turn, write one short sentence in visible assistant content stating "
    "what the previous tool results establish and how that informs your next action. Summarize the "
    "observation and decision, without a detailed reasoning trace. Keep the sentence within 40 words "
    "and return the tool calls in the same response. If the task is finished, give the usual final summary."
)
NEXT_ACTION = (
    "Before each next tool-use turn, write one short sentence in visible assistant content stating "
    "the next action you are about to take. Keep it within 40 words and return the tool calls in the "
    "same response. Do not summarize the previous result or provide a reasoning trace. If the task is "
    "finished, give the usual final summary."
)


@dataclass(frozen=True)
class InterventionSpec:
    """Versioned description of one intervention mode.

    ``prompt`` is intentionally the existing literal.  Keeping it here lets
    adapters report what was configured without reconstructing prompt text.
    """

    mode: str
    prompt: str | None = None
    trigger: str = "configured"
    schema: str = INTERVENTION_SCHEMA
    policy_version: str = INTERVENTION_POLICY_VERSION
    config: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, str) or not self.mode.strip():
            raise ValueError("intervention mode must be a non-empty string")
        if not isinstance(self.trigger, str) or not self.trigger.strip():
            raise ValueError("intervention trigger must be a non-empty string")
        if not isinstance(self.policy_version, str) or not self.policy_version.strip():
            raise ValueError("intervention policy_version must be a non-empty string")
        if self.config is not None and not isinstance(self.config, dict):
            raise ValueError("intervention config must be an object")

    @property
    def config_hash(self) -> str:
        payload = {
            "mode": self.mode,
            "prompt": self.prompt,
            "trigger": self.trigger,
            "policy_version": self.policy_version,
            "config": self.config or {},
        }
        return hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()

    @property
    def prompt_injected(self) -> bool:
        return self.prompt is not None

    def as_dict(self) -> dict:
        return {
            "schema": self.schema,
            "policy_schema": INTERVENTION_POLICY_SCHEMA,
            "mode": self.mode,
            "trigger": self.trigger,
            "policy_version": self.policy_version,
            "config_hash": self.config_hash,
            "config": dict(self.config or {}),
            "prompt_injected": self.prompt_injected,
            "prompt_chars": len(self.prompt) if self.prompt is not None else 0,
        }


def intervention_spec(mode: str) -> InterventionSpec:
    """Return the compatible spec for a configured intervention mode."""
    prompts = {"result_explanation": RESULT_EXPLANATION, "next_action": NEXT_ACTION}
    if mode == "none":
        return InterventionSpec(mode=mode)
    if mode == "repeat_reminder":
        return InterventionSpec(mode=mode, trigger="three_identical_single_tool_calls")
    if mode in prompts:
        return InterventionSpec(mode=mode, prompt=prompts[mode], trigger="system_prompt")
    raise ValueError("unknown tool intervention")


@dataclass(frozen=True, slots=True)
class InterventionContext:
    """Read-only facts exposed to a dynamic policy after one tool round.

    The context contains only the current conversation/tool-loop evidence.  A
    policy never receives the gold patch, grader output, or hidden task files.
    """

    request: int
    step: int
    calls: tuple[Any, ...] = ()
    results: tuple[Any, ...] = ()
    prior_events: tuple[dict, ...] = ()
    schema: str = INTERVENTION_CONTEXT_SCHEMA

    def __post_init__(self) -> None:
        if not isinstance(self.request, int) or self.request < 0:
            raise ValueError("intervention request must be a non-negative integer")
        if not isinstance(self.step, int) or self.step < 0:
            raise ValueError("intervention step must be a non-negative integer")
        if not isinstance(self.calls, tuple) or not isinstance(self.results, tuple):
            raise ValueError("intervention calls/results must be tuples")
        if not isinstance(self.prior_events, tuple):
            raise ValueError("intervention prior_events must be a tuple")

    @property
    def tool_count(self) -> int:
        return len(self.calls)

    @property
    def success_count(self) -> int:
        return sum(1 for item in self.results if bool(getattr(item, "success", False)))

    @property
    def failure_count(self) -> int:
        return sum(1 for item in self.results if not bool(getattr(item, "success", False)))

    @property
    def truncated_count(self) -> int:
        return sum(1 for item in self.results if bool(getattr(item, "truncated", False)))


@dataclass(frozen=True)
class InterventionDecision:
    """A policy's serializable decision for one observed tool round."""

    prompt: str | None
    trigger: str
    metadata: dict[str, Any] | None = None
    schema: str = INTERVENTION_DECISION_SCHEMA

    def __post_init__(self) -> None:
        if self.prompt is not None and not isinstance(self.prompt, str):
            raise ValueError("intervention prompt must be a string or None")
        if not isinstance(self.trigger, str) or not self.trigger.strip():
            raise ValueError("intervention decision trigger must be a non-empty string")
        if self.metadata is not None and not isinstance(self.metadata, dict):
            raise ValueError("intervention decision metadata must be an object")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "trigger": self.trigger,
            "prompt_injected": self.prompt is not None,
            "metadata": dict(self.metadata or {}),
        }


class InterventionPolicy(Protocol):
    """Protocol for stateful, resumable, tool-loop interventions."""

    spec: InterventionSpec

    def initial_prompt(self) -> str | None:
        ...

    def observe(self, context: InterventionContext) -> InterventionDecision | None:
        ...

    def snapshot(self) -> dict[str, Any]:
        ...

    def restore(self, snapshot: dict[str, Any] | None) -> None:
        ...


class ConfiguredInterventionPolicy:
    """Built-in policy implementation preserving FIX-13/FIX-15 behavior."""

    def __init__(self, spec: InterventionSpec, detector: "RepeatCallDetector | None" = None):
        self.spec = spec
        self.detector = detector

    def initial_prompt(self) -> str | None:
        return self.spec.prompt

    def observe(self, context: InterventionContext) -> InterventionDecision | None:
        if self.detector is None:
            return None
        trigger = self.detector.observe(context.calls, request=context.request)
        if trigger is None:
            return None
        return InterventionDecision(
            prompt=REPEAT_REMINDER,
            trigger=self.spec.trigger,
            metadata=trigger,
        )

    def snapshot(self) -> dict[str, Any]:
        return {"repeat_call": self.detector.snapshot()} if self.detector is not None else {}

    def restore(self, snapshot: dict[str, Any] | None) -> None:
        if self.detector is None or not snapshot:
            return
        state = snapshot.get("repeat_call", snapshot)
        if isinstance(state, dict):
            self.detector = RepeatCallDetector(**state)


class DynamicInterventionPolicy:
    """Adapter for a caller-supplied pure/stateful policy callback.

    ``decide`` may return an :class:`InterventionDecision`, a prompt string, or
    ``None``.  The callback receives only :class:`InterventionContext`; state is
    supplied through ``snapshot``/``restore`` so session resume is explicit.
    """

    def __init__(self, mode: str, decide, *, prompt: str | None = None,
                 state: dict[str, Any] | None = None, trigger: str = "dynamic",
                 policy_version: str = INTERVENTION_POLICY_VERSION,
                 config: dict[str, Any] | None = None):
        if not callable(decide):
            raise TypeError("dynamic intervention decide must be callable")
        self.spec = InterventionSpec(mode=mode, prompt=prompt, trigger=trigger,
                                     policy_version=policy_version, config=config)
        self._decide = decide
        self._state = dict(state or {})

    def initial_prompt(self) -> str | None:
        return self.spec.prompt

    def observe(self, context: InterventionContext) -> InterventionDecision | None:
        result = self._decide(context, self._state)
        if result is None:
            return None
        if isinstance(result, InterventionDecision):
            return result
        if isinstance(result, str):
            return InterventionDecision(prompt=result, trigger=self.spec.trigger)
        raise TypeError("dynamic intervention decide must return InterventionDecision, str, or None")

    def snapshot(self) -> dict[str, Any]:
        return {"state": dict(self._state)}

    def restore(self, snapshot: dict[str, Any] | None) -> None:
        if isinstance(snapshot, dict) and isinstance(snapshot.get("state"), dict):
            self._state = dict(snapshot["state"])


def build_intervention_policy(value: str | InterventionPolicy) -> InterventionPolicy:
    """Build a compatible policy from a legacy mode or a policy object."""
    if not isinstance(value, str):
        for name in ("initial_prompt", "observe", "snapshot", "restore", "spec"):
            if not hasattr(value, name):
                raise TypeError(f"intervention policy missing {name}")
        return value
    spec = intervention_spec(value)
    detector = RepeatCallDetector() if value == "repeat_reminder" else None
    return ConfiguredInterventionPolicy(spec, detector)


@dataclass
class RepeatCallDetector:
    call_key: str | None = None
    streak: int = 0
    triggered: dict | None = None

    def observe(self, calls, *, request: int) -> dict | None:
        if self.triggered is not None:
            return None
        if len(calls) != 1 or calls[0].parse_error or not isinstance(calls[0].arguments, dict):
            self._reset()
            return None
        call = calls[0]
        try:
            key = json.dumps([call.name, call.arguments], sort_keys=True, ensure_ascii=False, allow_nan=False)
        except (ValueError, TypeError):
            self._reset()
            return None
        self.streak = self.streak + 1 if key == self.call_key else 1
        self.call_key = key
        if self.streak == 3:
            self.triggered = dict(request=request, call_key=key)
            return dict(self.triggered)
        return None

    def _reset(self):
        self.call_key, self.streak = None, 0

    def snapshot(self):
        return dict(call_key=self.call_key, streak=self.streak, triggered=self.triggered)
