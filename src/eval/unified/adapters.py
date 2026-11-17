"""Adapter facade for the unified evaluation front door.

It validates explicit manifest entries, resolves named adapters, and preserves
the existing runner/grader as the owner of execution and scoring.  The shared
layer adds only the stable receipt envelope and provenance fields.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol

from .contracts import (
    GraderSpec,
    RunReceipt,
    TaskSpec,
    UnifiedContractError,
    normalize_legacy_result,
)


ADAPTER_SCHEMA = "eval.adapter.v1"
ADAPTER_KINDS = frozenset({"mini", "repo_pytest", "swebench"})


class AdapterError(UnifiedContractError):
    """Raised when an adapter boundary is missing or inconsistent."""


def _intervention_kwargs(entry: "ManifestEntry") -> dict[str, Any]:
    metadata = entry.metadata
    return {
        "intervention_policy": str(metadata.get("intervention_policy", "none")),
        "intervention_policy_version": str(metadata.get("intervention_policy_version", "unknown")),
        "intervention_config_hash": metadata.get("intervention_config_hash"),
    }


def load_manifest_rows(path: str | Path) -> list[dict[str, Any]]:
    """Load an explicit JSON/YAML manifest without selecting an executor.

    A manifest is either a list of entries or an object with ``entries`` (the
    preferred spelling) or ``tasks``.  The loader validates only the container
    shape; each entry is validated by :meth:`ManifestEntry.from_mapping`.
    """
    source = Path(path)
    suffix = source.suffix.lower()
    text = source.read_text(encoding="utf-8")
    if suffix == ".json":
        payload = json.loads(text)
    elif suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - declared dependency
            raise AdapterError("YAML manifests require PyYAML") from exc
        payload = yaml.safe_load(text)
    else:
        raise AdapterError("manifest must use .json, .yaml, or .yml")
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, Mapping):
        rows = payload.get("entries", payload.get("tasks"))
    else:
        rows = None
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        raise AdapterError("manifest must be a list or an object with entries/tasks")
    return [dict(row) for row in rows]


@dataclass(frozen=True)
class ManifestEntry:
    """One explicit task entry for the future unified front door."""

    task: TaskSpec
    adapter: str
    grader: GraderSpec
    config_name: str = "default"
    metadata: dict[str, Any] = field(default_factory=dict)
    schema: str = ADAPTER_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != ADAPTER_SCHEMA:
            raise AdapterError(f"unsupported adapter schema: {self.schema!r}")
        if self.adapter not in ADAPTER_KINDS:
            raise AdapterError(f"unknown adapter: {self.adapter!r}")
        if self.task.kind != self.adapter:
            raise AdapterError(
                f"task kind {self.task.kind!r} does not match adapter {self.adapter!r}"
            )
        if not isinstance(self.config_name, str) or not self.config_name.strip():
            raise AdapterError("config_name must be a non-empty string")
        if not isinstance(self.metadata, dict):
            raise AdapterError("manifest metadata must be an object")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ManifestEntry":
        if not isinstance(value, Mapping):
            raise AdapterError("manifest entry must be an object")
        raw = dict(value)
        task_raw = raw.get("task", raw)
        if not isinstance(task_raw, Mapping):
            raise AdapterError("manifest entry task must be an object")
        task = TaskSpec.from_mapping(task_raw)
        adapter = str(raw.get("adapter", task.kind))
        grader_raw = raw.get("grader", {})
        if isinstance(grader_raw, str):
            grader = GraderSpec(kind=grader_raw)
        elif isinstance(grader_raw, Mapping):
            grader = GraderSpec(
                kind=str(grader_raw.get("kind", task.grader_profile)),
                version=str(grader_raw.get("version", "unknown")),
                manifest_ref=grader_raw.get("manifest_ref"),
                metadata=dict(grader_raw.get("metadata", {})),
            )
        else:
            raise AdapterError("manifest grader must be a string or object")
        metadata = raw.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise AdapterError("manifest metadata must be an object")
        return cls(
            task=task,
            adapter=adapter,
            grader=grader,
            config_name=str(raw.get("config_name", "default")),
            metadata=dict(metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EvaluationAdapter(Protocol):
    """Boundary implemented by a real or mock execution adapter."""

    name: str

    def execute(
        self,
        entry: ManifestEntry,
        *,
        run_id: str,
        raw_result: Mapping[str, Any] | None = None,
    ) -> RunReceipt:
        """Return a receipt; real implementations own their runner/grader."""


class MockAdapter:
    """No-model adapter used for contract tests and showcase smoke."""

    def __init__(self, name: str):
        if name not in ADAPTER_KINDS:
            raise AdapterError(f"unknown adapter: {name!r}")
        self.name = name

    def execute(
        self,
        entry: ManifestEntry,
        *,
        run_id: str,
        raw_result: Mapping[str, Any] | None = None,
    ) -> RunReceipt:
        if entry.adapter != self.name:
            raise AdapterError(
                f"entry adapter {entry.adapter!r} does not match {self.name!r}"
            )
        return normalize_legacy_result(
            raw_result or {"status": "unknown"},
            task=entry.task,
            run_id=run_id,
            adapter=self.name,
            grader_kind=entry.grader.kind,
            grader_version=entry.grader.version,
            config_name=entry.config_name,
            **_intervention_kwargs(entry),
            provenance={
                "adapter_schema": ADAPTER_SCHEMA,
                "execution": "mock",
                "manifest_entry": entry.task.task_id,
            },
        )


class MiniAdapter:
    """Explicit real mini-task adapter backed by the existing ``EvalRunner``.

    The adapter intentionally accepts an injected factory.  The unified layer
    therefore does not decide which model, config, or network endpoint to
    use, and tests can provide a MockLLM-backed factory without any paid call.
    Repository and SWE-bench entries are rejected by ``ManifestEntry`` before
    this adapter is invoked.
    """

    name = "mini"

    def __init__(self, agent_factory, *, config: Mapping[str, Any] | None = None,
                 judge_fn=None, dataset_path: str | Path = "datasets/mini_tasks.json"):
        if not callable(agent_factory):
            raise AdapterError("MiniAdapter requires an agent_factory callable")
        self.agent_factory = agent_factory
        self.config = dict(config or {})
        self.judge_fn = judge_fn
        self.dataset_path = str(dataset_path)

    async def execute_async(self, entry: ManifestEntry, *, run_id: str) -> RunReceipt:
        if entry.adapter != self.name:
            raise AdapterError(f"entry adapter {entry.adapter!r} does not match 'mini'")
        from src.eval.runner import EvalRunner

        payload = dict(entry.task.metadata)
        payload.setdefault("id", entry.task.task_id)
        payload.setdefault("description", entry.task.description)
        if "expected" not in payload:
            raise AdapterError("mini task metadata must include expected")
        config = dict(self.config)
        config.update(entry.metadata.get("config", {}))
        runner = EvalRunner(self.agent_factory, self.dataset_path)
        task_result = await runner._run_task(
            entry.config_name, config, payload, self.judge_fn
        )
        passed = bool(task_result.success and task_result.expected_check)
        raw = asdict(task_result)
        raw.update({"status": "pass" if passed else "fail", "passed": passed})
        return normalize_legacy_result(
            raw,
            task=entry.task,
            run_id=run_id,
            adapter=self.name,
            grader_kind=entry.grader.kind,
            grader_version=entry.grader.version,
            config_name=entry.config_name,
            **_intervention_kwargs(entry),
            provenance={
                "adapter_schema": ADAPTER_SCHEMA,
                "execution": "eval_runner",
                "dataset_path": self.dataset_path,
                "task_id": entry.task.task_id,
            },
        )

    def execute(self, entry: ManifestEntry, *, run_id: str,
                raw_result: Mapping[str, Any] | None = None) -> RunReceipt:
        if raw_result is not None:
            raise AdapterError("MiniAdapter does not accept raw_result; use MockAdapter for fixtures")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.execute_async(entry, run_id=run_id))
        raise AdapterError("MiniAdapter.execute cannot run inside an event loop; use execute_async")


class RepoPytestAdapter:
    """Real repository-pytest adapter around :class:`RepositoryGrader`.

    ``case_loader`` is the explicit boundary for private task inputs.  It must
    return ``base``, ``candidate`` and ``spec``; the unified layer never scans
    a repository or derives a grader specification from a task id.
    """

    name = "repo_pytest"

    def __init__(self, case_loader, *, grader=None):
        if not callable(case_loader):
            raise AdapterError("RepoPytestAdapter requires an explicit case_loader")
        if grader is None:
            from src.eval.repo_grader import RepositoryGrader
            grader = RepositoryGrader()
        if not callable(getattr(grader, "evaluate", None)):
            raise AdapterError("repo pytest grader must provide evaluate()")
        self.case_loader = case_loader
        self.grader = grader

    async def execute_async(self, entry: ManifestEntry, *, run_id: str) -> RunReceipt:
        if entry.adapter != self.name:
            raise AdapterError(f"entry adapter {entry.adapter!r} does not match 'repo_pytest'")
        case = self.case_loader(entry)
        if inspect.isawaitable(case):
            case = await case
        if not isinstance(case, Mapping):
            raise AdapterError("repo pytest case_loader must return an object")
        required = {"base", "candidate", "spec"}
        missing = sorted(required - set(case))
        if missing:
            raise AdapterError(f"repo pytest case is missing: {', '.join(missing)}")
        spec = case["spec"]
        if isinstance(spec, Mapping):
            from src.eval.repo_grader import RepositorySpec
            spec = RepositorySpec.from_mapping(spec)
        raw = await self.grader.evaluate(case["base"], case["candidate"], spec)
        if not isinstance(raw, Mapping):
            raise AdapterError("repo pytest grader must return an object")
        return normalize_legacy_result(
            raw,
            task=entry.task,
            run_id=run_id,
            adapter=self.name,
            grader_kind=entry.grader.kind,
            grader_version=entry.grader.version,
            config_name=entry.config_name,
            **_intervention_kwargs(entry),
            provenance={
                "adapter_schema": ADAPTER_SCHEMA,
                "execution": "repository_grader",
                "case_source": entry.metadata.get("case_source", "explicit_loader"),
            },
        )

    def execute(self, entry: ManifestEntry, *, run_id: str,
                raw_result: Mapping[str, Any] | None = None) -> RunReceipt:
        if raw_result is not None:
            raise AdapterError("RepoPytestAdapter does not accept raw_result; use MockAdapter for fixtures")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.execute_async(entry, run_id=run_id))
        raise AdapterError("RepoPytestAdapter.execute cannot run inside an event loop; use execute_async")


class SwebenchAdapter:
    """Real SWE-bench-style adapter around the existing ``ImageGrader``.

    The explicit case loader returns the keyword arguments accepted by
    ``ImageGrader.evaluate`` (at minimum ``image``, ``test_overlay``, ``f2p``
    and ``p2p``).  This preserves task-specific grading semantics and Docker
    provenance inside ``raw_receipt``.
    """

    name = "swebench"

    def __init__(self, case_loader, *, grader=None):
        if not callable(case_loader):
            raise AdapterError("SwebenchAdapter requires an explicit case_loader")
        if grader is None:
            from src.eval.image_grader import ImageGrader
            grader = ImageGrader()
        if not callable(getattr(grader, "evaluate", None)):
            raise AdapterError("SWE-bench grader must provide evaluate()")
        self.case_loader = case_loader
        self.grader = grader

    async def execute_async(self, entry: ManifestEntry, *, run_id: str) -> RunReceipt:
        if entry.adapter != self.name:
            raise AdapterError(f"entry adapter {entry.adapter!r} does not match 'swebench'")
        case = self.case_loader(entry)
        if inspect.isawaitable(case):
            case = await case
        if not isinstance(case, Mapping):
            raise AdapterError("SWE-bench case_loader must return an object")
        required = {"image", "test_overlay", "f2p", "p2p"}
        missing = sorted(required - set(case))
        if missing:
            raise AdapterError(f"SWE-bench case is missing: {', '.join(missing)}")
        allowed = {
            "image", "delta", "test_overlay", "f2p", "p2p", "django", "sympy",
            "timeout", "repo_root", "python_bin", "autoload_plugins", "pytest_options",
            "keep_original_files", "memory_bytes",
        }
        kwargs = {key: case[key] for key in allowed if key in case}
        raw = await self.grader.evaluate(**kwargs)
        if not isinstance(raw, Mapping):
            raise AdapterError("SWE-bench grader must return an object")
        return normalize_legacy_result(
            raw,
            task=entry.task,
            run_id=run_id,
            adapter=self.name,
            grader_kind=entry.grader.kind,
            grader_version=entry.grader.version,
            config_name=entry.config_name,
            **_intervention_kwargs(entry),
            provenance={
                "adapter_schema": ADAPTER_SCHEMA,
                "execution": "image_grader",
                "case_source": entry.metadata.get("case_source", "explicit_loader"),
            },
        )

    def execute(self, entry: ManifestEntry, *, run_id: str,
                raw_result: Mapping[str, Any] | None = None) -> RunReceipt:
        if raw_result is not None:
            raise AdapterError("SwebenchAdapter does not accept raw_result; use MockAdapter for fixtures")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.execute_async(entry, run_id=run_id))
        raise AdapterError("SwebenchAdapter.execute cannot run inside an event loop; use execute_async")


class AdapterRegistry:
    """Explicit registry; it never chooses a runner implicitly."""

    def __init__(self, adapters: Mapping[str, EvaluationAdapter] | None = None):
        self._adapters: dict[str, EvaluationAdapter] = {}
        for name, adapter in (adapters or {}).items():
            self.register(name, adapter)

    def register(self, name: str, adapter: EvaluationAdapter) -> None:
        if name not in ADAPTER_KINDS:
            raise AdapterError(f"unknown adapter: {name!r}")
        if getattr(adapter, "name", name) != name:
            raise AdapterError("registered adapter name does not match its object")
        self._adapters[name] = adapter

    def get(self, name: str) -> EvaluationAdapter:
        try:
            return self._adapters[name]
        except KeyError as exc:
            raise AdapterError(
                f"adapter {name!r} is not registered; real execution is not wired yet"
            ) from exc

    def execute(
        self,
        entry: ManifestEntry,
        *,
        run_id: str,
        raw_result: Mapping[str, Any] | None = None,
    ) -> RunReceipt:
        return self.get(entry.adapter).execute(
            entry, run_id=run_id, raw_result=raw_result
        )

    async def execute_async(
        self,
        entry: ManifestEntry,
        *,
        run_id: str,
        raw_result: Mapping[str, Any] | None = None,
    ) -> RunReceipt:
        """Async front door used by callers already inside an event loop."""
        adapter = self.get(entry.adapter)
        execute_async = getattr(adapter, "execute_async", None)
        if callable(execute_async):
            return await execute_async(entry, run_id=run_id)
        return adapter.execute(entry, run_id=run_id, raw_result=raw_result)

    async def execute_many_async(
        self,
        entries: list[ManifestEntry],
        *,
        run_id_factory=None,
    ) -> list[RunReceipt]:
        """Execute an explicit ordered manifest through registered adapters."""
        if run_id_factory is None:
            run_id_factory = lambda entry, index: f"{entry.task.task_id}/r{index}"
        receipts = []
        for index, entry in enumerate(entries, start=1):
            receipts.append(await self.execute_async(
                entry, run_id=run_id_factory(entry, index)
            ))
        return receipts
