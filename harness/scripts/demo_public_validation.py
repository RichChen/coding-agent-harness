#!/usr/bin/env python3
"""One bounded real Sphinx public validation demo. No model or independent grader."""
from __future__ import annotations
import argparse
import asyncio
import base64
import json
import os
from pathlib import Path
import sys
import time
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.validation.contracts import ValidationRecord, plain
from src.validation.docker_replica import DockerReplicaExecutor
from src.validation.ledger import ByteBudget, ValidationLedger
from src.validation.service import ValidationControl, ValidationService
from src.validation.sphinx_profile import SphinxProfile, ValidationArtifacts, REVISION
from src.workspace.blobs import WorkspaceBlobStore
from src.workspace.capture import CaptureProvider
from src.workspace.contracts import canonical_json
from src.workspace.control import CaptureControl
from src.workspace.coordinator import WorkspaceCoordinator


class DiskEvents:
    def __init__(self,path): self.path=path
    def emit(self,name,payload):
        with self.path.open('ab') as f:
            f.write(canonical_json(dict(event=name,payload=plain(payload)))+b'\n')
            f.flush();os.fsync(f.fileno())


def compact(record):
    fields=('operation_id','trigger','candidate_id','check_id','environment_id','status','reason')
    result={name:getattr(record,name) for name in fields}
    if isinstance(record,ValidationRecord):
        result.update(record_id=record.record_id,counts=dict(record.counts),complete=record.complete,
            cleanup_state=record.cleanup_state,node_report=record.node_report.to_dict(),
            coverage=plain(record.coverage),evidence=[ref.to_dict() for ref in record.evidence])
    return result


async def run_demo(output:Path):
    output=output.resolve()
    output.mkdir(parents=True,exist_ok=False) # Existing output is always rejected.
    store=WorkspaceBlobStore(output/'artifacts')
    artifacts=ValidationArtifacts(store)
    profile=SphinxProfile()
    editor=WorkspaceCoordinator(profile.new_workspace())
    provider=CaptureProvider(editor,store,base_revision=REVISION,policy=profile.policy)
    executor=DockerReplicaExecutor(store=store,profile=profile)
    deadline=time.monotonic()+1200
    def ctrl(op,seconds=120): return CaptureControl(op,min(deadline,time.monotonic()+seconds))
    report=dict(schema='public-validation-demo-v1',created_at=datetime.now(timezone.utc).isoformat(),
        run={'status':'running'},mechanism='artificial source fault injection, not an issue repair',
        development_validation={'status':'not_run','coverage':'regression_only'},
        independent_evaluation={'status':'not_evaluated','solved':None},records={},checks={},replicas=[])
    failure=None
    try:
        print('Starting owned Sphinx editor and capturing actual base',flush=True)
        await editor.start(ctrl('editor-start',20))
        base=await provider.capture(ctrl('base-capture',20))
        environment,facts=await profile.probe_environment(editor,ctrl('profile-probe',10))
        spec=profile.build_spec(base,environment)
        report['environment']=plain(environment)
        report['environment_id']=environment.environment_id
        report['dependency_package_count']=len(facts['distributions'])
        report['spec_id']=spec.spec_id
        report['base_manifest_id']=base.manifest_id
        report['editor']=editor.transport_identity
        service=ValidationService(base_entries=base.entries,blobs=artifacts,artifacts=artifacts,
            executor=executor,events=DiskEvents(output/'events.jsonl'))
        control=ValidationControl(deadline,ValidationLedger(),ByteBudget(32*1024**2,8*1024**2))
        async def validate(name,capture,trigger,binding=None):
            print('Public validation: '+name,flush=True)
            record=await service.validate(capture,spec,environment,trigger=trigger,
                operation_id=name,control=control,baseline=binding)
            report['records'][name]=compact(record)
            (output/(name+'.json')).write_bytes(canonical_json(record.to_dict()))
            after=await provider.capture(ctrl('editor-after-'+name,20))
            assert after.manifest_id==capture.manifest_id,'validation polluted editor inputs'
            report['checks']['editor_unchanged_after_'+name]=True
            return record
        baseline=await validate('baseline',base,'baseline')
        assert baseline.status=='passed' and baseline.counts['collected']==8 and baseline.counts['passed']==8
        binding=service.bind_baseline(spec,environment,baseline)
        report['check_id']=binding.check_id
        source_path='sphinx/util/__init__.py'
        source=store.read(dict(base.corpus_blobs)[source_path])
        test_path='tests/test_util.py'
        original_test=store.read(dict(base.corpus_blobs)[test_path])
        async def write(path,data,name):
            # Only the two declared public demo inputs can be edited by this script.
            assert path in (source_path,test_path)
            code="import sys,json,pathlib,base64;p=json.load(sys.stdin);pathlib.Path('/workspace',p['path']).write_bytes(base64.b64decode(p['data'],validate=True))"
            result=await editor.execute([environment.python_bin,'-I','-S','-c',code],ctrl(name,10),
                stdin=canonical_json(dict(path=path,data=base64.b64encode(data).decode('ascii'))))
            assert result.exit_code==0,'demo edit RPC failed'
            return await provider.capture(ctrl('capture-'+name,20))
        legal_bytes=source+b'\n# public validation mechanism: legal candidate\n'
        legal=await write(source_path,legal_bytes,'legal-edit')
        good=await validate('legal',legal,'explicit_tool',binding)
        assert good.status=='passed'
        count=len(executor.executions)
        reused=await validate('legal-reuse',legal,'natural_end',binding)
        assert reused.record_id==good.record_id and len(executor.executions)==count
        fault=await write(source_path,source+b'\ndef display_chunk(chunk):\n    return "PUBLIC_VALIDATION_INJECTED_FAILURE"\n','fault-edit')
        failed=await validate('fault',fault,'explicit_tool',binding)
        assert fault.candidate_id!=legal.candidate_id and failed.status=='behavior_failed'
        assert failed.record_id!=good.record_id and len(executor.executions)==count+1
        restored=await write(source_path,source+b'\n# public validation mechanism: restored behavior\n','restore-edit')
        final=await validate('restored',restored,'hard_stop',binding)
        assert final.status=='passed' and final.budget_receipt['final_slot']
        frozen=await write(test_path,original_test+b'\n# frozen-input rejection probe\n','frozen-edit')
        count=len(executor.executions)
        rejected=await validate('frozen-rejected',frozen,'natural_end',binding)
        assert rejected.status=='not_run' and rejected.reason=='protected_change'
        assert len(executor.executions)==count
        final_capture=await write(test_path,original_test,'restore-test')
        assert final_capture.candidate_id==restored.candidate_id
        final_reused=await validate('final-reuse',final_capture,'natural_end',binding)
        assert final_reused.record_id==final.record_id and len(executor.executions)==count
        report['checks'].update(baseline_real_eight_passed=True,legal_passed=True,source_fault_failed=True,
            changed_version_not_reused=True,same_version_reused_without_execution=True,
            frozen_test_rejected_without_replica=True,restored_final_passed=True)
        report['validation_budget']=control.ledger.snapshot()
        report['artifact_budget']=control.artifacts.snapshot()
        report['development_validation']=dict(status=final.status,coverage='regression_only',
            candidate_id=final.candidate_id,record_id=final.record_id)
        report['run']={'status':'completed'}
    except Exception as exc:
        failure=exc
        report['run']={'status':'failed','reason':type(exc).__name__}
    finally:
        report['editor_cleanup_ok']=await editor.close()
        report['replicas']=executor.executions
        report['owned_containers_remaining']=[r['container_name'] for r in executor.executions if not r['cleanup_ok']]
        if not report['editor_cleanup_ok']:
            report['owned_containers_remaining'].append(editor.transport_identity['container_name'])
        if report['owned_containers_remaining']:
            report['run']={'status':'failed','reason':'cleanup_failed'}
        (output/'summary.json').write_bytes(canonical_json(report))
    print(json.dumps(dict(summary=str(output/'summary.json'),run=report['run'],
        development_validation=report['development_validation'],independent_evaluation=report['independent_evaluation'],
        replica_count=len(executor.executions),owned_containers_remaining=report['owned_containers_remaining']),ensure_ascii=False),flush=True)
    if failure is not None: raise failure
    if report['run']['status']!='completed': raise RuntimeError('demo incomplete')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True,help='new output directory; existing paths rejected')
    args=parser.parse_args()
    asyncio.run(run_demo(args.output))


if __name__=='__main__': main()
