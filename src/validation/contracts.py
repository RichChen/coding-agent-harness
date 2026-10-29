"""Public-only validation DTOs. No executor, grader, or runtime imports."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from types import MappingProxyType
from typing import Any, Protocol

from src.workspace.contracts import (ArtifactRef, ContractError, FileEntry, canonical_json,
                                     content_id, tree_id, validate_path, validation_key)

PROTOCOL = "public-pytest-v1"
PARSER_ID = "public-parser-v1"
ARGV_TEMPLATE = ("{python}", "-I", "-B", "{runner}", "{request}", "{report}")
PYTEST_ARGS = ("-q", "-p", "no:cacheprovider", "-o", "addopts=", "--basetemp={temp}")
PROFILES = {"pytest8-v1": "8.", "pytest45-v1": "4.5.", "pytest9-fixture-v1": "9."}
FORBIDDEN = {"private", "private_source_binding", "test_overlay", "test_patch", "gold_patch",
             "patch", "fail_to_pass", "pass_to_pass", "f2p", "p2p", "grader", "grader_spec"}
ENV_KEYS = {"PATH", "LANG", "HOME", "TMPDIR", "SYSTEMROOT", "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
            "PYTHONPATH", "PYTHONDONTWRITEBYTECODE"}


def plain(value):
    if is_dataclass(value):
        return {f.name: plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(v) for v in value]
    return value


def freeze(value):
    # Enforce shared numeric/UTF-8 semantics before copying into immutable containers.
    canonical_json(value)
    if isinstance(value, Mapping):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(freeze(v) for v in value)
    return value


def public_only(value):
    if isinstance(value, Mapping):
        for k, v in value.items():
            if k.lower() in FORBIDDEN or k.lower().startswith("private_"):
                raise ContractError("private evaluation fields are not public validation inputs")
            public_only(v)
    elif isinstance(value, (tuple, list)):
        for v in value:
            public_only(v)


def identity(value, domain):
    if (not isinstance(value, str) or not value.startswith(domain + ":")
            or len(value) != len(domain) + 65
            or any(c not in "0123456789abcdef" for c in value.split(":")[-1])):
        raise ContractError("invalid " + domain + " identity")


@dataclass(frozen=True)
class ValidationEnvironment:
    image_digest: str
    image_id: str
    architecture: str
    python_bin: str
    python_version: str
    pytest_version: str
    dependency_fingerprint: str
    env: Mapping[str, str]
    profile: str
    resources: Mapping[str, Any]
    initialization_version: str = "replica-v1"
    cwd: str = "/workspace"

    def __post_init__(self):
        identity(self.image_digest, "sha256")
        identity(self.image_id, "sha256")
        if self.cwd != "/workspace" or self.profile not in ("docker_linux", "local_fixture"):
            raise ContractError("unsupported validation environment")
        if not self.python_bin or not self.python_version or not self.pytest_version:
            raise ContractError("interpreter versions must be explicit")
        if not self.dependency_fingerprint or not self.architecture or not self.initialization_version:
            raise ContractError("environment fingerprint incomplete")
        if set(self.env) - ENV_KEYS or any(not isinstance(v, str) or "\0" in v for v in self.env.values()):
            raise ContractError("environment must use explicit nonsecret allowlist")
        required = {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONPATH": "/workspace:/workspace/src"}
        if any(self.env.get(k) != v for k, v in required.items()):
            raise ContractError("required deterministic pytest environment missing")
        object.__setattr__(self, "env", freeze(self.env))
        object.__setattr__(self, "resources", freeze(self.resources))

    @property
    def environment_id(self):
        return content_id("environment-v1", plain(self))


@dataclass(frozen=True)
class ValidationLimits:
    test_seconds: int = 90
    total_seconds: int = 162
    max_output_bytes: int = 1048576  # per stream
    max_report_bytes: int = 1048576
    max_nodes: int = 10000
    max_attestation_bytes: int = 8192

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in plain(self).values()):
            raise ContractError("validation limits must be positive integers")
        if self.total_seconds < self.test_seconds:
            raise ContractError("total phase budget is smaller than test budget")

    @property
    def evidence_bytes(self):
        return self.max_output_bytes * 2 + self.max_report_bytes + self.max_attestation_bytes


@dataclass(frozen=True)
class PublicValidationSpec:
    provenance: Mapping[str, Any]
    protected_manifest: tuple[FileEntry, ...]
    selectors: tuple[str, ...]
    runner_id: str
    adapter: str = "pytest8-v1"
    parser_id: str = PARSER_ID
    coverage: str = "regression_only"
    scope: str = "declared public regression checks only"
    target_evidence: Mapping[str, Any] | None = None
    known_fixture_failures: tuple[str, ...] = ()
    imports: tuple[str, ...] = ()
    runner_is_candidate: bool = False
    limits: ValidationLimits = ValidationLimits()
    schema_version: str = "public-check-v1"
    argv_template: tuple[str, ...] = ARGV_TEMPLATE
    pytest_args: tuple[str, ...] = PYTEST_ARGS
    policy_version: str = "public-policy-v1"

    def __post_init__(self):
        if (tuple(self.argv_template) != ARGV_TEMPLATE or tuple(self.pytest_args) != PYTEST_ARGS
                or self.policy_version != "public-policy-v1"):
            raise ContractError("public-v1 only accepts the fixed host-owned argv policy")
        object.__setattr__(self, "argv_template", tuple(self.argv_template))
        object.__setattr__(self, "pytest_args", tuple(self.pytest_args))
        public_only(self.provenance)
        public_only(self.target_evidence)
        if set(self.provenance) - {"repo", "commit", "paths", "issue_source", "frozen_at"}:
            raise ContractError("unknown public provenance field")
        if not all(self.provenance.get(k) for k in ("repo", "commit", "paths", "frozen_at")):
            raise ContractError("public provenance incomplete")
        paths = self.provenance["paths"]
        if not isinstance(paths, (list, tuple)) or not paths:
            raise ContractError("public provenance paths must be explicit")
        for path in paths: validate_path(path)
        if len(set(self.known_fixture_failures)) != len(self.known_fixture_failures):
            raise ContractError("duplicate classified fixture failure")
        if "pytest" in self.imports:
            raise ContractError("pytest import policy is set by runner_is_candidate")
        if self.schema_version != "public-check-v1" or self.parser_id != PARSER_ID or self.adapter not in PROFILES:
            raise ContractError("unsupported public runner/parser protocol")
        identity(self.runner_id, "runner-v1")
        if self.coverage not in ("regression_only", "fixture") or not self.scope:
            raise ContractError("unsupported coverage")
        if self.coverage == "regression_only" and (self.target_evidence is not None or self.known_fixture_failures):
            raise ContractError("regression checks cannot claim target evidence or known target failures")
        if self.coverage == "fixture" and not self.target_evidence:
            raise ContractError("fixture requires explicit target provenance")
        manifest = tuple(sorted(self.protected_manifest, key=lambda e: e.path.encode("utf-8")))
        tree_id(manifest)
        protected = {e.path for e in manifest}
        if not self.selectors or len(set(self.selectors)) != len(self.selectors):
            raise ContractError("nonempty unique selectors required")
        for selector in self.selectors:
            path = selector.split("::")[0]
            validate_path(path)
            if path.startswith("-") or not path.endswith(".py") or path not in protected:
                raise ContractError("selector must name a frozen public Python test")
        if type(self.runner_is_candidate) is not bool or not isinstance(self.limits, ValidationLimits):
            raise ContractError("invalid validation policy")
        if self.adapter == "pytest9-fixture-v1" and self.coverage != "fixture":
            raise ContractError("pytest9 adapter is fixture-only")
        if any(not isinstance(m, str) or not all(p.isidentifier() for p in m.split(".")) for m in self.imports):
            raise ContractError("imports must be module names")
        object.__setattr__(self, "protected_manifest", manifest)
        for name in ("selectors", "imports", "known_fixture_failures"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        object.__setattr__(self, "provenance", freeze(self.provenance))
        object.__setattr__(self, "target_evidence", freeze(self.target_evidence))

    @property
    def spec_id(self):
        return content_id("public-spec-v1", plain(self))


@dataclass(frozen=True)
class BaselineBinding:
    spec_id: str
    environment_id: str
    base_manifest_id: str
    base_candidate_id: str
    nodeids: tuple[str, ...]
    outcomes: tuple[Mapping[str, Any], ...]
    record_id: str

    def __post_init__(self):
        for name, domain in (("spec_id", "public-spec-v1"), ("environment_id", "environment-v1"),
                             ("base_manifest_id", "tree-v1"), ("base_candidate_id", "candidate-v1"),
                             ("record_id", "validation-record-v1")):
            identity(getattr(self, name), domain)
        if not self.nodeids or len(set(self.nodeids)) != len(self.nodeids):
            raise ContractError("baseline requires nonempty unique collection")
        object.__setattr__(self, "nodeids", tuple(sorted(self.nodeids)))
        object.__setattr__(self, "outcomes", tuple(freeze(v) for v in self.outcomes))

    @property
    def check_id(self):
        # Receipt ID is audit only: it contains the provisional baseline check ID.
        return content_id("check-v1", {k: v for k, v in plain(self).items() if k != "record_id"})


@dataclass(frozen=True)
class ValidationAttempt:
    operation_id: str
    trigger: str
    reason: str
    candidate_id: str | None = None
    check_id: str | None = None
    environment_id: str | None = None
    budget_receipt: Mapping[str, Any] | None = None
    evidence: tuple[ArtifactRef, ...] = ()
    status: str = "not_run"

    def __post_init__(self):
        if not self.operation_id or self.trigger not in ("baseline", "explicit_tool", "natural_end", "hard_stop"):
            raise ContractError("invalid validation operation")
        if self.status != "not_run":
            raise ContractError("attempt without execution must be not_run")
        for name, domain in (("candidate_id", "candidate-v1"), ("check_id", "check-v1"),
                             ("environment_id", "environment-v1")):
            if getattr(self, name) is not None:
                identity(getattr(self, name), domain)
        object.__setattr__(self, "budget_receipt", freeze(self.budget_receipt or {}))
        object.__setattr__(self, "evidence", tuple(self.evidence))

    def to_dict(self):
        return plain(self)


@dataclass(frozen=True)
class ValidationRecord:
    operation_id: str
    trigger: str
    candidate_id: str
    check_id: str
    environment_id: str
    status: str
    reason: str
    execution_status: str
    exit_code: int | None
    counts: Mapping[str, int]
    node_report: ArtifactRef | None
    evidence: tuple[ArtifactRef, ...]
    complete: bool
    cleanup_state: str
    started_at: str | None
    finished_at: str
    budget_receipt: Mapping[str, Any]
    coverage: Mapping[str, Any]

    def __post_init__(self):
        ValidationAttempt(self.operation_id, self.trigger, self.reason, self.candidate_id,
                          self.check_id, self.environment_id)
        if (not self.reason or not self.finished_at or self.exit_code is not None and type(self.exit_code) is not int
                or self.execution_status not in ("succeeded", "failed", "cancelled", "timed_out", "blocked", "outcome_unknown")):
            raise ContractError("invalid execution metadata")
        if self.complete and not self.started_at:
            raise ContractError("complete execution requires a start record")
        if self.status not in ("passed", "behavior_failed", "inconclusive"):
            raise ContractError("invalid executed validation status")
        if self.status in ("passed", "behavior_failed") and (
                not self.complete or self.cleanup_state != "cleaned" or self.node_report is None):
            raise ContractError("effective result requires complete persisted evidence and cleanup")
        if self.status == "passed" and (self.execution_status != "succeeded" or self.exit_code != 0):
            raise ContractError("passed requires successful real process")
        required_counts = {"collected", "passed", "failed", "errors", "skipped", "xfailed", "xpassed"}
        if (set(self.counts) != required_counts or any(type(v) is not int or v < 0 for v in self.counts.values())
                or any(not isinstance(ref, ArtifactRef) for ref in self.evidence)
                or self.node_report is not None and self.node_report not in self.evidence):
            raise ContractError("invalid validation counts/evidence")
        if self.status == "passed" and (self.counts["passed"] < 1 or any(self.counts[k] for k in
                ("failed", "errors", "skipped", "xfailed", "xpassed"))):
            raise ContractError("passed requires effective, unskipped test bodies")
        if self.status == "behavior_failed" and (self.execution_status != "failed" or self.exit_code != 1
                or self.counts["failed"] < 1):
            raise ContractError("behavior failure requires observed real test failure")
        if self.complete and self.counts["collected"] != self.counts["passed"] + self.counts["failed"]:
            raise ContractError("complete result counts mismatch")
        for name in ("counts", "budget_receipt", "coverage"):
            object.__setattr__(self, name, freeze(getattr(self, name)))
        object.__setattr__(self, "evidence", tuple(self.evidence))

    @property
    def record_id(self):
        return content_id("validation-record-v1", plain(self))

    @property
    def key(self):
        return validation_key(candidate_id=self.candidate_id, check_id=self.check_id,
                              environment_id=self.environment_id)

    def to_dict(self):
        return dict(record_id=self.record_id, **plain(self))

    @classmethod
    def from_dict(cls, value):
        if set(value) != {f.name for f in fields(cls)} | {"record_id"}:
            raise ContractError("invalid persisted validation record schema")
        data = dict(value)
        expected = data.pop("record_id")
        data["node_report"] = ArtifactRef.from_dict(data["node_report"]) if data["node_report"] else None
        data["evidence"] = tuple(ArtifactRef.from_dict(v) for v in data["evidence"])
        result = cls(**data)
        if result.record_id != expected:
            raise ContractError("persisted validation record identity mismatch")
        return result


class ArtifactWriter(Protocol):
    def publish(self, data: bytes) -> ArtifactRef:
        """Return only after durable publication, with verified length/hash."""
        ...


class EventSink(Protocol):
    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        """Atomically persist payload (including result + budget), or raise."""
        ...
