"""Versioned, adapter-neutral records for the evaluation front door.

This module is intentionally a compatibility layer.  Existing runners and
graders remain responsible for execution and scoring; these records preserve
their provenance while giving reports one stable envelope to consume.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


SCHEMA_VERSION = "eval.unified.v1"
_KINDS = frozenset({"mini", "repo_pytest", "swebench"})
_STATUSES = frozenset({"pass", "partial", "fail", "invalid_env", "error", "unknown"})


class UnifiedContractError(ValueError):
    """Raised when a record would lose important evaluation provenance."""


def canonical_json_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _text(value: Any, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise UnifiedContractError(f"{name} must be a non-empty string")
    return value


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise UnifiedContractError(f"{name} must be an object")
    return dict(value)


@dataclass(frozen=True)
class TaskSpec:
    """Public task identity shared by all adapters.

    Adapter-specific fields belong in ``metadata``.  This keeps the public
    envelope stable without pretending that pytest and SWE-bench have the
    same private grader contract.
    """

    task_id: str
    kind: str
    description: str = ""
    repo_id: str | None = None
    base_revision: str | None = None
    execution_profile: str = "host"
    grader_profile: str = "builtin"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _text(self.task_id, "task_id")
        if self.kind not in _KINDS:
            raise UnifiedContractError(f"unknown task kind: {self.kind!r}")
        if not isinstance(self.description, str):
            raise UnifiedContractError("description must be a string")
        _text(self.execution_profile, "execution_profile")
        _text(self.grader_profile, "grader_profile")
        if not isinstance(self.metadata, dict):
            raise UnifiedContractError("metadata must be an object")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TaskSpec":
        raw = dict(value)
        if "task_id" not in raw and "id" in raw:
            raw["task_id"] = raw.pop("id")
        return cls(
            task_id=raw["task_id"], kind=raw.get("kind", "mini"),
            description=raw.get("description", ""), repo_id=raw.get("repo_id"),
            base_revision=raw.get("base_revision", raw.get("base_commit")),
            execution_profile=raw.get("execution_profile", raw.get("workspace_profile", "host")),
            grader_profile=raw.get("grader_profile", "builtin"),
            metadata=_mapping(raw.get("metadata"), "metadata"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GraderSpec:
    """How an adapter scores a run, without embedding private test data."""

    kind: str
    version: str = "unknown"
    manifest_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _text(self.kind, "grader kind")
        _text(self.version, "grader version")
        if not isinstance(self.metadata, dict):
            raise UnifiedContractError("grader metadata must be an object")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RunSpec:
    """Reproducibility identity for one adapter invocation."""

    run_id: str
    task: TaskSpec
    adapter: str
    config_name: str = "default"
    model: str | None = None
    intervention_policy: str = "none"
    intervention_policy_version: str = "unknown"
    intervention_config_hash: str | None = None
    attempt: int = 1
    runner_version: str = "unknown"
    budget: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _text(self.run_id, "run_id")
        _text(self.adapter, "adapter")
        _text(self.config_name, "config_name")
        _text(self.intervention_policy, "intervention_policy")
        _text(self.intervention_policy_version, "intervention_policy_version")
        if self.intervention_config_hash is not None:
            _text(self.intervention_config_hash, "intervention_config_hash")
        _text(self.runner_version, "runner_version")
        if not isinstance(self.attempt, int) or self.attempt < 1:
            raise UnifiedContractError("attempt must be a positive integer")
        if not isinstance(self.budget, dict) or not isinstance(self.metadata, dict):
            raise UnifiedContractError("budget and metadata must be objects")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RunSpec":
        raw = dict(value)
        task = raw.get("task")
        if not isinstance(task, TaskSpec):
            task = TaskSpec.from_mapping(task or raw)
        return cls(
            run_id=raw["run_id"], task=task, adapter=raw.get("adapter", "legacy"),
            config_name=raw.get("config_name", "default"), model=raw.get("model"),
            intervention_policy=raw.get("intervention_policy", "none"),
            intervention_policy_version=raw.get("intervention_policy_version", "unknown"),
            intervention_config_hash=raw.get("intervention_config_hash"),
            attempt=raw.get("attempt", 1), runner_version=raw.get("runner_version", "unknown"),
            budget=_mapping(raw.get("budget"), "budget"), metadata=_mapping(raw.get("metadata"), "metadata"),
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        return value


@dataclass(frozen=True)
class RunReceipt:
    """Common report envelope; ``raw_receipt`` remains authoritative for details."""

    run: RunSpec
    grader: GraderSpec
    status: str
    success: bool | None = None
    partial: bool | None = None
    cost: dict[str, Any] = field(default_factory=dict)
    trace_ref: str | None = None
    raw_receipt: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    errors: tuple[str, ...] = ()
    schema: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema != SCHEMA_VERSION:
            raise UnifiedContractError(f"unsupported schema: {self.schema!r}")
        if self.status not in _STATUSES:
            raise UnifiedContractError(f"unknown run status: {self.status!r}")
        if not isinstance(self.cost, dict) or not isinstance(self.raw_receipt, dict):
            raise UnifiedContractError("cost and raw_receipt must be objects")
        if not isinstance(self.provenance, dict):
            raise UnifiedContractError("provenance must be an object")
        if not isinstance(self.errors, tuple) or any(not isinstance(x, str) for x in self.errors):
            raise UnifiedContractError("errors must be a tuple of strings")

    @property
    def receipt_hash(self) -> str:
        return canonical_json_hash(self.to_dict(include_hash=False))

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = asdict(self)
        value["errors"] = list(self.errors)
        if include_hash:
            value["receipt_hash"] = canonical_json_hash(value)
        return value


def _status_from_legacy(raw: Mapping[str, Any]) -> str:
    status = raw.get("status")
    if status in _STATUSES:
        return str(status)
    if raw.get("pass") is True or raw.get("passed") is True or raw.get("solved") is True:
        return "pass"
    if raw.get("partial") is True or raw.get("status") == "partial":
        return "partial"
    if raw.get("infra_error") or raw.get("invalid_env"):
        return "invalid_env"
    if raw.get("fail") is True or raw.get("failed") is True:
        return "fail"
    return "unknown"


def _intervention_from_raw(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    """Normalize runtime intervention evidence into a compact receipt field."""
    direct = raw.get("intervention")
    if isinstance(direct, Mapping):
        return dict(direct)
    events = raw.get("intervention_events")
    if not isinstance(events, list):
        return None
    configured = next((item for item in events
                       if isinstance(item, Mapping)
                       and item.get("event") == "intervention_configured"), {})
    triggered = [item for item in events
                 if isinstance(item, Mapping)
                 and item.get("event") == "intervention_triggered"]
    return {
        "schema": "intervention_run.v1",
        "policy": configured.get("mode", "none"),
        "policy_version": configured.get("policy_version", "unknown"),
        "config_hash": configured.get("config_hash"),
        "trigger_count": len(triggered),
        "prompt_injection_count": sum(1 for item in triggered
                                       if item.get("prompt_injected") is True),
        "resumed": bool(raw.get("intervention_resumed", False)),
        "events": [
            {
                "trigger": item.get("trigger"),
                "prompt_injected": bool(item.get("prompt_injected")),
                "metadata": dict(item.get("metadata") or {}),
            }
            for item in triggered
        ],
    }


def normalize_legacy_result(
    raw: Mapping[str, Any],
    *,
    task: TaskSpec | Mapping[str, Any],
    run_id: str,
    adapter: str,
    grader_kind: str,
    grader_version: str = "legacy",
    config_name: str = "default",
    model: str | None = None,
    intervention_policy: str = "none",
    intervention_policy_version: str = "unknown",
    intervention_config_hash: str | None = None,
    runner_version: str = "legacy",
    provenance: Mapping[str, Any] | None = None,
) -> RunReceipt:
    """Wrap an old result without changing or reinterpreting its raw fields."""
    if not isinstance(raw, Mapping):
        raise UnifiedContractError("legacy result must be an object")
    task_spec = task if isinstance(task, TaskSpec) else TaskSpec.from_mapping(task)
    status = _status_from_legacy(raw)
    raw_intervention = _intervention_from_raw(raw)
    if raw_intervention is None:
        raw_intervention = raw.get("intervention_provenance")
    if isinstance(raw_intervention, Mapping):
        intervention_policy = str(raw_intervention.get("policy", intervention_policy))
        intervention_policy_version = str(raw_intervention.get(
            "policy_version", intervention_policy_version
        ))
        intervention_config_hash = raw_intervention.get(
            "config_hash", intervention_config_hash
        )
    run = RunSpec(
        run_id=run_id, task=task_spec, adapter=adapter, config_name=config_name,
        model=model, intervention_policy=intervention_policy,
        intervention_policy_version=intervention_policy_version,
        intervention_config_hash=intervention_config_hash,
        runner_version=runner_version, metadata={"legacy": True},
    )
    grader = GraderSpec(kind=grader_kind, version=grader_version)
    errors = []
    if status == "unknown":
        errors.append("legacy result has no recognized status field")
    receipt_provenance = dict(provenance or {})
    if isinstance(raw_intervention, Mapping):
        receipt_provenance.setdefault("intervention", dict(raw_intervention))
    return RunReceipt(
        run=run, grader=grader, status=status,
        success=(status == "pass") if status != "unknown" else None,
        partial=(status == "partial") if status != "unknown" else None,
        cost={k: raw[k] for k in ("cost_cny", "cost", "tokens", "model_calls") if k in raw},
        trace_ref=raw.get("trace_file") or raw.get("trace_ref"),
        raw_receipt=dict(raw), provenance=receipt_provenance, errors=tuple(errors),
    )
