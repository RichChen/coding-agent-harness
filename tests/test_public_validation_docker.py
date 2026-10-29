"""Docker adapter policy/lifecycle units; real acceptance is the bounded demo."""
import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from src.validation.contracts import ValidationLimits
from src.validation.docker_replica import DockerReplicaExecutor
from src.validation.replica import ReplicaRequest
from src.validation.sphinx_profile import SphinxProfile, ValidationArtifacts, IMAGE, REVISION
from src.workspace.blobs import WorkspaceBlobStore
from src.workspace.contracts import ContractError, FileEntry, blob_sha256, tree_id
from src.workspace.control import CaptureError
from src.workspace.policy import CapturePolicy


def facts():
    return dict(python_bin='/usr/local/bin/python',python_version='3.9.25',pytest_version='8.4.2',
        pytest_file='/usr/local/lib/python3.9/site-packages/pytest/__init__.py',
        sphinx_file='/workspace/sphinx/__init__.py',distributions=[['pytest','8.4.2'],['Sphinx','3.5.0.dev0']])


def environment(**changes):
    data=facts(); data.update(changes)
    identity=dict(image_id=IMAGE.split('@')[1],image_digest=IMAGE.split('@')[1])
    return SphinxProfile().environment_from_probe(identity,'amd64',{'cpus':'1'},data)


@pytest.mark.parametrize('change',[
    {'python_version':'3.9.19'},{'pytest_version':'4.5.0'},
    {'pytest_file':'/workspace/src/pytest.py'},{'sphinx_file':'/opt/repo/sphinx/__init__.py'},
    {'distributions':[]},
])
def test_actual_probe_facts_are_required_not_requested_versions(change):
    with pytest.raises(ContractError): environment(**change)


def test_dependency_changes_change_environment_identity():
    a=environment()
    b=environment(distributions=[['pytest','8.4.2'],['Sphinx','changed']])
    assert a.environment_id!=b.environment_id


def test_workspace_blob_failures_are_validation_contract_errors(tmp_path):
    store=WorkspaceBlobStore(tmp_path/'store'); adapter=ValidationArtifacts(store)
    ref=adapter.publish(b'public evidence')
    assert adapter.read(ref)==b'public evidence'
    (store.root/ref.key).write_bytes(b'corrupt')
    with pytest.raises(ContractError,match='artifact_corrupt'): adapter.read(ref)


def request():
    env=environment()
    spec=SimpleNamespace(adapter='pytest8-v1',coverage='regression_only',selectors=('tests/test_util.py',),
        imports=('sphinx',),runner_is_candidate=False,limits=ValidationLimits(),protected_manifest=())
    capture=SimpleNamespace(base_revision=REVISION,manifest_id='declared-not-observed')
    return ReplicaRequest(capture,spec,env,'adapter-unit',time.monotonic()+120,lambda:False)


class FakeProfile:
    policy=CapturePolicy()
    def __init__(self,observed=None): self.observed=observed
    def new_workspace(self): return SimpleNamespace(name='unit-owned-no-container')
    async def probe_environment(self,coordinator,control): return self.observed,facts()


@pytest.mark.asyncio
@pytest.mark.parametrize('cleanup',[True,False])
async def test_failed_start_has_prior_intent_and_confirmed_or_failed_cleanup(monkeypatch,cleanup):
    import src.validation.docker_replica as module
    steps=[]
    class Coordinator:
        def __init__(self,transport): pass
        async def start(self,control): steps.append('create'); raise CaptureError('docker_control_failed')
        async def close(self): steps.append('cleanup'); return cleanup
    monkeypatch.setattr(module,'WorkspaceCoordinator',Coordinator)
    executor=DockerReplicaExecutor(store=object(),profile=FakeProfile())
    result=await executor.execute(request(),lambda name:steps.append('intent'))
    assert steps==['intent','create','cleanup']
    assert result.cleanup_state==('cleaned' if cleanup else 'cleanup_failed')
    assert result.environment_id=='' and result.before_manifest_id is None
    assert executor.executions[0]['reason']=='docker_control_failed'


@pytest.mark.asyncio
async def test_observed_manifest_and_environment_are_not_copied_from_request(monkeypatch):
    import src.validation.docker_replica as module
    class Coordinator:
        transport_identity={'container_id':'observed-unit-id'}
        def __init__(self,transport): pass
        async def start(self,control): pass
        async def close(self): return True
    entries=(FileEntry('sphinx/util.py','regular',0o644,3,blob_sha256(b'abc')),)
    async def materialize(*args): return entries
    monkeypatch.setattr(module,'WorkspaceCoordinator',Coordinator)
    monkeypatch.setattr(module,'materialize',materialize)
    observed=environment(distributions=[['pytest','8.4.2'],['Sphinx','different dependency']])
    executor=DockerReplicaExecutor(store=object(),profile=FakeProfile(observed))
    req=request()
    result=await executor.execute(req,lambda name:None)
    assert result.execution_status=='blocked' and result.cleanup_state=='cleaned'
    assert result.before_manifest_id==tree_id(entries)!=req.capture.manifest_id
    assert result.environment_id==observed.environment_id!=req.environment.environment_id
    assert result.after_manifest_id is None


@pytest.mark.asyncio
async def test_unsupported_pytest45_never_creates_replica():
    req=request(); req.spec.adapter='pytest45-v1'
    executor=DockerReplicaExecutor(store=object())
    with pytest.raises(ContractError): await executor.execute(req,lambda name:pytest.fail('no intent expected'))
    assert executor.executions==[]


@pytest.mark.asyncio
async def test_refused_started_callback_cleans_monitor_without_container_creation(monkeypatch):
    import src.validation.docker_replica as module
    steps=[]
    class Coordinator:
        def __init__(self,transport): pass
        async def start(self,control): pytest.fail('callback refused start')
        async def close(self): steps.append('cleanup'); return True
    monkeypatch.setattr(module,'WorkspaceCoordinator',Coordinator)
    def refused(name): raise CaptureError('cancelled')
    executor=DockerReplicaExecutor(store=object(),profile=FakeProfile())
    result=await executor.execute(request(),refused)
    assert steps==['cleanup'] and result.execution_status=='cancelled'
    assert executor.executions==[]


@pytest.mark.asyncio
async def test_demo_rejects_existing_output_before_creating_any_container(tmp_path,monkeypatch):
    import runpy
    demo=runpy.run_path(str(Path(__file__).parents[1]/'scripts/demo_public_validation.py'))
    def forbidden(self): pytest.fail('no Docker on output refusal')
    monkeypatch.setattr(SphinxProfile,'new_workspace',forbidden)
    sentinel=tmp_path/'existing.txt';sentinel.write_text('preserve',encoding='utf-8')
    with pytest.raises(FileExistsError): await demo['run_demo'](tmp_path)
    assert sentinel.read_text(encoding='utf-8')=='preserve' and len(list(tmp_path.iterdir()))==1
