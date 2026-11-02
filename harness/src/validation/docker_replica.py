"""Production Sphinx ReplicaExecutor using the shared workspace foundation."""
from __future__ import annotations
import asyncio
import base64
import copy
import json
import time
from dataclasses import dataclass
from uuid import uuid4

from src.validation.replica import ReplicaRequest, ReplicaResult, RUNNER_BYTES, RUNNER_SHA256
from src.validation.sphinx_profile import IMAGE, REVISION, SphinxProfile
from src.workspace.capture import CaptureProvider
from src.workspace.contracts import ContractError, blob_sha256, canonical_json, tree_id
from src.workspace.control import CaptureControl, CaptureError
from src.workspace.coordinator import WorkspaceCoordinator, cleanup_exception
from src.workspace.materialize import materialize

WRITE_CONTROL = r'''
import json,sys,pathlib,base64,hashlib
p=json.load(sys.stdin); root=pathlib.Path(p['root']); root.mkdir(mode=0o700)
for name,encoded in p['files'].items():
 if name not in ('runner.py','request.json'): raise ValueError('invalid control file')
 data=base64.b64decode(encoded,validate=True); target=root/name; target.write_bytes(data); target.chmod(0o444)
print(json.dumps({name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in p['files']}))
'''
READ_CONTROL = r'''
import sys,json,pathlib,hashlib,base64
p=json.load(sys.stdin); root=pathlib.Path(p['root']); result={}
for name in ('runner.py','request.json'):
 path=root/name
 if path.is_symlink() or not path.is_file(): raise ValueError('control input missing')
 result[name]=hashlib.sha256(path.read_bytes()).hexdigest()
path=root/'report.json'
if path.is_symlink(): raise ValueError('report symlink')
data=b''
if path.is_file():
 with path.open('rb') as f: data=f.read(p['limit']+1)
result['report']=base64.b64encode(data).decode('ascii')
print(json.dumps(result))
'''


class DockerReplicaExecutor:
    def __init__(self, *, store, profile=None):
        self.store=store
        self.profile=profile or SphinxProfile()
        self.executions=[]  # Small host audit, includes every reserved owned name.

    async def execute(self, request: ReplicaRequest, started) -> ReplicaResult:
        if (request.environment.profile!='docker_linux' or request.environment.image_digest!=IMAGE.split('@')[1]
                or request.capture.base_revision!=REVISION or request.spec.adapter!='pytest8-v1'
                or request.spec.coverage!='regression_only' or request.spec.selectors!=('tests/test_util.py',)
                or request.spec.imports!=('sphinx',) or request.spec.runner_is_candidate):
            raise ContractError('Docker executor supports only the declared Sphinx public profile')
        transport=self.profile.new_workspace()
        coordinator=WorkspaceCoordinator(transport)
        provider=CaptureProvider(coordinator,self.store,base_revision=REVISION,policy=self.profile.policy)
        cancel=asyncio.Event()
        async def watch_cancel():
            while not cancel.is_set():
                if request.cancelled(): cancel.set(); return
                await asyncio.sleep(0.02)
        watcher=asyncio.create_task(watch_cancel())
        def control(phase, seconds, wire=32*1024**2):
            if request.cancelled(): cancel.set()
            return CaptureControl(request.operation_id+':'+phase,min(request.deadline_mono,time.monotonic()+seconds),
                                  cancel=cancel,wire_limit=wire)
        audit=dict(operation_id=request.operation_id,container_name=transport.name,container_id=None,
                   cleanup_ok=False,phase='reserved',reason=None)
        status, code, before, after, actual_environment='blocked',None,None,None,''
        out=err=report=b''
        truncated=False
        unchanged=False
        # This callback is durable BEFORE any container creation or helper operation.
        try:
            started(transport.name)
            self.executions.append(audit)
            audit['phase']='create'
            await coordinator.start(control('create',20))
            audit['container_id']=coordinator.transport_identity['container_id']
            audit['phase']='materialize'
            entries=await materialize(request.capture,self.store,provider,control('materialize',10))
            before=tree_id(entries)
            frozen={e.path:e for e in entries}
            if any(frozen.get(e.path)!=e for e in request.spec.protected_manifest):
                raise ContractError('frozen materialization mismatch')
            audit['phase']='environment'
            observed,facts=await self.profile.probe_environment(coordinator,control('environment',10,1024**2))
            actual_environment=observed.environment_id
            audit['environment_id']=actual_environment
            audit['probe_sha256']=blob_sha256(canonical_json(facts))
            if actual_environment!=request.environment.environment_id:
                raise ContractError('actual replica environment differs from frozen environment')
            root='/tmp/public-validation-'+uuid4().hex
            args=[*request.spec.selectors,*(arg.format(temp=root+'/pytest-tmp') for arg in request.spec.pytest_args)]
            payload=canonical_json(dict(workspace='/workspace',operation_id=request.operation_id,
                                       imports=list(request.spec.imports),args=args))
            files={'runner.py':base64.b64encode(RUNNER_BYTES).decode('ascii'),
                   'request.json':base64.b64encode(payload).decode('ascii')}
            audit['phase']='control'
            result=await coordinator.execute([observed.python_bin,'-I','-S','-c',WRITE_CONTROL],
                control('write-control',10),stdin=canonical_json(dict(root=root,files=files)))
            hashes=json.loads(result.stdout) if result.exit_code==0 else {}
            expected={'runner.py':RUNNER_SHA256,'request.json':blob_sha256(payload)}
            if hashes!=expected: raise ContractError('control upload failed integrity check')
            substitutions=dict(python=observed.python_bin,runner=root+'/runner.py',request=root+'/request.json',report=root+'/report.json')
            argv=[arg.format(**substitutions) for arg in request.spec.argv_template]
            audit['phase']='pytest'
            result=await coordinator.execute(argv,control('pytest',request.spec.limits.test_seconds,
                wire=max(4096,2*request.spec.limits.max_output_bytes)),env=dict(observed.env))
            code=result.exit_code
            status='succeeded' if code==0 else 'failed'
            truncated=any(len(data)>request.spec.limits.max_output_bytes for data in (result.stdout,result.stderr))
            out=result.stdout[:request.spec.limits.max_output_bytes]
            err=result.stderr[:request.spec.limits.max_output_bytes]
            audit['phase']='evidence'
            packet=await coordinator.execute([observed.python_bin,'-I','-S','-c',READ_CONTROL],
                control('read-control',10,2*request.spec.limits.max_report_bytes+8192),
                stdin=canonical_json(dict(root=root,limit=request.spec.limits.max_report_bytes)))
            if packet.exit_code: raise ContractError('control evidence unavailable')
            evidence=json.loads(packet.stdout)
            if set(evidence)!={'runner.py','request.json','report'}: raise ContractError('invalid control evidence')
            report=base64.b64decode(evidence.pop('report'),validate=True)
            unchanged=evidence==expected
            if len(report)>request.spec.limits.max_report_bytes: truncated=True
            report=report[:request.spec.limits.max_report_bytes]
            # Shared scanner proves a quiescent post-test tree, including frozen inputs.
            post=await provider.capture(control('post-capture',20))
            after=post.manifest_id
            audit['post_capture_id']=post.capture_id
            audit['phase']='environment-after'
            final_environment,_=await self.profile.probe_environment(coordinator,control('environment-after',10,1024**2))
            if final_environment.environment_id!=actual_environment:
                actual_environment=final_environment.environment_id
                raise ContractError('environment changed while testing')
            audit['phase']='complete'
        except CaptureError as exc:
            audit['reason']=exc.reason
            status=('cancelled' if exc.reason=='cancelled' else 'timed_out' if exc.reason in ('deadline','operation_timeout') else 'blocked')
            truncated=truncated or exc.reason=='wire_quota'
            err=(err+b'\n'+canonical_json(dict(adapter='docker-public-sphinx-v1',phase=audit['phase'],reason=exc.reason)))[:request.spec.limits.max_output_bytes]
        except asyncio.CancelledError:
            status='cancelled';audit['reason']='cancelled'
        except Exception as exc:
            status='blocked';audit['reason']=type(exc).__name__
            err=(err+b'\n'+canonical_json(dict(adapter='docker-public-sphinx-v1',phase=audit['phase'],reason=type(exc).__name__)))[:request.spec.limits.max_output_bytes]
        finally:
            cleanup_error = None
            try:
                async with asyncio.timeout(12):
                    audit['cleanup_ok']=await coordinator.close()
            except (Exception,asyncio.CancelledError) as exc:
                audit['cleanup_ok']=False
                cleanup_error=cleanup_exception(exc)
            audit['cleanup_evidence']=dict(container_name=transport.name,
                cleanup_ok=audit['cleanup_ok'],close_error=cleanup_error,
                attempts=copy.deepcopy(getattr(coordinator,'cleanup_evidence',[])))
            watcher.cancel()
            await asyncio.gather(watcher,return_exceptions=True)
        audit.update(execution_status=status,exit_code=code,before_manifest_id=before,after_manifest_id=after)
        return ReplicaResult(status,code,report,out,err,actual_environment,before,after,
                             'cleaned' if audit['cleanup_ok'] else 'cleanup_failed',truncated,unchanged,
                             cleanup_evidence=audit['cleanup_evidence'])
