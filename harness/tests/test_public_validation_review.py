"""Behavior regressions for the c72d7c7 independent review findings."""
import json
import runpy
from dataclasses import replace
from pathlib import Path

import pytest

from src.validation.contracts import BaselineBinding, ValidationAttempt
from src.workspace.contracts import content_id

H = runpy.run_path(str(Path(__file__).parent / "fixtures/public_validation/support.py"))


async def baseline(state):
    captures, store, env, spec, events, executor, service, control = state
    record = await service.validate(captures["C0"], spec, env, trigger="baseline",
        operation_id="baseline", control=control)
    return record, service.bind_baseline(spec, env, record)


@pytest.mark.asyncio
async def test_candidate_rejects_binding_without_a_committed_baseline_execution():
    captures, store, env, spec, events, executor, service, control = H["setup"]()
    forged = BaselineBinding(spec.spec_id, env.environment_id,
        captures["C0"].base_manifest_id, captures["C0"].candidate_id,
        ("tests/test_calc.py::test_add",), (), content_id("validation-record-v1", {}))
    executor.failed = False
    result = await service.validate(captures["C2"], spec, env, trigger="natural_end",
        operation_id="candidate", control=control, baseline=forged)
    assert isinstance(result, ValidationAttempt) and result.reason == "baseline_unavailable"
    assert executor.calls == 0 and not control.ledger.operations


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["outcomes", "nodeids", "record_id"])
async def test_binding_must_equal_the_rebuilt_committed_baseline(change):
    state = H["setup"]()
    captures, store, env, spec, events, executor, service, control = state
    record, binding = await baseline(state)
    changes = {"outcomes": (), "nodeids": ("other.py::test_other",),
               "record_id": content_id("validation-record-v1", {})}
    altered = replace(binding, **{change: changes[change]})
    executor.failed = False
    result = await service.validate(captures["C2"], spec, env, trigger="natural_end",
        operation_id="candidate", control=control, baseline=altered)
    assert isinstance(result, ValidationAttempt)
    assert result.reason == ("baseline_unavailable" if change == "record_id" else "baseline_mismatch")
    assert executor.calls == 1 and control.ledger.remaining_candidates == 3


@pytest.mark.asyncio
async def test_corrupt_committed_baseline_evidence_prevents_candidate_execution():
    state = H["setup"]()
    captures, store, env, spec, events, executor, service, control = state
    record, binding = await baseline(state)
    store.data[record.node_report.key] = b"corrupt"
    executor.failed = False
    result = await service.validate(captures["C2"], spec, env, trigger="hard_stop",
        operation_id="candidate", control=control, baseline=binding)
    assert isinstance(result, ValidationAttempt) and result.reason == "baseline_evidence_unavailable"
    assert executor.calls == 1 and control.ledger.remaining_candidates == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["evidence", "inadmissible", "capture_blob"])
async def test_same_operation_and_key_reuse_share_current_eligibility_checks(invalid):
    state = H["setup"]()
    captures, store, env, spec, events, executor, service, control = state
    base_record, binding = await baseline(state)
    executor.failed = False
    capture = captures["C2"]
    record = await service.validate(capture, spec, env, trigger="explicit_tool",
        operation_id="candidate", control=control, baseline=binding)
    snapshot = control.ledger.snapshot()
    if invalid == "evidence": store.data[record.node_report.key] = b"corrupt"
    elif invalid == "capture_blob": store.data[capture.changed[0][1].key] = b"corrupt"
    else: capture = replace(capture, admissible=False, violations=({"reason": "unsupported_change"},))
    expected = {"evidence": "evidence_unavailable", "inadmissible": "unsupported_change",
                "capture_blob": "capture_invalid"}[invalid]
    for operation in ("candidate", "another"):
        result = await service.validate(capture, spec, env, trigger="explicit_tool",
            operation_id=operation, control=control, baseline=binding)
        assert isinstance(result, ValidationAttempt) and result.reason == expected
    assert executor.calls == 2 and control.ledger.snapshot() == snapshot
    # The immutable historical record remains inspectable, even while ineligible.
    assert service._operations["candidate"][1] is record


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", [10, 11])
async def test_already_finished_executor_cannot_pass_after_its_request_deadline(finish):
    state = H["setup"]()
    captures, store, env, spec, events, executor, service, control = state
    record, binding = await baseline(state)
    clock = [0]
    control = replace(control, deadline_mono=1000, clock=lambda: clock[0])
    executor.failed = False
    def finish_late(result, request):
        assert request.deadline_mono == 10
        clock[0] = finish  # task finishes without yielding back to the monitor.
        return result
    executor.transform = finish_late
    result = await service.validate(captures["C2"], spec, env, trigger="hard_stop",
        operation_id="late", control=control, baseline=binding)
    assert result.status == "inconclusive" and result.reason == "timeout" and not result.complete
    assert executor.calls == 2 and control.ledger.remaining_candidates == 2


@pytest.mark.asyncio
async def test_started_callback_refuses_side_effects_after_request_deadline():
    state = H["setup"]()
    captures, store, env, spec, events, executor, service, control = state
    record, binding = await baseline(state)
    clock, effects = [0], []
    control = replace(control, deadline_mono=1000, clock=lambda: clock[0])
    class LateStart:
        async def execute(self, request, started):
            assert request.deadline_mono == 10
            clock[0] = 11
            started("owned-but-not-created")
            effects.append("created")
            # A normal-looking result demonstrates why the service must gate start.
            from src.validation.replica import ReplicaResult
            return ReplicaResult("succeeded", 0,
                json.dumps(H["receipt"](env, request.operation_id)).encode(), b"", b"",
                env.environment_id, request.capture.manifest_id, request.capture.manifest_id, "cleaned")
    service.executor = LateStart()
    result = await service.validate(captures["C2"], spec, env, trigger="hard_stop",
        operation_id="late-start", control=control, baseline=binding)
    assert isinstance(result, ValidationAttempt) and result.reason == "deadline"
    assert effects == [] and control.ledger.remaining_candidates == 3


@pytest.mark.asyncio
async def test_restore_without_baseline_record_cannot_replay_a_passed_candidate():
    state = H["setup"]()
    captures, store, env, spec, events, executor, service, control = state
    base_record, binding = await baseline(state)
    executor.failed = False
    record = await service.validate(captures["C2"], spec, env, trigger="natural_end",
        operation_id="candidate", control=control, baseline=binding)
    from src.validation.service import ValidationService
    restored = ValidationService(base_entries=captures["C0"].entries, blobs=store,
        artifacts=store, executor=executor, events=events, committed=[record])
    for operation in ("candidate", "new"):
        refused = await restored.validate(captures["C2"], spec, env, trigger="natural_end",
            operation_id=operation, control=control, baseline=binding)
        assert isinstance(refused, ValidationAttempt) and refused.reason == "baseline_unavailable"
    assert executor.calls == 2 and restored._operations["candidate"][1] is record


@pytest.mark.asyncio
async def test_concurrent_different_operations_for_one_key_execute_once():
    import asyncio
    state = H["setup"]()
    captures, store, env, spec, events, executor, service, control = state
    base_record, binding = await baseline(state)
    executor.failed = False
    first, second = await asyncio.gather(*(service.validate(captures["C2"], spec, env,
        trigger="natural_end", operation_id=operation, control=control, baseline=binding)
        for operation in ("one", "two")))
    assert first is second and first.status == "passed"
    assert executor.calls == 2 and control.ledger.remaining_candidates == 2


@pytest.mark.asyncio
async def test_baseline_operation_cannot_be_replayed_as_a_candidate_without_binding():
    state = H["setup"]()
    captures, store, env, spec, events, executor, service, control = state
    base_record, binding = await baseline(state)
    refused = await service.validate(captures["C0"], spec, env, trigger="natural_end",
        operation_id="baseline", control=control)
    assert isinstance(refused, ValidationAttempt) and refused.reason == "baseline_required"
    assert service._operations["baseline"][1] is base_record and executor.calls == 1
