"""Small, explainable no-model intervention policies for local validation.

These policies observe only the current tool-loop context.  They are fixtures
for checking the harness boundary and event evidence; they do not encode task
answers or claim that a read result found the target.
"""
from __future__ import annotations

from typing import Any

from .tool_intervention import (
    DynamicInterventionPolicy,
    InterventionContext,
    InterventionDecision,
)


_READ_NAMES = ("read", "search", "find", "grep", "list", "cat", "glob")
_EDIT_NAMES = ("write", "edit", "patch", "replace", "delete", "move", "mkdir", "create")


def _tool_name(call: Any) -> str:
    return str(getattr(call, "name", "")).lower()


def _successful(context: InterventionContext, index: int) -> bool:
    return index < len(context.results) and bool(getattr(context.results[index], "success", False))


def showcase_policy(*, idle_rounds: int = 2) -> DynamicInterventionPolicy:
    """Build an explainable policy used by tests and the E-line showcase.

    It emits a candidate reminder only after a successful read/search round is
    followed by ``idle_rounds`` rounds with no edit call.  A failed read never
    produces a "found target" claim, and a multi-tool round is not treated as a
    single repeated call.
    """
    if not isinstance(idle_rounds, int) or idle_rounds < 1:
        raise ValueError("idle_rounds must be a positive integer")

    def decide(context: InterventionContext, state: dict[str, Any]):
        state.setdefault("idle_rounds", idle_rounds)
        state.setdefault("read_observed", False)
        state.setdefault("idle", 0)
        state.setdefault("triggered", False)

        if len(context.calls) != len(context.results):
            return None

        names = [_tool_name(call) for call in context.calls]
        edits = any(any(token in name for token in _EDIT_NAMES)
                    for name in names)
        reads = any(any(token in name for token in _READ_NAMES)
                    and _successful(context, index)
                    for index, name in enumerate(names))

        if edits:
            state["read_observed"] = False
            state["idle"] = 0
            state["triggered"] = False
            return None
        if reads:
            state["read_observed"] = True
            state["idle"] = 0
            state["triggered"] = False
            return None
        if not state["read_observed"] or state["triggered"]:
            return None

        state["idle"] += 1
        if state["idle"] < state["idle_rounds"]:
            return None
        state["triggered"] = True
        return InterventionDecision(
            prompt=(
                "A successful read/search has been followed by tool rounds without an edit. "
                "Reassess the next concrete action; this reminder does not establish correctness."
            ),
            trigger="candidate_no_edit_after_read",
            metadata={
                "observation": "successful_read_followed_by_no_edit",
                "candidate": True,
                "rounds_without_edit": state["idle"],
                "tool_count": context.tool_count,
                "failure_count": context.failure_count,
                "truncated_count": context.truncated_count,
            },
        )

    return DynamicInterventionPolicy(
        "showcase_trajectory",
        decide,
        trigger="trajectory_observation",
        config={"idle_rounds": idle_rounds, "read_names": _READ_NAMES, "edit_names": _EDIT_NAMES},
    )
