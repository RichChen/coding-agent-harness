"""Public validation orchestration; injected execution, blobs, events and budgets."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable

from src.workspace.contracts import (
    BlobReader, ContractError, FileEntry, WorkspaceCapture, candidate_id, content_id,
    read_verified_blob, tree_id, validation_key, canonical_json,
)
from .contracts import (BaselineBinding, ValidationAttempt, ValidationRecord, PROFILES, plain,
                        PublicValidationSpec, ValidationEnvironment, ArtifactWriter, EventSink)
from .ledger import BudgetRefusal, ValidationLedger, ByteBudget
from .parser import classify
from .replica import RUNNER_ID, RUNNER_SHA256, ReplicaRequest, ReplicaResult, ReplicaExecutor


@dataclass(frozen=True)
class ValidationControl:
    deadline_mono: float
    ledger: ValidationLedger
    artifacts: ByteBudget
    cancelled: Callable[[], bool] = lambda: False
    clock: Callable[[], float] = time.monotonic
    finalization_reserve_s: int = 162
    cleanup_grace_s: int = 12

    def __post_init__(self):
        import math
        if (not math.isfinite(self.deadline_mono) or type(self.finalization_reserve_s) is not int
                or self.finalization_reserve_s < 0 or type(self.cleanup_grace_s) is not int
                or not 0 <= self.cleanup_grace_s <= 12):
            raise ContractError("invalid absolute deadline/finalization/cleanup limits")

    @property
    def agent_deadline(self):
        return self.deadline_mono - self.finalization_reserve_s


class ValidationService:
    def __init__(self, *, base_entries: Iterable[FileEntry], blobs: BlobReader,
                 artifacts: ArtifactWriter, executor: ReplicaExecutor, events: EventSink,
                 committed: Iterable[ValidationRecord] = (), pending: dict[str, str] | None = None):
        self.base_entries = tuple(base_entries)
        self.blobs, self.artifacts, self.executor, self.events = blobs, artifacts, executor, events
        self._operations, self._cache = {}, {}
        self._records_by_id = {}  # Durable historical records, never replaced by replay refusals.
        self._pending = dict(pending or {})  # operation_id -> validation key; never replay.
        self._lock = asyncio.Lock()
        self._unsettled = set()
        for record in committed:
            if not isinstance(record, ValidationRecord) or record.operation_id in self._operations:
                raise ContractError("invalid committed record set")
            self._operations[record.operation_id] = (record.key, record)
            self._cache[record.key] = record
            self._records_by_id[record.record_id] = record

    @staticmethod
    def _check_id(spec, environment, baseline):
        if baseline is None:
            return content_id("check-v1", dict(spec_id=spec.spec_id,
                environment_id=environment.environment_id, stage="baseline-unbound"))
        if baseline.spec_id != spec.spec_id or baseline.environment_id != environment.environment_id:
            raise ContractError("baseline spec/environment binding mismatch")
        return baseline.check_id

    async def validate(self, capture: WorkspaceCapture | None, spec: PublicValidationSpec,
                       environment: ValidationEnvironment, *, trigger: str, operation_id: str,
                       control: ValidationControl, baseline: BaselineBinding | None = None
                       ) -> ValidationRecord | ValidationAttempt:
        async with self._lock:
            return await self._validate(capture, spec, environment, trigger=trigger,
                operation_id=operation_id, control=control, baseline=baseline)

    async def _validate(self, capture, spec, environment, *, trigger, operation_id, control, baseline):
        # Build an Attempt early to validate operation/trigger without faking identities.
        ValidationAttempt(operation_id, trigger, "preflight")
        check_id = self._check_id(spec, environment, baseline)
        key = (validation_key(candidate_id=capture.candidate_id, check_id=check_id,
                              environment_id=environment.environment_id) if capture else None)
        previous_result = self._operations.get(operation_id)
        if previous_result is not None and previous_result[0] != key:
            raise ContractError("operation_id reused for different validation identity")
        if previous_result is None and (operation_id in self._pending or operation_id in control.ledger.operations):
            previous = self._pending.get(operation_id)
            if previous is not None and previous != key:
                raise ContractError("pending operation identity changed")
            return ValidationAttempt(operation_id, trigger, "outcome_unknown",
                capture.candidate_id if capture else None, check_id if capture else None,
                environment.environment_id, control.ledger.snapshot())

        def attempt(reason):
            value = ValidationAttempt(operation_id, trigger, reason,
                capture.candidate_id if capture else None, check_id if capture else None,
                environment.environment_id, control.ledger.snapshot())
            payload = value.to_dict()
            if previous_result is not None and isinstance(previous_result[1], ValidationRecord):
                payload["source_record_id"] = previous_result[1].record_id
            self.events.emit("validation.not_run", payload)
            # A refusal describes current eligibility; it does not erase the
            # original outcome or authorize retrying the same operation.
            self._operations.setdefault(operation_id, (key, value))
            return value

        if control.cancelled(): return attempt("cancelled")
        if control.clock() >= control.deadline_mono: return attempt("deadline")
        if capture is None: return attempt("capture_unavailable")
        # These are consumer obligations, not evidence that the capture was quiescent.
        try:
            capture.validate_against_base(self.base_entries)
            capture.validate_blobs(self.blobs)
        except (ContractError, KeyError, OSError):
            return attempt("capture_invalid")
        current = {entry.path: entry for entry in capture.entries}
        base = {entry.path: entry for entry in self.base_entries}
        protected = {entry.path: entry for entry in spec.protected_manifest}
        required = {path for path in base if path.startswith(("tests/", "testing/"))
                    or path.split("/")[-1] in ("conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg",
                    "setup.py", "tox.ini", "requirements.txt", "test-requirements.txt")}
        if not required.issubset(protected) or any(base.get(path) != entry for path, entry in protected.items()):
            return attempt("protected_spec_incomplete")
        forbidden_new = {"conftest.py", "sitecustomize.py", "usercustomize.py", "pytest.ini",
                         "pyproject.toml", "setup.cfg", "setup.py", "tox.ini", "requirements.txt"}
        if any(path not in base and (path.startswith(("tests/", "testing/"))
                or path.split("/")[-1] in forbidden_new or path.endswith(".pth")) for path in current):
            return attempt("protected_change")
        if any(current.get(path) != entry for path, entry in protected.items()):
            return attempt("protected_change")
        if not capture.admissible: return attempt("unsupported_change")
        if capture.image_digest != environment.image_digest: return attempt("environment_mismatch")
        if capture.base_revision != spec.provenance["commit"]: return attempt("source_mismatch")
        if spec.runner_id != RUNNER_ID: return attempt("runner_mismatch")
        if not environment.pytest_version.startswith(PROFILES[spec.adapter]):
            return attempt("unsupported_adapter")
        if trigger == "baseline":
            if baseline is not None or capture.manifest_id != capture.base_manifest_id:
                return attempt("baseline_requires_base")
        elif baseline is None or baseline.base_manifest_id != capture.base_manifest_id:
            return attempt("baseline_required")
        if baseline is not None and baseline.base_candidate_id != candidate_id(
                base_revision=capture.base_revision, image_digest=capture.image_digest,
                base_manifest_id=capture.base_manifest_id, manifest_id=capture.base_manifest_id,
                rules_id=capture.rules_id):
            return attempt("baseline_policy_mismatch")
        if baseline is not None:
            baseline_record = self._records_by_id.get(baseline.record_id)
            if baseline_record is None or baseline_record.trigger != "baseline":
                return attempt("baseline_unavailable")
            try:
                rebuilt = self.bind_baseline(spec, environment, baseline_record)
            except (ContractError, KeyError, OSError):
                return attempt("baseline_evidence_unavailable")
            if rebuilt != baseline:
                return attempt("baseline_mismatch")
        # Operation replay and key reuse share all current capture/spec/baseline
        # checks above and the same evidence verification below. No early pass.
        if previous_result is not None:
            record = previous_result[1]
            if isinstance(record, ValidationRecord):
                try:
                    self._verify_record_evidence(record)
                except (ContractError, KeyError, OSError):
                    return attempt("evidence_unavailable")
            return record
        if key in self._cache:
            record = self._cache[key]
            if record.complete and record.status in ("passed", "behavior_failed"):
                try:
                    self._verify_record_evidence(record)
                except (ContractError, KeyError, OSError):
                    return attempt("evidence_unavailable")
                self.events.emit("validation.reused", dict(operation_id=operation_id,
                    source_record_id=record.record_id, validation_key=key))
                self._operations[operation_id] = (key, record)
                return record
            return attempt("previous_inconclusive")
        if key in self._pending.values():
            return attempt("outcome_unknown")
        final = trigger in ("natural_end", "hard_stop")
        latest = control.deadline_mono if final else control.agent_deadline
        if latest - control.clock() < spec.limits.total_seconds:
            return attempt("insufficient_time" if final else "finalization_time_reserved")
        if control.artifacts.final_reserve < spec.limits.evidence_bytes:
            return attempt("finalization_artifact_reserve_missing")
        try:
            control.artifacts.reserve(operation_id, spec.limits.evidence_bytes, final=final)
        except BudgetRefusal as exc:
            return attempt(str(exc))
        try:
            slot = control.ledger.reserve(operation_id, trigger)
        except BudgetRefusal as exc:
            control.artifacts.settle(operation_id, 0)
            return attempt(str(exc))
        self._pending[operation_id] = key
        self.events.emit("validation.reserved", dict(operation_id=operation_id, validation_key=key,
            trigger=trigger, slot=slot, candidate_id=capture.candidate_id, check_id=check_id,
            environment_id=environment.environment_id, spec_id=spec.spec_id,
            budget=control.ledger.snapshot(), artifacts=control.artifacts.snapshot()))
        started_at = None
        started_once = False
        replica_identity = None
        request_deadline = min(latest, control.clock() + spec.limits.total_seconds)
        def started(replica_id):
            nonlocal started_at, started_once, replica_identity
            if started_once: raise ContractError("executor attempted duplicate replica start")
            if not isinstance(replica_id, str) or not replica_id or len(replica_id) > 512:
                raise ContractError("executor must identify its owned replica")
            if control.cancelled() or control.clock() >= request_deadline:
                raise BudgetRefusal("cancelled" if control.cancelled() else "deadline")
            self.events.emit("validation.started", dict(operation_id=operation_id, replica_id=replica_id,
                validation_key=key, environment_id=environment.environment_id,
                spec_id=spec.spec_id, runner_id=spec.runner_id, parser_id=spec.parser_id))
            if control.cancelled() or control.clock() >= request_deadline:
                raise BudgetRefusal("cancelled" if control.cancelled() else "deadline")
            control.ledger.start(operation_id)
            started_once = True
            replica_identity = replica_id
            started_at = datetime.now(timezone.utc).isoformat()
        request = ReplicaRequest(capture, spec, environment, operation_id,
            request_deadline, control.cancelled)
        execution = await self._execute_bounded(request, started, control)
        if not started_once:
            control.ledger.settle(operation_id)
            control.artifacts.settle(operation_id, 0)
            reason = ("cancelled" if control.cancelled() or execution.execution_status == "cancelled" else
                      "deadline" if execution.execution_status == "timed_out" or control.clock() >= request_deadline
                      else "executor_not_started")
            value = ValidationAttempt(operation_id, trigger, reason, capture.candidate_id,
                                      check_id, environment.environment_id, control.ledger.snapshot())
            self.events.emit("validation.committed", dict(attempt=value.to_dict(),
                budget=control.ledger.snapshot(), artifacts=control.artifacts.snapshot()))
            self._operations[operation_id] = (key, value)
            del self._pending[operation_id]
            return value
        parsed = classify(execution.report, operation_id=operation_id, runner_sha256=RUNNER_SHA256,
            spec=spec, environment=environment, execution_status=execution.execution_status,
            exit_code=execution.exit_code, expected_nodeids=baseline.nodeids if baseline else None,
            input_unchanged=execution.after_manifest_id == capture.manifest_id,
            cleanup_state=execution.cleanup_state, truncated=execution.truncated)
        override = None
        if execution.execution_status in ("cancelled", "timed_out"):
            override = "cancelled" if execution.execution_status == "cancelled" else "timeout"
        elif execution.environment_id != environment.environment_id:
            override = "environment_mismatch"
        elif execution.before_manifest_id != capture.manifest_id:
            override = "materialization_mismatch"
        elif not execution.runner_unchanged:
            override = "runner_mutated"
        elif control.cancelled(): override = "cancelled"
        elif control.clock() >= request.deadline_mono: override = "timeout"
        status, reason, complete = (("inconclusive", override, False) if override else
                                    (parsed.status, parsed.reason, parsed.complete))
        refs, node_ref, used = [], None, 0
        try:
            attestation_data = dict(protocol="replica-attestation-v1",
                operation_id=operation_id, replica_id=replica_identity, candidate_id=capture.candidate_id,
                check_id=check_id, environment_id=execution.environment_id,
                before_manifest_id=execution.before_manifest_id, after_manifest_id=execution.after_manifest_id,
                runner_unchanged=execution.runner_unchanged, cleanup_state=execution.cleanup_state,
                execution_status=execution.execution_status, exit_code=execution.exit_code,
                truncated=execution.truncated)
            if execution.cleanup_evidence is not None:
                attestation_data["cleanup_evidence"] = execution.cleanup_evidence
            attestation = canonical_json(attestation_data)
            for data, limit in ((execution.report, spec.limits.max_report_bytes),
                                (execution.stdout, spec.limits.max_output_bytes),
                                (execution.stderr, spec.limits.max_output_bytes),
                                (attestation, spec.limits.max_attestation_bytes)):
                if not isinstance(data, bytes) or len(data) > limit:
                    raise ContractError("evidence quota exceeded")
                ref = self.artifacts.publish(data)
                # Publishing must guarantee durable content; the reader independently verifies it.
                if ref.size != len(data): raise ContractError("artifact size mismatch")
                from src.workspace.contracts import blob_sha256
                if ref.sha256 != blob_sha256(data): raise ContractError("artifact hash mismatch")
                used += len(data); refs.append(ref)
                read_verified_blob(self.blobs, ref)
            node_ref = refs[0]
        except Exception:
            status, reason, complete = "inconclusive", "evidence_incomplete", False
            # A failed publication may have written bytes before raising. Keep the
            # whole reservation charged rather than grant free repeated output.
            used = spec.limits.evidence_bytes
        if control.cancelled():
            status, reason, complete = "inconclusive", "cancelled", False
        elif control.clock() >= request.deadline_mono:
            status, reason, complete = "inconclusive", "timeout", False
        control.artifacts.settle(operation_id, used)
        control.ledger.settle(operation_id)
        record = ValidationRecord(operation_id, trigger, capture.candidate_id, check_id,
            environment.environment_id, status, reason, execution.execution_status, execution.exit_code,
            parsed.counts, node_ref, tuple(refs), complete, execution.cleanup_state, started_at,
            datetime.now(timezone.utc).isoformat(),
            dict(ledger=control.ledger.snapshot(), final_slot=slot["final_slot"], artifacts=control.artifacts.snapshot()),
            dict(kind=spec.coverage, scope=spec.scope, target_evidence=plain(spec.target_evidence),
                 runner_is_candidate=spec.runner_is_candidate, adapter=spec.adapter))
        # One durable event commits record and settled budgets. Failure leaves pending, never a cache hit.
        self.events.emit("validation.committed", dict(record=record.to_dict(),
            budget=control.ledger.snapshot(), artifacts=control.artifacts.snapshot()))
        self._operations[operation_id] = (key, record)
        self._cache[key] = record
        self._records_by_id[record.record_id] = record
        del self._pending[operation_id]
        return record

    def _verify_record_evidence(self, record: ValidationRecord) -> bytes | None:
        report = None
        for ref in record.evidence:
            data = read_verified_blob(self.blobs, ref)
            if ref == record.node_report:
                report = data
        return report

    def bind_baseline(self, spec: PublicValidationSpec, environment: ValidationEnvironment,
                      record: ValidationRecord) -> BaselineBinding:
        check_id = self._check_id(spec, environment, None)
        if (record.trigger != "baseline" or record.check_id != check_id
                or record.environment_id != environment.environment_id or not record.complete
                or record.status not in ("passed", "behavior_failed")):
            raise ContractError("baseline is not a complete public execution")
        if self._records_by_id.get(record.record_id) != record:
            raise ContractError("baseline must be committed by this service or restored store")
        report = self._verify_record_evidence(record)
        parsed = classify(report, operation_id=record.operation_id, runner_sha256=RUNNER_SHA256,
            spec=spec, environment=environment, execution_status=record.execution_status,
            exit_code=record.exit_code, cleanup_state=record.cleanup_state)
        if (not parsed.complete or parsed.status != record.status or parsed.counts != record.counts):
            raise ContractError("baseline evidence contradicts its committed record")
        import json
        parsed_report = json.loads(report)
        failures = {p["nodeid"] for p in parsed_report["phases"]
                    if p["when"] == "call" and p["outcome"] == "failed"}
        if failures != set(spec.known_fixture_failures):
            raise ContractError("unclassified public baseline failures")
        if record.status == "behavior_failed" and spec.coverage != "fixture":
            raise ContractError("real regression baseline must pass")
        return BaselineBinding(spec.spec_id, environment.environment_id,
            tree_id(self.base_entries), record.candidate_id,
            tuple(parsed_report["nodeids"]), tuple(parsed_report["phases"]), record.record_id)

    async def _execute_bounded(self, request, started, control):
        task = asyncio.create_task(self.executor.execute(request, started))
        stopped = None
        try:
            while not task.done():
                if control.cancelled():
                    stopped = "cancelled"
                    break
                if control.clock() >= request.deadline_mono:
                    stopped = "timed_out"
                    break
                await asyncio.wait({task}, timeout=0.02)
        except asyncio.CancelledError:
            stopped = "cancelled"
        # The executor may finish without yielding after crossing its own
        # deadline. Recheck even when task.done() was already true.
        if stopped is None:
            if control.cancelled():
                stopped = "cancelled"
            elif control.clock() >= request.deadline_mono:
                stopped = "timed_out"
        if stopped:
            task.cancel()
            try:
                await asyncio.wait({task}, timeout=control.cleanup_grace_s)
            except asyncio.CancelledError:
                pass
        if task.done() and not task.cancelled():
            try:
                execution = task.result()
                if not isinstance(execution, ReplicaResult):
                    raise ContractError("executor must return ReplicaResult")
                if stopped:
                    from dataclasses import replace
                    execution = replace(execution, execution_status=stopped)
                return execution
            except Exception:
                pass
        if not task.done():
            # Unconfirmed cleanup is retained as an owned unresolved operation.
            # Do not await forever or call an executor again to manufacture success.
            self._unsettled.add(task)
            def consume(finished):
                self._unsettled.discard(finished)
                if not finished.cancelled(): finished.exception()
            task.add_done_callback(consume)
        return ReplicaResult(stopped or "failed", None, b"", b"", b"",
                             request.environment.environment_id, None, None, "cleanup_failed")
