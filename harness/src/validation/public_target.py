"""Independent, fixed public acceptance; does not produce B ValidationRecords."""
from __future__ import annotations
import asyncio
import base64
import json
from pathlib import Path
import time
from uuid import uuid4

from src.validation.sphinx_profile import SphinxProfile, IMAGE, REVISION, PUBLIC_ENV
from src.workspace.blobs import WorkspaceBlobStore
from src.workspace.capture import CaptureProvider
from src.workspace.contracts import (WorkspaceCapture, canonical_json, content_id, blob_sha256, read_verified_blob)
from src.workspace.control import CaptureControl, CaptureError
from src.workspace.coordinator import WorkspaceCoordinator
from src.workspace.materialize import materialize

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / 'tests/fixtures/p4_sphinx8721'
MATERIALS = ('task.md', 'runner.py', 'conf.py', 'index.rst', 'p4_example.py')
CASES = ('fresh_default', 'html_epub_default', 'html_epub_false', 'epub_true')
OUTPUT_LIMIT = 4 * 1024**2
TOTAL_SECONDS = 180
UPLOAD = r'''
import json,sys,pathlib,base64,hashlib
p=json.load(sys.stdin); root=pathlib.Path(p['root']); root.mkdir(mode=0o700)
allowed={'runner.py','conf.py','index.rst','p4_example.py'}
if set(p['files'])!=allowed: raise ValueError('invalid public materials')
for name,encoded in p['files'].items():
 data=base64.b64decode(encoded,validate=True); f=root/name; f.write_bytes(data); f.chmod(0o444)
print(json.dumps({n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in allowed}))
'''
DOWNLOAD = r'''
import json,sys,pathlib,hashlib,base64
p=json.load(sys.stdin); root=pathlib.Path(p['root']); hashes={}
for name in ('runner.py','conf.py','index.rst','p4_example.py'):
 f=root/name
 if f.is_symlink() or not f.is_file(): raise ValueError('control missing')
 hashes[name]=hashlib.sha256(f.read_bytes()).hexdigest()
f=root/'report.json'
if f.is_symlink() or not f.is_file(): raise ValueError('report missing')
with f.open('rb') as stream: raw=stream.read(p['limit']+1)
print(json.dumps(dict(hashes=hashes,report=base64.b64encode(raw).decode())))
'''


def package_manifest():
    files = {name: blob_sha256((PACKAGE/name).read_bytes()) for name in MATERIALS}
    payload = dict(version='p4-public-target-v1', source='https://github.com/sphinx-doc/sphinx/issues/8720',
        public_input_sha256=blob_sha256((ROOT/'runs/sprint5-main-public/sphinx-doc__sphinx-8721.json').read_bytes()),
        revision=REVISION, image=IMAGE, files=files, cases=list(CASES),
        host_sha256=blob_sha256(Path(__file__).read_bytes()),
        cli_sha256=blob_sha256((ROOT/'scripts/check_public_target.py').read_bytes()),
        limits=dict(total_seconds=TOTAL_SECONDS,cleanup_seconds=12,output_bytes=OUTPUT_LIMIT))
    return dict(check_id=content_id('public-target-v1',payload), **payload)


def verify_package():
    frozen = json.loads((PACKAGE/'check-manifest.json').read_bytes())
    if frozen != package_manifest():
        raise ValueError('public check package changed after freeze')
    return frozen


def public_task_input():
    frozen=verify_package()
    issue=json.loads((ROOT/'runs/sprint5-main-public/sphinx-doc__sphinx-8721.json').read_bytes())
    return ('# Original public issue (verbatim)\n\n'+issue['issue_text']+
        '\n\n# Host-authored task constraints and public acceptance\n\n'+
        'Frozen check identity: '+frozen['check_id']+'\n\n'+(PACKAGE/'task.md').read_text(encoding='utf-8') + ''.join(
        '\n\n## Public acceptance material: '+name+'\n```\n'+(PACKAGE/name).read_text(encoding='utf-8')+'\n```\n'
        for name in MATERIALS if name != 'task.md'))


def verify_terminal(snapshot, capture):
    terminal = snapshot.get('terminal')
    workspace = snapshot.get('workspace') or {}
    sealed = workspace.get('sealed') or {}
    report = (terminal or {}).get('workspace_v1') or {}
    delivery = report.get('delivery') or {}
    if (not terminal or not sealed.get('capture_ref') or snapshot.get('pending_model')
            or snapshot.get('pending_tools') or workspace.get('pending_validation')
            or delivery.get('status') not in ('exported','no_changes')
            or delivery.get('final_candidate_unknown',True)
            or delivery.get('candidate_id') != capture.candidate_id
            or delivery.get('capture_ref') != sealed.get('capture_ref')
            or report.get('run',{}).get('cleanup_ok') is not True):
        raise ValueError('requires a clean terminal session and its final sealed capture')
    if capture.base_revision != REVISION or capture.image_digest != IMAGE.split('@')[1] or not capture.admissible:
        raise ValueError('unsupported sealed candidate')


def load_candidate(session_path, export_path=None):
    """No arbitrary directory/capture option: require the durable terminal journal.

    Optional exports are cross-checked against that source journal. An export
    manifest alone has no terminal-journal proof and is deliberately insufficient.
    """
    from src.runtime.workspace_io import inspect_workspace_session
    from src.runtime.session import RunSession
    from src.tracing.store import EventStore
    from src.tracing.artifacts import ArtifactStore
    snapshot = inspect_workspace_session(session_path)  # validates nested committed artifacts
    sealed = snapshot.get('workspace',{}).get('sealed') or {}
    ref = sealed.get('capture_ref')
    if not ref: raise ValueError('no final sealed capture')
    with EventStore(Path(session_path),readonly=True) as events:
        # Normal replay restores both artifact ceilings from run.started. Keep
        # its verified declaration instead of introducing a second 2MiB default.
        session = RunSession(events,ArtifactStore(Path(session_path)/'artifacts',events.run_id,readonly=True))
        native = session.artifacts
        capture = WorkspaceCapture.from_json(native.read(ref))
    verify_terminal(snapshot,capture)
    class Reader:
        def read(self, value):
            if value.key != value.sha256: raise ValueError('unexpected session blob key')
            return native.read(dict(sha256=value.sha256,size_bytes=value.size,path=value.key,run_id=native.run_id))
    reader = Reader()
    capture.validate_blobs(reader)
    if export_path is not None:
        export = Path(export_path)
        manifest = json.loads((export/'manifest.json').read_bytes())
        exported = WorkspaceCapture.from_json((export/'capture.json').read_bytes())
        if (manifest.get('schema_version')!='workspace-export-v1' or manifest.get('final') is not True
                or manifest.get('candidate_id')!=capture.candidate_id or exported!=capture
                or manifest.get('report')!=snapshot['terminal']['workspace_v1']
                or manifest.get('pending')):
            raise ValueError('export is not the final source-session candidate')
        for value in (capture.barrier_receipt,*(r for _,r in capture.changed),*(r for _,r in capture.corpus_blobs)):
            path=export/'blobs'/value.sha256
            if path.is_symlink() or path.parent.is_symlink(): raise ValueError('unsafe export blob')
            raw=path.read_bytes()
            if len(raw)!=value.size or blob_sha256(raw)!=value.sha256: raise ValueError('corrupt export blob')
    return capture, reader, dict(run_id=snapshot['run_id'],candidate_id=capture.candidate_id,
                                capture_id=capture.capture_id,source='terminal-session')


def classify_report(data, exit_code):
    """Recompute each public predicate; a printed status alone is insufficient."""
    try:
        if data['protocol']!='sphinx8721-public-target-v1' or tuple(c['name'] for c in data['cases'])!=CASES:
            raise ValueError('case coverage')
        statuses=[]
        for case in data['cases']:
            expected=['html','epub'] if case['name'].startswith('html_') else ['epub']
            if case['status']=='inconclusive': raise ValueError('inconclusive case')
            if [b['builder'] for b in case['builds']]!=expected: raise ValueError('build coverage')
            for build in case['builds']:
                facts=build['facts']
                if (build['exit_code']!=0 or facts.get('exit_code')!=0 or facts.get('error_type')
                        or facts['sphinx_file']!='/workspace/sphinx/__init__.py' or facts['python_version']!='3.9.25'):
                    raise ValueError('build/source mismatch')
            if case['archive_bytes']<=0 or not case['archive'].endswith('.epub'): raise ValueError('empty archive')
            if len(expected)==2 and case.get('html_source_page') is not True: raise ValueError('HTML control')
            disk, archive=case['disk_module_pages'],case['archive_module_pages']
            if not isinstance(disk,list) or not isinstance(archive,list): raise ValueError('page evidence')
            ok=(any(p.endswith('p4_example.xhtml') for p in disk) and any(p.endswith('p4_example.xhtml') for p in archive)
                if case['name']=='epub_true' else not disk and not archive)
            status='passed' if ok else 'behavior_failed'
            if status!=case['status']: raise ValueError('predicate mismatch')
            statuses.append(status)
        result='passed' if all(s=='passed' for s in statuses) else 'behavior_failed'
        if data['status']!=result or exit_code!=(0 if result=='passed' else 1): raise ValueError('process mismatch')
        return result
    except (KeyError,ValueError,TypeError,AttributeError):
        return 'inconclusive'


async def check_target(output, *, capture=None, reader=None, source=None):
    frozen=verify_package()  # before any Docker work
    output=Path(output).absolute();output.mkdir(parents=True,exist_ok=False)
    store=WorkspaceBlobStore(output/'capture-store')
    profile=SphinxProfile();coordinator=WorkspaceCoordinator(profile.new_workspace())
    provider=CaptureProvider(coordinator,store,base_revision=REVISION,policy=profile.policy)
    deadline=time.monotonic()+TOTAL_SECONDS
    def control(name,seconds=None,wire=32*1024**2):
        return CaptureControl('public-target:'+name,min(deadline,time.monotonic()+seconds) if seconds else deadline,wire_limit=wire)
    record=dict(schema='public-target-result-v1',check_id=frozen['check_id'],status='inconclusive',
        source=source or dict(source='original-base'),image=IMAGE,revision=REVISION,
        container=coordinator.transport_identity,cleanup_ok=False,phase='start',limits=frozen['limits'])
    started=time.monotonic()
    try:
        await coordinator.start(control('start',60))
        record['container']=coordinator.transport_identity
        record['phase']='capture-or-materialize'
        if capture is None:
            capture=await provider.capture(control('base-capture',60));reader=store
            if capture.changed or capture.deleted: raise ValueError('base is not clean')
        else:
            await materialize(capture,reader,provider,control('materialize',60))
        (output/'checked-capture.json').write_bytes(capture.to_json())
        record.update(candidate_id=capture.candidate_id,manifest_id=capture.manifest_id,capture_id=capture.capture_id,
                      capture_sha256=blob_sha256(capture.to_json()))
        record['phase']='environment'
        environment,facts=await profile.probe_environment(coordinator,control('environment',10))
        record.update(environment_id=environment.environment_id,environment_probe=facts)
        root='/tmp/p4-public-target-'+uuid4().hex
        files={name:base64.b64encode((PACKAGE/name).read_bytes()).decode() for name in MATERIALS if name!='task.md'}
        expected={name:frozen['files'][name] for name in files}
        upload=await coordinator.execute(['/usr/local/bin/python','-I','-S','-c',UPLOAD],control('upload',10),
                                        stdin=canonical_json(dict(root=root,files=files)))
        if upload.exit_code or json.loads(upload.stdout)!=expected: raise ValueError('public material upload mismatch')
        record['phase']='target-builds'
        result=await coordinator.execute(['/usr/local/bin/python','-I','-B',root+'/runner.py',root,
                                         str(int(control('remaining').remaining(None)*1000))],
                                        control('target-builds',wire=OUTPUT_LIMIT),env=PUBLIC_ENV)
        (output/'stdout.bin').write_bytes(result.stdout);(output/'stderr.bin').write_bytes(result.stderr)
        record['exit_code']=result.exit_code
        packet=await coordinator.execute(['/usr/local/bin/python','-I','-S','-c',DOWNLOAD],
            control('report',10,wire=OUTPUT_LIMIT*2),stdin=canonical_json(dict(root=root,limit=OUTPUT_LIMIT)))
        if packet.exit_code: raise ValueError('missing target report')
        payload=json.loads(packet.stdout);raw=base64.b64decode(payload['report'],validate=True)
        if len(raw)+len(result.stdout)+len(result.stderr)>OUTPUT_LIMIT: raise ValueError('output limit')
        (output/'target-report.json').write_bytes(raw)
        if payload['hashes']!=expected: raise ValueError('public control material mutated')
        data=json.loads(raw);record['cases']=data.get('cases',[])
        record['phase']='post-capture'
        after=await provider.capture(control('post-capture',20))
        record['after_manifest_id']=after.manifest_id
        if after.manifest_id!=capture.manifest_id or after.candidate_id!=capture.candidate_id:
            raise ValueError('candidate input mutated')
        final_environment,_=await profile.probe_environment(coordinator,control('post-environment',10))
        if final_environment.environment_id!=environment.environment_id: raise ValueError('environment mutated')
        control('final').remaining(None)
        record['status']=classify_report(data,result.exit_code)
        record.update(phase='complete',control_hashes=payload['hashes'],report_sha256=blob_sha256(raw))
    except (Exception,asyncio.CancelledError) as exc:
        record.update(status='inconclusive',reason=getattr(exc,'reason',type(exc).__name__),detail=str(exc))
    finally:
        try:
            if coordinator.state=='failed':
                # operation() already performed its bounded owned cleanup. Do
                # not grant a second cleanup allowance after an unknown result.
                record['cleanup_ok']=coordinator.cleanup_ok is True
            else:
                async with asyncio.timeout(12): record['cleanup_ok']=await coordinator.close()
        except (Exception,asyncio.CancelledError): record['cleanup_ok']=False
        if not record['cleanup_ok']: record.update(status='inconclusive',reason='cleanup_failed')
        record['elapsed_ms']=int((time.monotonic()-started)*1000)
        (output/'check-manifest.json').write_bytes(canonical_json(frozen))
        (output/'result.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    return record
