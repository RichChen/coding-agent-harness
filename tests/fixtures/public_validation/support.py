"""Independent trusted fixture support, never a production capture/materializer."""
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys
import time
import pytest

from src.workspace.contracts import ArtifactRef, FileEntry, WorkspaceCapture, content_id
from src.validation.contracts import PublicValidationSpec, ValidationEnvironment, ValidationLimits
from src.validation.replica import RUNNER_ID, RUNNER_SHA256, ReplicaResult
from src.validation.ledger import ValidationLedger, ByteBudget
from src.validation.service import ValidationControl, ValidationService

SHARED = runpy.run_path(str(Path(__file__).parents[1] / 'workspace_contracts/fixture.py'))
BASE_FILES = SHARED['BASE_FILES']
build_capture = SHARED['build_capture']
reference_entries = SHARED['reference_entries']
reference_id = SHARED['reference_id']


class Store:
    def __init__(self, fixture_blobs): self.data = fixture_blobs.blobs
    def read(self, ref): return self.data[ref.key]
    def publish(self, data):
        sha = hashlib.sha256(data).hexdigest(); key = 'blobs/' + sha
        self.data[key] = data
        return ArtifactRef(sha, len(data), key)


class Events:
    def __init__(self): self.rows = []
    def emit(self, name, payload):
        from src.validation.contracts import plain
        self.rows.append((name, plain(payload)))


def environment(**changes):
    env = dict(PATH=os.environ.get('PATH', ''), PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
               PYTHONDONTWRITEBYTECODE='1', PYTHONPATH='/workspace:/workspace/src')
    if os.name == 'nt': env['SYSTEMROOT'] = os.environ['SYSTEMROOT']
    values = dict(image_digest='sha256:'+'1'*64, image_id='sha256:'+'1'*64,
        architecture='fixture-host', python_bin=sys.executable,
        python_version='.'.join(str(v) for v in sys.version_info[:3]), pytest_version=pytest.__version__,
        dependency_fingerprint=content_id('fixture-deps-v1', {'pytest':pytest.__version__}),
        env=env, profile='local_fixture', resources={'isolation':'trusted-fixture-only'})
    return ValidationEnvironment(**(values | changes))


def spec(base, **changes):
    values = dict(provenance={'repo':'fixture','commit':'fixture-base','paths':['tests/test_calc.py'],
        'frozen_at':'2026-10-30T00:00:00Z'},
        protected_manifest=tuple(e for e in base.entries if e.path != 'pkg/calc.py'),
        selectors=('tests/test_calc.py',), runner_id=RUNNER_ID, adapter='pytest9-fixture-v1',
        coverage='fixture', scope='artificial add fixture only',
        target_evidence={'source':'artificial fixture'},
        known_fixture_failures=('tests/test_calc.py::test_add',), imports=('pkg.calc',),
        limits=ValidationLimits(test_seconds=8, total_seconds=10, max_output_bytes=32768,
                                max_report_bytes=32768))
    return PublicValidationSpec(**(values | changes))


def receipt(env, operation_id='op', *, nodeids=('tests/test_calc.py::test_add',), failed=False):
    phases = []
    for node in nodeids:
        for when in ('setup','call','teardown'):
            failure = failed and when=='call'
            phases.append(dict(nodeid=node,when=when,outcome='failed' if failure else 'passed',
                xfail=None,exception='builtins.AssertionError' if failure else None,detail='assert failed' if failure else ''))
    return dict(protocol='public-pytest-v1', operation_id=operation_id,runner_sha256=RUNNER_SHA256,
        complete=True,collection_finished=True,nodeids=list(nodeids),phases=phases,collection_errors=[],
        deselected=[],sessionfinish=True,exitstatus=1 if failed else 0,returned_exitstatus=1 if failed else 0,
        runner_error=None,python_version=env.python_version,pytest_version=env.pytest_version,
        origins={'pytest':'installed','pkg.calc':'workspace'})


class FakeExecutor:
    def __init__(self, transform=None, failed=False):
        self.calls = 0; self.transform = transform; self.failed = failed
    async def execute(self, request, started):
        self.calls += 1
        started('fixture-owned-' + request.operation_id)
        d = receipt(request.environment,request.operation_id,failed=self.failed)
        result = ReplicaResult('failed' if self.failed else 'succeeded',1 if self.failed else 0,
            json.dumps(d).encode(),b'output',b'',request.environment.environment_id,
            request.capture.manifest_id,request.capture.manifest_id,'cleaned')
        return self.transform(result,request) if self.transform else result


class Materializer:
    def __init__(self, store): self.store = store; self.outputs=[]
    def prepare(self, capture, workspace):
        files = dict(BASE_FILES)
        for path in capture.deleted: files.pop(path)
        for path,ref in capture.changed: files[path] = self.store.read(ref)
        for path,data in files.items():
            p=workspace/path; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(data)
        return self.verify(capture,workspace)
    def verify(self,capture,workspace):
        # Independent tiny fixture oracle. Linux modes are virtual 0644 on Windows;
        # this deliberately makes no claim to validate production filesystem modes.
        files={p.relative_to(workspace).as_posix():p.read_bytes() for p in workspace.rglob('*') if p.is_file()
            and not any(part in ('__pycache__','.pytest_cache') for part in p.relative_to(workspace).parts)
            and p.suffix not in ('.pyc','.pyo')}
        self.outputs.append(files)
        return reference_id('tree-v1',reference_entries(files))


def setup(executor=None, events=None):
    captures, blobs = SHARED['build_sequence'](); store=Store(blobs)
    env=environment(); public=spec(captures['C0']); events=events or Events()
    executor=executor or FakeExecutor(failed=True)
    service=ValidationService(base_entries=captures['C0'].entries,blobs=store,artifacts=store,
                              executor=executor,events=events)
    control=ValidationControl(time.monotonic()+1000,ValidationLedger(),ByteBudget(4*1024**2,1048576))
    return captures,store,env,public,events,executor,service,control
