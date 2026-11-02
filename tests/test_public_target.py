"""Public acceptance predicates/provenance without Docker or model calls."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import runpy
import zipfile
import pytest
from src.validation import public_target as target
from src.workspace.contracts import candidate_id

RUNNER=runpy.run_path(str(target.PACKAGE/'runner.py'))


def protocol():
    cases=[]
    for name in target.CASES:
        builders=['html','epub'] if name.startswith('html_') else ['epub']
        cases.append(dict(name=name,status='passed',builds=[dict(builder=b,exit_code=0,facts=dict(
            exit_code=0,sphinx_file='/workspace/sphinx/__init__.py',python_version='3.9.25')) for b in builders],
            archive='public.epub',archive_bytes=100,html_source_page=True,
            disk_module_pages=['_modules/p4_example.xhtml'] if name=='epub_true' else [],
            archive_module_pages=['_modules/p4_example.xhtml'] if name=='epub_true' else []))
    return dict(protocol='sphinx8721-public-target-v1',cases=cases,status='passed')


def test_pass_and_real_behavior_failure_are_different_from_invalid_build():
    data=protocol();assert target.classify_report(data,0)=='passed'
    for case in data['cases'][1:3]:
        case.update(status='behavior_failed',disk_module_pages=['_modules/p4_example.xhtml'],
                    archive_module_pages=['_modules/p4_example.xhtml'])
    data['status']='behavior_failed'
    assert target.classify_report(data,1)=='behavior_failed'
    data['cases'][0]['builds'][0]['exit_code']=3
    assert target.classify_report(data,1)=='inconclusive'


@pytest.mark.parametrize('bad',['empty','missing_case','wrong_source','wrong_exit','no_html','no_optin','inconclusive','status_only'])
def test_false_green_is_inconclusive(bad):
    data=protocol()
    if bad=='empty':data['cases'][0]['archive_bytes']=0
    elif bad=='missing_case':data['cases'].pop()
    elif bad=='wrong_source':data['cases'][0]['builds'][0]['facts']['sphinx_file']='/installed/sphinx.py'
    elif bad=='wrong_exit':data['cases'][0]['builds'][0]['facts']['exit_code']=1
    elif bad=='no_html':data['cases'][1]['html_source_page']=False
    elif bad=='no_optin':data['cases'][3]['archive_module_pages']=[]
    elif bad=='inconclusive':data['cases'][0]['status']='inconclusive'
    else:data={'status':'passed'}
    assert target.classify_report(data,0)=='inconclusive'


def test_inspect_epub_requires_real_nonempty_structure_and_checks_archive(tmp_path):
    (tmp_path/'index.xhtml').write_text('<html>Index</html>')
    path=tmp_path/'test.epub'
    def write(include_index=True,module=False):
        with zipfile.ZipFile(path,'w') as out:
            out.writestr('mimetype',b'application/epub+zip')
            out.writestr('META-INF/container.xml','container')
            out.writestr('content.opf','package')
            if include_index:out.writestr('index.xhtml','Index')
            if module:out.writestr('_modules/p4_example.xhtml','Source')
    write(False)
    with pytest.raises(ValueError):RUNNER['inspect_epub'](tmp_path)
    write(module=True)
    evidence=RUNNER['inspect_epub'](tmp_path)
    assert evidence['disk_module_pages']==[] and evidence['archive_module_pages']==['_modules/p4_example.xhtml']


def terminal_fixture():
    fixture=runpy.run_path(str(target.ROOT/'tests/fixtures/workspace_contracts/fixture.py'))
    capture,_=fixture['build_capture']()
    digest=target.IMAGE.split('@')[1]
    capture=replace(capture,image_digest=digest,candidate_id=candidate_id(base_revision=capture.base_revision,
        image_digest=digest,base_manifest_id=capture.base_manifest_id,manifest_id=capture.manifest_id,rules_id=capture.rules_id))
    capture=replace(capture,base_revision=target.REVISION,candidate_id=candidate_id(base_revision=target.REVISION,
        image_digest=digest,base_manifest_id=capture.base_manifest_id,manifest_id=capture.manifest_id,rules_id=capture.rules_id))
    ref={'sha256':'a'*64}
    snapshot=dict(terminal=dict(workspace_v1=dict(run=dict(cleanup_ok=True),delivery=dict(
        status='no_changes',candidate_id=capture.candidate_id,capture_ref=ref,final_candidate_unknown=False))),
        workspace=dict(sealed=dict(capture_ref=ref)),pending_model=None,pending_tools=[])
    return snapshot,capture


@pytest.mark.parametrize('bad',['no_terminal','no_seal','pending_model','pending_tool','pending_validation','dirty','wrong_candidate','cleanup'])
def test_only_terminal_sealed_candidate_is_eligible(bad):
    snapshot,capture=terminal_fixture();target.verify_terminal(snapshot,capture)
    if bad=='no_terminal':snapshot['terminal']=None
    elif bad=='no_seal':snapshot['workspace']['sealed']={}
    elif bad=='pending_model':snapshot['pending_model']={'pending':True}
    elif bad=='pending_tool':snapshot['pending_tools']=['tool']
    elif bad=='pending_validation':snapshot['workspace']['pending_validation']={'pending':True}
    elif bad=='dirty':snapshot['terminal']['workspace_v1']['delivery']['final_candidate_unknown']=True
    elif bad=='wrong_candidate':snapshot['terminal']['workspace_v1']['delivery']['candidate_id']='other'
    else:snapshot['terminal']['workspace_v1']['run']['cleanup_ok']=False
    with pytest.raises(ValueError):target.verify_terminal(snapshot,capture)


def test_arbitrary_directory_is_not_a_terminal_session(tmp_path):
    (tmp_path/'capture.json').write_text('{}')
    with pytest.raises(Exception):target.load_candidate(tmp_path)


@pytest.mark.skipif(not (target.ROOT / "runs/sprint5-main-public/sphinx-doc__sphinx-8721.json").is_file(), reason="needs runs/sprint5-main-public/sphinx-doc__sphinx-8721.json (not published)")
def test_task_is_complete_public_input_and_manifest_detects_material_change(monkeypatch,tmp_path):
    package=tmp_path/'public';package.mkdir()
    for name in target.MATERIALS:(package/name).write_bytes((target.PACKAGE/name).read_bytes())
    monkeypatch.setattr(target,'PACKAGE',package)
    frozen=target.package_manifest()
    (package/'check-manifest.json').write_bytes(target.canonical_json(frozen))
    text=target.public_task_input()
    assert 'Original public issue (verbatim)' in text and frozen['check_id'] in text
    assert 'viewcode creates pages for epub' in text
    for name in target.MATERIALS:
        assert (package/name).read_text(encoding='utf-8') in text
    (package/'runner.py').write_text('changed')
    with pytest.raises(ValueError,match='after freeze'):target.verify_package()


def large_terminal_session(tmp_path, size, item_limit):
    """Actual SQLite journal + artifact files; no mocked inspection/reader."""
    from src.runtime.session import RunSession
    from src.runtime.workspace_io import SessionWorkspaceArtifacts
    fixture=runpy.run_path(str(target.ROOT/'tests/fixtures/workspace_contracts/fixture.py'))
    body=b'#'+b' '*(size-2)+b'\n'
    files=dict(fixture['BASE_FILES'],**{'sphinx/large.py':body})
    cap,blobs=fixture['build_capture'](files)
    digest=target.IMAGE.split('@')[1]
    cap=replace(cap,base_revision=target.REVISION,image_digest=digest,candidate_id=candidate_id(
        base_revision=target.REVISION,image_digest=digest,base_manifest_id=cap.base_manifest_id,
        manifest_id=cap.manifest_id,rules_id=cap.rules_id))
    anchor=tmp_path/'anchor';anchor.mkdir()
    directory=tmp_path/'session'
    expected=dict(workspace=dict(path=str(anchor)),backend=dict(transport='workspace-v1'),code_id='public-target-size-fixture')
    with RunSession.create(directory,task_id='size-boundary',expected=expected,artifact_item_limit_bytes=item_limit) as session:
        bridge=SessionWorkspaceArtifacts(session)
        refs={key:bridge.put(data) for key,data in blobs.blobs.items()}
        cap=replace(cap,changed=tuple((p,refs[r.key]) for p,r in cap.changed),
                    corpus_blobs=tuple((p,refs[r.key]) for p,r in cap.corpus_blobs if r.size<=1024**2),
                    barrier_receipt=refs[cap.barrier_receipt.key])
        native=session.artifacts.publish(cap.to_json())
        report=dict(run=dict(cleanup_ok=True),delivery=dict(status='exported',candidate_id=cap.candidate_id,
                    capture_ref=native,final_candidate_unknown=False))
        session.append('workspace.started',{})
        session.append('workspace.capture.committed',dict(capture_id=cap.capture_id,candidate_id=cap.candidate_id,
                       capture_ref=native,_artifacts=[native]))
        session.append('completion.sealed',dict(capture_ref=native,record_ref=None,report=report))
        session.append('run.finished',dict(workspace_v1=report))
    return directory,cap,body


@pytest.mark.parametrize('size',[2*1024**2,2*1024**2+4])
@pytest.mark.parametrize('declared_limit',[3*1024**2,8*1024**2])
def test_candidate_reader_inherits_real_session_size_quota(tmp_path,size,declared_limit):
    from src.tracing.store import StoreError
    from src.workspace.contracts import ArtifactRef,blob_sha256
    directory,expected,body=large_terminal_session(tmp_path,size,declared_limit)
    capture,reader,source=target.load_candidate(directory)
    assert capture==expected and source['candidate_id']==capture.candidate_id
    assert reader.read(dict(capture.changed)['sphinx/large.py'])==body
    # Even a hash-correct orphan cannot bypass that exact declared item ceiling.
    too_large=b'x'*(declared_limit+1)
    checksum=blob_sha256(too_large)
    (directory/'artifacts'/checksum).write_bytes(too_large)
    with pytest.raises(StoreError,match='size mismatch'):
        reader.read(ArtifactRef(checksum,len(too_large),checksum))


@pytest.mark.parametrize('damage',['hash','size'])
def test_large_candidate_still_requires_original_blob_integrity(tmp_path,damage):
    from src.tracing.store import StoreError
    directory,cap,body=large_terminal_session(tmp_path,2*1024**2+4,8*1024**2)
    ref=dict(cap.changed)['sphinx/large.py']
    (directory/'artifacts'/ref.sha256).write_bytes(b'!'+body[1:] if damage=='hash' else body+b'!')
    with pytest.raises(StoreError,match='hash mismatch' if damage=='hash' else 'size mismatch'):
        target.load_candidate(directory)
