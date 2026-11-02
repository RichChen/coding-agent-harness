import asyncio
import json
import runpy
import time
from dataclasses import replace
from pathlib import Path
import pytest

from src.validation.service import ValidationService,ValidationControl
from src.validation.contracts import ValidationAttempt,ValidationRecord
from src.validation.ledger import ValidationLedger,ByteBudget,BudgetRefusal
from src.workspace.contracts import ContractError,content_id
H=runpy.run_path(str(Path(__file__).parent/'fixtures/public_validation/support.py'))

async def bind(state):
    c,store,e,s,events,executor,svc,ctl=state
    r=await svc.validate(c['C0'],s,e,trigger='baseline',operation_id='baseline',control=ctl)
    return svc.bind_baseline(s,e,r)

@pytest.mark.asyncio
async def test_fixture_baseline_fail_candidate_pass_identity_reuse_and_events():
    state=H['setup'](); c,store,e,s,events,executor,svc,ctl=state
    binding=await bind(state); executor.failed=False
    r=await svc.validate(c['C2'],s,e,trigger='explicit_tool',operation_id='fix',control=ctl,baseline=binding)
    assert r.status=='passed' and r.coverage['kind']=='fixture' and r.complete
    assert binding.check_id==r.check_id
    witness=json.loads(store.read(r.evidence[-1]))
    assert witness['before_manifest_id']==witness['after_manifest_id']==c['C2'].manifest_id
    assert witness['candidate_id']==r.candidate_id and witness['cleanup_state']=='cleaned'
    again=await svc.validate(c['C2'],s,e,trigger='natural_end',operation_id='end',control=ctl,baseline=binding)
    same=await svc.validate(c['C2'],s,e,trigger='explicit_tool',operation_id='fix',control=ctl,baseline=binding)
    assert again is same is r and executor.calls==2 and ctl.ledger.remaining_candidates==2
    assert [n for n,p in events.rows].count('validation.reused')==1
    names=[n for n,p in events.rows]
    assert names.index('validation.reserved')<names.index('validation.started')<names.index('validation.committed')
    committed=[p for n,p in events.rows if n=='validation.committed'][-1]
    assert committed['record']['record_id']==r.record_id
    assert committed['budget']==ctl.ledger.snapshot()
    with pytest.raises(ContractError):
        await svc.validate(c['C1'],s,e,trigger='natural_end',operation_id='fix',control=ctl,baseline=binding)

@pytest.mark.asyncio
async def test_changed_candidate_not_reused_and_final_slot_blocks_explicit_tool():
    state=H['setup'](); c,store,e,s,ev,ex,svc,ctl=state; b=await bind(state)
    first=await svc.validate(c['C0'],s,e,trigger='natural_end',operation_id='one',control=ctl,baseline=b)
    ctl.ledger.note_feedback(first)
    second=await svc.validate(c['C1'],s,e,trigger='natural_end',operation_id='two',control=ctl,baseline=b)
    ctl.ledger.note_feedback(second)
    with pytest.raises(BudgetRefusal): ctl.ledger.note_feedback(second)
    blocked=await svc.validate(c['C2'],s,e,trigger='explicit_tool',operation_id='three',control=ctl,baseline=b)
    assert isinstance(blocked,ValidationAttempt) and blocked.reason=='final_slot_reserved'
    ex.failed=False
    final=await svc.validate(c['C2'],s,e,trigger='hard_stop',operation_id='final',control=ctl,baseline=b)
    assert final.status=='passed' and final.budget_receipt['final_slot']
    assert ctl.ledger.remaining_candidates==0 and ex.calls==4
    with pytest.raises(BudgetRefusal): ctl.ledger.note_feedback(final)

@pytest.mark.asyncio
async def test_corrupt_capture_protected_change_and_unsupported_do_not_start():
    state=H['setup'](); c,store,e,s,ev,ex,svc,ctl=state; b=await bind(state)
    ref=c['C2'].changed[0][1]; original=store.data[ref.key]; store.data[ref.key]=b'corrupt'
    r=await svc.validate(c['C2'],s,e,trigger='natural_end',operation_id='bad',control=ctl,baseline=b)
    assert r.reason=='capture_invalid'; store.data[ref.key]=original
    files=dict(H['BASE_FILES']); files['tests/test_calc.py']=b'import pytest\npytest.skip(allow_module_level=True)\n'
    changed,blobs=H['build_capture'](files,violations=[{'reason':'protected_change'}])
    store.data.update(blobs.blobs)
    r=await svc.validate(changed,s,e,trigger='natural_end',operation_id='tests',control=ctl,baseline=b)
    assert r.reason=='protected_change'
    unsupported=replace(c['C2'],admissible=False,violations=({'reason':'unsupported_change'},))
    r=await svc.validate(unsupported,s,e,trigger='natural_end',operation_id='unsupported',control=ctl,baseline=b)
    assert r.reason=='unsupported_change' and ex.calls==1 and ctl.ledger.remaining_candidates==3

@pytest.mark.asyncio
async def test_environment_baseline_and_unclassified_failure_rejected():
    state=H['setup'](); c,store,e,s,ev,ex,svc,ctl=state
    r=await svc.validate(c['C2'],s,e,trigger='explicit_tool',operation_id='early',control=ctl)
    assert r.reason=='baseline_required'
    raw=await svc.validate(c['C0'],s,e,trigger='baseline',operation_id='baseline',control=ctl)
    with pytest.raises(ContractError): svc.bind_baseline(replace(s,known_fixture_failures=()),e,raw)
    b=svc.bind_baseline(s,e,raw)
    with pytest.raises(ContractError):
        await svc.validate(c['C2'],s,replace(e,dependency_fingerprint='changed'),trigger='natural_end',
                           operation_id='env',control=ctl,baseline=b)
    assert ex.calls==1

@pytest.mark.asyncio
@pytest.mark.parametrize('transform,reason',[
    (lambda r,q:replace(r,after_manifest_id=q.capture.base_manifest_id),'input_mutated'),
    (lambda r,q:replace(r,cleanup_state='cleanup_failed'),'cleanup_failed'),
    (lambda r,q:replace(r,truncated=True),'evidence_incomplete'),
    (lambda r,q:replace(r,execution_status='timed_out',exit_code=None),'timeout'),
    (lambda r,q:replace(r,runner_unchanged=False),'runner_mutated'),
    (lambda r,q:replace(r,before_manifest_id=None),'materialization_mismatch'),
    (lambda r,q:replace(r,environment_id=content_id('environment-v1',{})),'environment_mismatch'),
])
async def test_incomplete_execution_spends_once_and_does_not_auto_retry(transform,reason):
    state=H['setup'](); c,store,e,s,ev,ex,svc,ctl=state; b=await bind(state)
    ex.failed=False; ex.transform=transform
    r=await svc.validate(c['C2'],s,e,trigger='natural_end',operation_id='bad',control=ctl,baseline=b)
    assert r.status=='inconclusive' and r.reason==reason
    again=await svc.validate(c['C2'],s,e,trigger='natural_end',operation_id='retry',control=ctl,baseline=b)
    assert again.reason=='previous_inconclusive' and ex.calls==2
    assert ctl.ledger.remaining_candidates==2

@pytest.mark.asyncio
async def test_cancel_deadline_and_reserve_window_prevent_side_effects():
    state=H['setup'](); c,store,e,s,ev,ex,svc,ctl=state; b=await bind(state)
    for name,control,reason in [
      ('cancel',replace(ctl,cancelled=lambda:True),'cancelled'),
      ('expired',replace(ctl,deadline_mono=0),'deadline'),
      ('reserved',replace(ctl,deadline_mono=time.monotonic()+170),'finalization_time_reserved'),
      ('short',replace(ctl,deadline_mono=time.monotonic()+1),'insufficient_time')]:
        r=await svc.validate(c['C2'],s,e,trigger='explicit_tool' if name=='reserved' else 'hard_stop',
                             operation_id=name,control=control,baseline=b)
        assert r.reason==reason
    assert ex.calls==1 and ctl.ledger.remaining_candidates==3

@pytest.mark.asyncio
async def test_pending_restored_committed_and_concurrent_idempotency():
    state=H['setup'](); c,store,e,s,ev,ex,svc,ctl=state; b=await bind(state); ex.failed=False
    requests=[svc.validate(c['C2'],s,e,trigger='natural_end',operation_id='same',control=ctl,baseline=b) for _ in range(2)]
    a,z=await asyncio.gather(*requests); assert a is z and ex.calls==2
    restored=ValidationService(base_entries=c['C0'].entries,blobs=store,artifacts=store,executor=ex,
                              events=ev,committed=[svc._operations["baseline"][1],a],pending={'pending':a.key})
    result=await restored.validate(c['C2'],s,e,trigger='hard_stop',operation_id='new',control=ctl,baseline=b)
    assert result is a
    unknown=await restored.validate(c['C2'],s,e,trigger='hard_stop',operation_id='pending',control=ctl,baseline=b)
    assert unknown.reason=='outcome_unknown' and ex.calls==2

@pytest.mark.asyncio
async def test_failed_commit_never_becomes_a_valid_cache_entry():
    state=H['setup'](); c,store,e,s,ev,ex,svc,ctl=state; b=await bind(state); ex.failed=False
    original=ev.emit
    def failing(name,payload):
        if name=='validation.committed': raise OSError('injected durable failure')
        original(name,payload)
    ev.emit=failing
    with pytest.raises(OSError):
        await svc.validate(c['C2'],s,e,trigger='natural_end',operation_id='commit',control=ctl,baseline=b)
    result=await svc.validate(c['C2'],s,e,trigger='natural_end',operation_id='commit',control=ctl,baseline=b)
    assert result.reason=='outcome_unknown' and ex.calls==2


def test_byte_and_slot_reservations_restore_and_feedback_limits():
    bytes=ByteBudget(100,40)
    with pytest.raises(BudgetRefusal): bytes.reserve('ordinary',61)
    bytes.reserve('ordinary',60); bytes.settle('ordinary',60)
    bytes.reserve('final',40,final=True); bytes.settle('final',40)
    assert bytes.committed==100
    l=ValidationLedger(); l.reserve('unknown','hard_stop'); l.start('unknown')
    restored=ValidationLedger.restore(l.snapshot())
    assert restored.snapshot()==l.snapshot() and restored.remaining_candidates==2
    with pytest.raises(ContractError): restored.reserve('unknown','hard_stop')

@pytest.mark.asyncio
async def test_no_capture_attempt_has_no_fabricated_candidate_id():
    c,store,e,s,ev,ex,svc,ctl=H['setup']()
    r=await svc.validate(None,s,e,trigger='hard_stop',operation_id='no-capture',control=ctl)
    assert r.candidate_id is None and r.reason=='capture_unavailable' and ex.calls==0


@pytest.mark.asyncio
async def test_commit_unknown_blocks_a_different_operation_for_the_same_key():
    state=H['setup'](); c,store,e,s,ev,ex,svc,ctl=state; b=await bind(state); ex.failed=False
    original=ev.emit
    def failing(name,payload):
        if name=='validation.committed': raise OSError('injected')
        original(name,payload)
    ev.emit=failing
    with pytest.raises(OSError):
        await svc.validate(c['C2'],s,e,trigger='natural_end',operation_id='lost',control=ctl,baseline=b)
    ev.emit=original
    r=await svc.validate(c['C2'],s,e,trigger='hard_stop',operation_id='another',control=ctl,baseline=b)
    assert r.reason=='outcome_unknown' and ex.calls==2

@pytest.mark.asyncio
async def test_reuse_requires_intact_durable_artifacts_and_record_roundtrip():
    state=H['setup'](); c,store,e,s,ev,ex,svc,ctl=state; b=await bind(state); ex.failed=False
    record=await svc.validate(c['C2'],s,e,trigger='natural_end',operation_id='first',control=ctl,baseline=b)
    assert ValidationRecord.from_dict(record.to_dict())==record
    wire=record.to_dict(); wire['counts']['passed']=50
    with pytest.raises(ContractError): ValidationRecord.from_dict(wire)
    store.data[record.node_report.key]=b'corrupt'
    r=await svc.validate(c['C2'],s,e,trigger='hard_stop',operation_id='again',control=ctl,baseline=b)
    assert r.reason=='evidence_unavailable' and ex.calls==2

@pytest.mark.asyncio
@pytest.mark.parametrize('stop',['cancel','deadline','async_cancel'])
async def test_started_executor_is_cancelled_and_failed_attempt_spends_one_slot(stop):
    from src.validation.replica import ReplicaResult
    state=H['setup'](); c,store,e,s,ev,ex,svc,ctl=state; b=await bind(state)
    begun=asyncio.Event(); cancelled=asyncio.Event(); cleaned=[]
    fake_clock=[0]
    class Blocking:
        async def execute(self,req,started):
            started('owned-blocking'); begun.set()
            try: await asyncio.Future()
            except asyncio.CancelledError:
                cleaned.append(True)
                return ReplicaResult('cancelled',None,b'',b'',b'',e.environment_id,
                                     req.capture.manifest_id,req.capture.manifest_id,'cleaned')
    svc.executor=Blocking()
    ctl=replace(ctl,deadline_mono=1000,clock=lambda:fake_clock[0],cancelled=cancelled.is_set)
    task=asyncio.create_task(svc.validate(c['C2'],s,e,trigger='natural_end',operation_id='stop',control=ctl,baseline=b))
    await begun.wait()
    if stop=='cancel': cancelled.set()
    elif stop=='deadline': fake_clock[0]=11
    else: task.cancel()
    r=await asyncio.wait_for(task,2)
    assert r.status=='inconclusive' and r.reason==('timeout' if stop=='deadline' else 'cancelled')
    assert r.cleanup_state=='cleaned' and cleaned==[True] and ctl.ledger.remaining_candidates==2

@pytest.mark.asyncio
async def test_baseline_allowance_is_single_and_failures_are_not_silently_green():
    c,store,e,s,ev,ex,svc,ctl=H['setup']()
    await svc.validate(c['C0'],s,e,trigger='baseline',operation_id='one',control=ctl)
    # An incomplete baseline may not be retried implicitly with a fresh op ID.
    other_spec=replace(s,scope='different declared check')
    r=await svc.validate(c['C0'],other_spec,e,trigger='baseline',operation_id='two',control=ctl)
    assert r.reason=='baseline_limit' and ex.calls==1

@pytest.mark.asyncio
async def test_reject_unfrozen_policy_or_missing_report_space():
    state=H['setup'](); c,store,e,s,ev,ex,svc,ctl=state; b=await bind(state)
    changed_rules=content_id('rules-v1',{'different':'rules'})
    from src.workspace.contracts import candidate_id
    cap=replace(c['C2'],rules_id=changed_rules,candidate_id=candidate_id(
        base_revision=c['C2'].base_revision,image_digest=c['C2'].image_digest,
        base_manifest_id=c['C2'].base_manifest_id,manifest_id=c['C2'].manifest_id,rules_id=changed_rules))
    r=await svc.validate(cap,s,e,trigger='natural_end',operation_id='policy',control=ctl,baseline=b)
    assert r.reason=='baseline_policy_mismatch'
    r=await svc.validate(c['C2'],s,e,trigger='natural_end',operation_id='space',
        control=replace(ctl,artifacts=ByteBudget(1000000,1)),baseline=b)
    assert r.reason=='finalization_artifact_reserve_missing' and ex.calls==1


@pytest.mark.asyncio
async def test_no_start_releases_slot_and_artifact_reservation():
    state=H['setup'](); c,store,e,s,ev,ex,svc,ctl=state; b=await bind(state)
    class NoStart:
        async def execute(self,request,started): raise OSError('preflight failed')
    svc.executor=NoStart()
    r=await svc.validate(c['C2'],s,e,trigger='natural_end',operation_id='no-start',control=ctl,baseline=b)
    assert isinstance(r,ValidationAttempt) and r.reason=='executor_not_started'
    assert ctl.ledger.remaining_candidates==3 and not ctl.artifacts.held

@pytest.mark.asyncio
async def test_durable_evidence_publication_failure_cannot_produce_pass():
    state=H['setup'](); c,store,e,s,ev,ex,svc,ctl=state; b=await bind(state); ex.failed=False
    def broken(data): raise OSError('disk full')
    store.publish=broken
    r=await svc.validate(c['C2'],s,e,trigger='hard_stop',operation_id='disk',control=ctl,baseline=b)
    assert (r.status,r.reason,r.complete)==('inconclusive','evidence_incomplete',False)
    assert ctl.ledger.remaining_candidates==2


@pytest.mark.asyncio
async def test_new_conftest_cannot_evade_frozen_manifest_membership():
    state=H['setup'](); c,store,e,s,ev,ex,svc,ctl=state; b=await bind(state)
    files=dict(H['BASE_FILES']); files['pkg/conftest.py']=b'def pytest_collection_modifyitems(items): items.clear()\n'
    # Even an internally consistent capture with an erroneous admissible flag must
    # not bypass the validation service's frozen test/hook policy.
    cap,blobs=H['build_capture'](files); store.data.update(blobs.blobs)
    r=await svc.validate(cap,s,e,trigger='natural_end',operation_id='hook',control=ctl,baseline=b)
    assert r.reason=='protected_change' and ex.calls==1

@pytest.mark.asyncio
async def test_unconfirmed_executor_cleanup_is_bounded_and_never_retried():
    state=H['setup'](); c,store,e,s,ev,ex,svc,ctl=state; b=await bind(state)
    started_event=asyncio.Event(); release=asyncio.Event(); fake_clock=[0]; calls=[]
    class IgnoresCancellation:
        async def execute(self,request,started):
            calls.append(True); started('owned-unknown'); started_event.set()
            try: await asyncio.Future()
            except asyncio.CancelledError: await release.wait()
            raise RuntimeError('finally stopped by test owner')
    svc.executor=IgnoresCancellation()
    ctl=replace(ctl,deadline_mono=1000,clock=lambda:fake_clock[0],cleanup_grace_s=0)
    task=asyncio.create_task(svc.validate(c['C2'],s,e,trigger='hard_stop',operation_id='unknown',control=ctl,baseline=b))
    await started_event.wait(); fake_clock[0]=11
    r=await asyncio.wait_for(task,1)
    assert (r.status,r.reason,r.cleanup_state)==('inconclusive','timeout','cleanup_failed')
    retry=await svc.validate(c['C2'],s,e,trigger='hard_stop',operation_id='retry',control=ctl,baseline=b)
    assert retry.reason=='previous_inconclusive' and calls==[True]
    release.set()
    if svc._unsettled: await asyncio.gather(*svc._unsettled,return_exceptions=True)
