"""The first supported public profile: immutable cached Sphinx + pytest 8.

No task/grader manifests are read. Environment facts come from an owned live
container; selectors/protection are declared public policy, never hidden nodes.
"""
from __future__ import annotations
import json
from dataclasses import dataclass

from src.validation.contracts import PublicValidationSpec, ValidationEnvironment
from src.validation.replica import RUNNER_ID
from src.workspace.contracts import ContractError, canonical_json, content_id
from src.workspace.docker_transport import DockerWorkspace
from src.workspace.policy import CapturePolicy

IMAGE = "coding-harness-a5-clean@sha256:1a6e20fb4b37194f8596111bc3658261cc40dbd85dc09a0e5be6d6690220b4e2"
REVISION = "82ef497a8c88f0f6e50d84520e7276bfbf65025d"
PUBLIC_ENV = {"PATH":"/usr/local/bin:/usr/bin:/bin", "LANG":"C.UTF-8", "HOME":"/tmp", "TMPDIR":"/tmp",
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD":"1", "PYTHONPATH":"/workspace:/workspace/src", "PYTHONDONTWRITEBYTECODE":"1"}
PROBE_CODE = r'''
import sys,json,importlib.metadata as md,pathlib
sys.path[:0]=['/workspace','/workspace/src']
import pytest,sphinx
print(json.dumps(dict(python_bin=sys.executable,python_version='.'.join(map(str,sys.version_info[:3])),
    pytest_version=pytest.__version__,pytest_file=str(pathlib.Path(pytest.__file__).resolve()),
    sphinx_file=str(pathlib.Path(sphinx.__file__).resolve()),
    distributions=sorted([[d.metadata['Name'],d.version] for d in md.distributions()], key=lambda x:(x[0].lower(),x[1])))))
'''


@dataclass(frozen=True)
class SphinxProfile:
    context: str = "desktop-linux"

    @property
    def policy(self): return CapturePolicy(editable_root="sphinx")

    def new_workspace(self):
        return DockerWorkspace(image=IMAGE, context=self.context, python="/usr/local/bin/python", cpus="1",
            memory_bytes=1024**3,workspace_bytes=256*1024**2,tmp_bytes=64*1024**2,pids_limit=128)

    def environment_from_probe(self, identity, architecture, resource_profile, facts):
        if (identity['image_digest'] != IMAGE.split('@')[1]
                or identity['image_id'] != identity['image_digest'] or architecture != 'amd64'
                or facts['python_bin'] != '/usr/local/bin/python' or facts['python_version'] != '3.9.25'
                or facts['pytest_version'] != '8.4.2'
                or not facts['pytest_file'].startswith('/usr/local/lib/python3.9/site-packages/pytest/')
                or facts['sphinx_file'] != '/workspace/sphinx/__init__.py'):
            raise ContractError('unsupported Sphinx image/interpreter/import source')
        if (not isinstance(facts['distributions'],list) or not facts['distributions']
                or any(not isinstance(v,list) or len(v)!=2 or not all(isinstance(x,str) and x for x in v)
                       for v in facts['distributions'])):
            raise ContractError('dependency probe incomplete')
        resources = dict(resource_profile, network='none', readonly_root=True, user='1000:1000',
            host_mounts=False, cap_drop=['ALL'], no_new_privileges=True)
        return ValidationEnvironment(image_digest=identity['image_digest'],image_id=identity['image_id'],
            architecture='linux/'+architecture,python_bin=facts['python_bin'],python_version=facts['python_version'],
            pytest_version=facts['pytest_version'],dependency_fingerprint=content_id('dependencies-v1',facts['distributions']),
            env=PUBLIC_ENV,profile='docker_linux',resources=resources,
            initialization_version=content_id('sphinx-replica-v1',dict(policy_id=self.policy.rules_id,
                runner_id=RUNNER_ID,materialize='workspace-materialize-v1',profile='sphinx8721-public-v1')))

    async def probe_environment(self, coordinator, control):
        # Image inspect and the interpreter probe both use the same coordinator.
        async with coordinator.operation(control) as transport:
            architecture=(await transport.ok(control,'image','inspect',IMAGE,'--format','{{.Architecture}}',limit=4096)).decode().strip()
            identity=coordinator.transport_identity
            resources=dict(transport.profile)
        result=await coordinator.execute(['/usr/local/bin/python','-I','-B','-c',PROBE_CODE],control,env=PUBLIC_ENV)
        if result.exit_code: raise ContractError('Sphinx environment probe failed')
        facts=json.loads(result.stdout)
        environment=self.environment_from_probe(identity,architecture,resources,facts)
        return environment, facts

    def build_spec(self, capture, environment):
        if (capture.base_revision!=REVISION or capture.image_digest!=environment.image_digest
                or capture.origin.get('image_id')!=environment.image_id or capture.changed or capture.deleted
                or capture.manifest_id!=capture.base_manifest_id or not capture.admissible):
            raise ContractError('profile requires verified unmodified Sphinx base')
        protected=tuple(e for e in capture.entries if not e.path.startswith('sphinx/')
                        or e.path.startswith('sphinx/testing/')
                        or e.path.rsplit('/',1)[-1] in self.policy.config()['frozen_names'])
        return PublicValidationSpec(provenance=dict(repo='sphinx-doc/sphinx',commit=REVISION,
            paths=['tests/test_util.py'],issue_source='public regression only; no target bug claim',
            frozen_at=capture.captured_at),protected_manifest=protected,selectors=('tests/test_util.py',),
            runner_id=RUNNER_ID,adapter='pytest8-v1',imports=('sphinx',),coverage='regression_only',
            scope='Sphinx base tests/test_util.py only; excludes target issue certification')


class ValidationArtifacts:
    """Thin error/publication bridge, not a second blob store or scanner."""
    def __init__(self, store): self.store=store
    def read(self, ref):
        from src.workspace.control import CaptureError
        try: return self.store.read(ref)
        except CaptureError as exc: raise ContractError('validation artifact '+exc.reason) from exc
    def publish(self, data): return self.store.put(data)
