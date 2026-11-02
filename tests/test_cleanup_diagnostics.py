"""Controlled cleanup faults; no Docker or solver API, not a historical reproduction."""
import asyncio
import json
import runpy
import time
from dataclasses import replace
from pathlib import Path

import pytest

from src.workspace.control import CaptureControl, CaptureError
from src.workspace.coordinator import WorkspaceCoordinator
from src.workspace.docker_transport import CommandResult, DockerWorkspace, OWNER_LABEL


class ScriptedDocker(DockerWorkspace):
    def __init__(self, fault=None):
        super().__init__(image="fixture@sha256:"+"1"*64)
        self.reserved = True
        self.container_id = "owned-id"
        self.fault = fault
        self.removed = False
        self.calls = []

    async def cli(self, control, *args, **kwargs):
        control.remaining(None)
        self.calls.append(args)
        phase = self.cleanup_evidence["phase"]
        if phase == self.fault:
            return CommandResult(125, b"", b"controlled daemon failure " + b"\0"*1000)
        if self.fault == "confirm_timeout" and phase == "confirm_absence":
            await control.wait(asyncio.sleep(1), maximum=.001)
        if args[0] == "ps":
            return CommandResult(0, b"" if self.removed else b"owned-id", b"")
        if args[0] == "inspect":
            label = "different-owner" if self.fault == "ownership" else self.owner
            return CommandResult(0, json.dumps([dict(Id=self.container_id, Config=dict(Labels={OWNER_LABEL:label}))]).encode(), b"")
        if args[0] == "rm":
            self.removed = True
            return CommandResult(0, b"owned-id", b"")
        raise AssertionError(args)


class PendingLookupTimeout(DockerWorkspace):
    """First result is real fixture data; second call has no command result."""
    def __init__(self):
        super().__init__(image="fixture@sha256:"+"1"*64)
        self.reserved=self.create_pending=True
        self.calls=[]
    async def cli(self,control,*args,**kwargs):
        self.calls.append(args)
        if len(self.calls)==1:return CommandResult(0,b"",b"first-query-marker")
        raise CaptureError("operation_timeout")


@pytest.mark.asyncio
async def test_repeated_locate_timeout_never_inherits_prior_result():
    transport=PendingLookupTimeout();coordinator=WorkspaceCoordinator(transport)
    assert not await coordinator.close()
    receipt=coordinator.cleanup_evidence[-1]['transport'];latest=receipt['steps'][-1]
    assert receipt['phase']=='locate' and not receipt['cleanup_ok'] and receipt['create_pending']
    assert not receipt['removal_succeeded'] and not any(c[0]=='rm' for c in transport.calls)
    assert len(receipt['steps'])==1 and latest['calls']==2 and latest['result_call_number']==2
    assert latest['result_scope']=='latest_call_only' and latest['exit_code'] is None and latest['stderr'] is None
    assert latest['reason']=='operation_timeout' and latest['exception_type']=='CaptureError'
    assert 'first-query-marker' not in json.dumps(receipt)


@pytest.mark.asyncio
async def test_normal_cleanup_has_confirmed_absence_identity_and_unchanged_grace():
    transport=ScriptedDocker();coordinator=WorkspaceCoordinator(transport)
    assert await coordinator.close()
    receipt=coordinator.cleanup_evidence[-1]["transport"]
    assert receipt["owner"]==transport.owner and receipt["container_id"]=="owned-id"
    assert receipt["status"]=="confirmed_absent" and receipt["absence_confirmed"]
    assert receipt["removal_succeeded"] and 0<receipt["remaining_ms_at_start"]<=10000
    assert [s["phase"] for s in receipt["steps"]]==["locate","inspect_owner","remove","confirm_absence"]
    assert all(s["exit_code"]==0 and s["remaining_ms_after"]<=s["remaining_ms_before"] for s in receipt["steps"])


@pytest.mark.asyncio
@pytest.mark.parametrize("fault,phase,removed",[("remove","remove",False),("confirm_absence","confirm_absence",True),
    ("confirm_timeout","confirm_absence",True),("ownership","verify_ownership",False),("locate","locate",False)])
async def test_failure_retains_stage_and_uncertainty_even_after_removal(fault,phase,removed):
    transport=ScriptedDocker(fault);transport.cleanup_ok=True # prior success must not leak into this failed attempt.
    coordinator=WorkspaceCoordinator(transport)
    assert not await coordinator.close()
    assert transport.cleanup_ok is False and transport.removed is removed
    receipt=coordinator.cleanup_evidence[-1]["transport"]
    assert receipt["phase"]==phase and receipt["status"]=="failed" and not receipt["absence_confirmed"]
    assert receipt["removal_succeeded"] is removed and receipt["error"]["exception_type"]=="CaptureError"
    if fault in ("remove","confirm_absence","locate"):
        step=receipt["steps"][-1]
        assert step["exit_code"]==125 and "controlled daemon failure" in step["stderr"] and step["stderr_truncated"]
        assert len(json.dumps(step["stderr"],ensure_ascii=False,separators=(",",":")).encode())<=256
    if fault=="confirm_timeout":assert receipt["error"]["reason"]=="operation_timeout"
    if fault=="ownership":assert not any(c[0]=="rm" for c in transport.calls)


@pytest.mark.asyncio
async def test_retry_confirmation_does_not_mutate_prior_failure_evidence():
    transport=ScriptedDocker("confirm_absence");coordinator=WorkspaceCoordinator(transport)
    assert not await coordinator.close()
    original=json.dumps(coordinator.cleanup_evidence[0],sort_keys=True)
    transport.fault=None
    assert await coordinator.close()
    assert json.dumps(coordinator.cleanup_evidence[0],sort_keys=True)==original
    assert len(coordinator.cleanup_evidence)==2 and coordinator.cleanup_evidence[-1]["cleanup_ok"]


@pytest.mark.asyncio
@pytest.mark.parametrize('scenario',['confirmation_failure','repeated_lookup_timeout'])
async def test_diagnostics_are_durable_attestation_not_only_memory(scenario):
    support=runpy.run_path(str(Path(__file__).parent/'fixtures/public_validation/support.py'))
    c,store,env,spec,events,executor,service,control=support['setup']()
    baseline=await service.validate(c['C0'],spec,env,trigger='baseline',operation_id='base',control=control)
    binding=service.bind_baseline(spec,env,baseline);executor.failed=False
    transport=ScriptedDocker('confirm_absence') if scenario=='confirmation_failure' else PendingLookupTimeout()
    coordinator=WorkspaceCoordinator(transport)
    assert not await coordinator.close()
    diagnostic=dict(container_name=transport.name,cleanup_ok=False,attempts=coordinator.cleanup_evidence)
    executor.transform=lambda result,request:replace(result,cleanup_state='cleanup_failed',cleanup_evidence=diagnostic)
    record=await service.validate(c['C2'],spec,env,trigger='hard_stop',operation_id='controlled-cleanup-fault',control=control,baseline=binding)
    assert record.status=='inconclusive' and record.reason=='cleanup_failed'
    attestation=json.loads(store.read(record.evidence[-1]))
    assert attestation['cleanup_evidence']==diagnostic
    if scenario=='repeated_lookup_timeout':
        step=attestation['cleanup_evidence']['attempts'][-1]['transport']['steps'][-1]
        assert step['calls']==2 and step['exit_code'] is None and step['stderr'] is None
    assert record.evidence[-1].size<=spec.limits.max_attestation_bytes
    committed=[p for kind,p in events.rows if kind=='validation.committed'][-1]
    assert committed['record']['evidence'][-1]['sha256']==record.evidence[-1].sha256


@pytest.mark.asyncio
async def test_docker_adapter_returns_cleanup_diagnostics_not_only_its_audit(monkeypatch):
    from types import SimpleNamespace
    from src.validation.docker_replica import DockerReplicaExecutor
    import src.validation.docker_replica as module
    fixture=runpy.run_path(str(Path(__file__).parent/'test_public_validation_docker.py'))
    attempts=[dict(cleanup_ok=False,transport=dict(phase='confirm_absence',absence_confirmed=False))]
    class Coordinator:
        def __init__(self,transport):self.cleanup_evidence=attempts
        async def start(self,control):raise CaptureError('controlled_start_failure')
        async def close(self):return False
    monkeypatch.setattr(module,'WorkspaceCoordinator',Coordinator)
    executor=DockerReplicaExecutor(store=object(),profile=fixture['FakeProfile']())
    result=await executor.execute(fixture['request'](),lambda owner:None)
    assert result.cleanup_state=='cleanup_failed'
    assert result.cleanup_evidence['attempts']==attempts
    attempts[0]['transport']['phase']='mutated later'
    assert result.cleanup_evidence['attempts'][0]['transport']['phase']=='confirm_absence'


@pytest.mark.asyncio
async def test_step_limit_completion_persists_separate_main_and_replica_cleanup(tmp_path):
    fixture=runpy.run_path(str(Path(__file__).parent/'test_workspace_completion.py'))
    loop,session,model,profile,executor=fixture['setup'](tmp_path,[fixture['write'](fixture['GOOD'])])
    loop.budget.max_steps=1
    executor.candidate_cleanup='cleanup_failed'
    with session:
        result=await session.run(loop,'controlled step-limit fixture; no external model')
        assert result.termination_kind=='step_limit' and not result.success
        assert result.workspace_v1['delivery']['status']=='incomplete'
        persisted=session.state.terminal['workspace_v1']['run']['cleanup_evidence']
        assert persisted['main_workspace']['cleanup_ok'] is True
        assert persisted['main_workspace']['attempts'][-1]['cleanup_ok'] is True
        assert any(r['cleanup_state']=='cleanup_failed' for r in persisted['validation_records'])
        assert not session.state.terminal['workspace_v1']['run']['cleanup_ok']
