import json
import runpy
from pathlib import Path
from dataclasses import replace
import pytest
from src.validation.parser import classify
from src.validation.replica import RUNNER_SHA256
from src.workspace.contracts import ContractError

H=runpy.run_path(str(Path(__file__).parent/'fixtures/public_validation/support.py'))


def parse(data=None, **kwargs):
    captures,store,env,spec,*_ = H['setup']()
    options=dict(operation_id='op',runner_sha256=RUNNER_SHA256,spec=spec,environment=env,
                 execution_status='succeeded',exit_code=0,expected_nodeids=('tests/test_calc.py::test_add',))
    return classify(json.dumps(data or H['receipt'](env)).encode(),**(options|kwargs))


def test_real_protocol_pass_and_assertion_failure():
    assert parse().status=='passed'
    env=H['environment'](); d=H['receipt'](env,failed=True)
    r=parse(d,execution_status='failed',exit_code=1)
    assert (r.status,r.reason,r.complete)==('behavior_failed','test_failure',True)


@pytest.mark.parametrize('change,reason',[
    (lambda d:d.update(complete=False),'evidence_incomplete'),
    (lambda d:d.update(sessionfinish=False),'evidence_incomplete'),
    (lambda d:d.update(collection_finished=False),'evidence_incomplete'),
    (lambda d:d.update(operation_id='other'),'report_mismatch'),
    (lambda d:d.update(runner_sha256='0'*64),'report_mismatch'),
    (lambda d:d.update(returned_exitstatus=1),'report_mismatch'),
    (lambda d:d.update(exitstatus=True),'report_mismatch'),
    (lambda d:d.update(nodeids=[]),'no_effective_tests'),
    (lambda d:d.update(deselected=['omitted']),'coverage_changed'),
    (lambda d:d.update(nodeids=['different']),'coverage_changed'),
    (lambda d:d['phases'].pop(),'evidence_incomplete'),
    (lambda d:d['phases'].append(dict(d['phases'][0])),'evidence_incomplete'),
    (lambda d:d['phases'][0].update(outcome='failed'),'environment_error'),
    (lambda d:d.update(collection_errors=['import failed']),'collection_error'),
    (lambda d:d.update(origins={'pytest':'workspace','pkg.calc':'workspace'}),'environment_mismatch'),
    (lambda d:d.update(pytest_version='8.4.2'),'environment_mismatch'),
    (lambda d:d.update(runner_error='ImportError'),'environment_error'),
    (lambda d:d['phases'][1].update(outcome='skipped'),'no_effective_tests'),
    (lambda d:d['phases'][1].update(xfail='known xpass'),'coverage_changed'),
])
def test_incomplete_or_inconsistent_evidence_never_passes(change,reason):
    d=H['receipt'](H['environment']()); change(d)
    result=parse(d); assert result.status=='inconclusive'; assert result.reason==reason


@pytest.mark.parametrize('kwargs,reason',[
    ({'execution_status':'timed_out','exit_code':None},'timeout'),
    ({'execution_status':'cancelled','exit_code':None},'cancelled'),
    ({'execution_status':'failed','exit_code':0},'report_mismatch'),
    ({'truncated':True},'evidence_incomplete'),
    ({'input_unchanged':False},'input_mutated'),
    ({'cleanup_state':'cleanup_failed'},'cleanup_failed'),
])
def test_process_inputs_and_cleanup_are_required(kwargs,reason):
    assert parse(**kwargs).reason==reason


def test_stdout_summary_or_junit_are_not_receipts():
    env=H['environment'](); c,_,_,s,*_=H['setup']()
    for raw in [b'8 passed',b'<testsuite tests="8"/>',b'{}',b'{"complete":true,"complete":true}']:
        r=classify(raw,operation_id='op',runner_sha256=RUNNER_SHA256,spec=s,environment=env,
                   execution_status='succeeded',exit_code=0)
        assert r.status=='inconclusive'


def test_call_exception_is_not_automatically_target_behavior():
    d=H['receipt'](H['environment'](),failed=True)
    d['phases'][1]['exception']='builtins.ImportError'
    assert parse(d,execution_status='failed',exit_code=1).reason=='environment_error'


def test_public_spec_freezes_identity_and_rejects_private_fields():
    c,_,env,s,*_=H['setup']()
    with pytest.raises(TypeError): s.provenance['repo']='changed'
    assert replace(s,scope='different scope').spec_id!=s.spec_id
    for field in ['gold_patch','test_patch','FAIL_TO_PASS','private_source_binding']:
        with pytest.raises(ContractError): replace(s,provenance=dict(s.provenance)|{field:'hidden'})
    with pytest.raises(ContractError): replace(env,env=dict(env.env)|{'PYTEST_ADDOPTS':'--fixtures'})
    with pytest.raises(ContractError): replace(s,adapter='unknown')
    with pytest.raises(ContractError): replace(s,coverage='regression_only')


@pytest.mark.parametrize('adapter,version,python_version',[
    ('pytest8-v1','8.4.2','3.9.25'),('pytest45-v1','4.5.0','3.9.19')])
def test_synthetic_supported_adapter_receipts_only_not_real_environment(adapter,version,python_version):
    # Deliberately synthetic: verifies schema/version policy, not actual old pytest hooks.
    _,_,_,spec,*_=H['setup']()
    env=H['environment'](pytest_version=version,python_version=python_version)
    spec=replace(spec,adapter=adapter,coverage='regression_only',target_evidence=None,
                 known_fixture_failures=())
    result=parse(H['receipt'](env),environment=env,spec=spec)
    assert result.status=='passed'
    with pytest.raises(ContractError): replace(spec,pytest_args=('--fixtures',))
    with pytest.raises(ContractError): replace(spec,argv_template=('sh','-c','pytest | tail'))


@pytest.mark.parametrize('mode',['partial_skip','xfail','strict_xpass','phase_reorder'])
def test_partial_skip_xfail_and_order_do_not_certify_coverage(mode):
    d=H['receipt'](H['environment'](),nodeids=('a','b'))
    if mode=='phase_reorder': d['phases'][0],d['phases'][1]=d['phases'][1],d['phases'][0]
    elif mode=='partial_skip': d['phases'][1]['outcome']='skipped'
    elif mode=='xfail': d['phases'][1].update(outcome='skipped',xfail='expected')
    else: d['phases'][1].update(outcome='failed',xfail='strict_xpass')
    r=parse(d,expected_nodeids=('a','b'))
    assert r.status=='inconclusive'
