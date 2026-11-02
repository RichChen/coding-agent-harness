import runpy
from pathlib import Path
from dataclasses import replace
import pytest
from src.validation.replica import LocalFixtureExecutor
from src.validation.service import ValidationService
H=runpy.run_path(str(Path(__file__).parent/'fixtures/public_validation/support.py'))

@pytest.mark.asyncio
async def test_real_local_pytest_fixture_failure_then_fix_and_clean_replica(tmp_path):
    c,store,e,s,ev,ex,svc,ctl=H['setup']()
    materializer=H['Materializer'](store)
    svc=ValidationService(base_entries=c['C0'].entries,blobs=store,artifacts=store,
        executor=LocalFixtureExecutor(tmp_path,materializer),events=ev)
    baseline=await svc.validate(c['C0'],s,e,trigger='baseline',operation_id='baseline',control=ctl)
    assert baseline.status=='behavior_failed',baseline.to_dict()
    binding=svc.bind_baseline(s,e,baseline)
    record=await svc.validate(c['C2'],s,e,trigger='natural_end',operation_id='fixed',control=ctl,baseline=binding)
    assert record.status=='passed',record.to_dict()
    assert record.counts['passed']==1 and record.coverage['kind']=='fixture'
    assert not list(tmp_path.iterdir())
    # Original shared fixture/editor bytes were never rewritten.
    assert H['BASE_FILES']['pkg/calc.py']==b'def add(a, b):\n    return 0\n'

@pytest.mark.asyncio
async def test_real_test_self_mutation_is_not_a_current_candidate_pass(tmp_path):
    c,store,e,s,ev,ex,svc,ctl=H['setup']()
    original=H['Materializer'](store)
    executor=LocalFixtureExecutor(tmp_path,original)
    svc=ValidationService(base_entries=c['C0'].entries,blobs=store,artifacts=store,executor=executor,events=ev)
    base=await svc.validate(c['C0'],s,e,trigger='baseline',operation_id='baseline',control=ctl)
    binding=svc.bind_baseline(s,e,base)
    files=dict(H['BASE_FILES'])
    files['pkg/calc.py']=b"from pathlib import Path\ndef add(a,b):\n    Path('data/rules.json').write_text('changed')\n    return a+b\n"
    mutated,blobs=H['build_capture'](files); store.data.update(blobs.blobs)
    r=await svc.validate(mutated,s,e,trigger='natural_end',operation_id='mutates',control=ctl,baseline=binding)
    assert (r.status,r.reason)==('inconclusive','input_mutated'),r.to_dict()
    assert H['BASE_FILES']['data/rules.json']==b'{"operation":"add"}\n'
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize('body,reason',[
    (b"import os\nos._exit(0)\n",'evidence_incomplete'),
    (b"import pytest\ndef add(a,b):\n    pytest.skip('skip via implementation')\n",'no_effective_tests'),
    (b"def add(a,b):\n    print('passed '*20000)\n    return 0\n",'evidence_incomplete'),
    (b"import time\ndef add(a,b):\n    time.sleep(10)\n    return a+b\n",'timeout'),
])
async def test_real_fixture_false_green_skip_output_limit_and_timeout(tmp_path,body,reason):
    c,store,e,s,ev,ex,svc,ctl=H['setup']()
    materializer=H['Materializer'](store)
    svc=ValidationService(base_entries=c['C0'].entries,blobs=store,artifacts=store,
        executor=LocalFixtureExecutor(tmp_path,materializer),events=ev)
    baseline=await svc.validate(c['C0'],s,e,trigger='baseline',operation_id='base',control=ctl)
    binding=svc.bind_baseline(s,e,baseline)
    if reason=='timeout':
        s=replace(s,limits=replace(s.limits,test_seconds=1))
        # Changed policy requires its own baseline; use a fresh task ledger/service.
        c,store,e,_,ev,ex,_,ctl=H['setup']()
        materializer=H['Materializer'](store)
        svc=ValidationService(base_entries=c['C0'].entries,blobs=store,artifacts=store,
            executor=LocalFixtureExecutor(tmp_path,materializer),events=ev)
        baseline=await svc.validate(c['C0'],s,e,trigger='baseline',operation_id='base',control=ctl)
        binding=svc.bind_baseline(s,e,baseline)
    files=dict(H['BASE_FILES']); files['pkg/calc.py']=body
    cap,blobs=H['build_capture'](files); store.data.update(blobs.blobs)
    r=await svc.validate(cap,s,e,trigger='natural_end',operation_id='bad',control=ctl,baseline=binding)
    assert (r.status,r.reason)==('inconclusive',reason),r.to_dict()
    assert not list(tmp_path.iterdir())
