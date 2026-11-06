"""Explicit opt-in REAL Docker acceptance. Enabled but unavailable is a failure."""
import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from src.execution.base import ExecutionRequest, ExecutionStatus, OutputQuotas, RunOutputMeter
from src.execution.docker import DockerExecutionBackend, DockerUnavailable, DEFAULT_IMAGE
from src.eval.contracts import PublicTask, EvaluationSpec, TestCase, canonical_hash
from src.eval.grader import IndependentGrader, tree_hash, save_json, regenerate_report

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.environ.get('HARNESS_DOCKER_TESTS') != '1', reason='opt in: HARNESS_DOCKER_TESTS=1; requires real Linux Docker')]


def backend(**kwargs):
    return DockerExecutionBackend(context=os.environ.get('HARNESS_DOCKER_CONTEXT', 'desktop-linux'), **kwargs)


def request(code, **kwargs):
    return ExecutionRequest(argv=['/usr/local/bin/python', '-I', '-S', '-c', code],
                            deadline_mono=time.monotonic()+20, **kwargs)


def evidence(name, data):
    if os.environ.get('HARNESS_H3_EVIDENCE'):
        save_json(Path(os.environ['HARNESS_H3_EVIDENCE'])/(name+'.json'), data)


async def test_container_isolation_and_actual_limits(tmp_path, monkeypatch):
    monkeypatch.setenv('H3_HOST_SECRET', 'never-copy-this')
    sentinel = tmp_path/'host-secret'; sentinel.write_text('host only')
    b = backend(workspace_bytes=4*1024*1024, tmpfs_bytes=2*1024*1024, pids_limit=24, memory_bytes=128*1024*1024, cpus=.5)
    try:
        await b.start({'public.txt': b'hello'})
        r = await b.execute(request('''import os,pathlib,socket,json,subprocess
assert 'H3_HOST_SECRET' not in os.environ
assert not pathlib.Path('/var/run/docker.sock').exists()
try: pathlib.Path('/root/.docker/config.json').read_bytes()
except (PermissionError,FileNotFoundError): pass
else: raise AssertionError('credential accessible')
assert not pathlib.Path('/host').exists()
assert not pathlib.Path('/workspace/.env').exists()
assert pathlib.Path('public.txt').read_text()=='hello'
try: pathlib.Path('/etc/cannot-write').write_text('x')
except OSError: pass
else: raise AssertionError('root writable')
s=socket.socket(); s.settimeout(.3)
assert s.connect_ex(('1.1.1.1',443))!=0
cg=pathlib.Path('/sys/fs/cgroup')
values={k:(cg/k).read_text().strip() for k in ('cpu.max','memory.max','pids.max')}
assert values['cpu.max']=='50000 100000',values
assert values['memory.max']=='134217728',values
assert values['pids.max']=='24',values
for path in ('/workspace/fill','/tmp/fill'):
    try:
        with open(path,'wb') as f:
            for _ in range(16): f.write(b'x'*1048576)
    except OSError: pass
    else: raise AssertionError('disk limit not enforced')
    finally: pathlib.Path(path).unlink(missing_ok=True)
children=[]
try:
    try:
        for _ in range(40): children.append(subprocess.Popen(['/bin/sleep','10']))
    except OSError: pass
    else: raise AssertionError('pid limit not enforced')
finally:
    for p in children: p.kill()
    for p in children: p.wait()
print(json.dumps(values))
'''))
        assert r.status == ExecutionStatus.SUCCEEDED, r.observation_text()
        evidence('isolation-limits', dict(capabilities=b.capabilities, observed=json.loads(r.stdout.text_preview), host_config=b.last_inspect['HostConfig']))
        await b.close(snapshot=True)
        assert b.snapshot == {'public.txt': b'hello'}
        assert sentinel.read_text() == 'host only'
    finally:
        await b.close()
    assert b.cleanup_ok


@pytest.mark.parametrize('mode', ['cancel', 'timeout', 'task_cancel'])
async def test_destroy_includes_setsid_descendants(mode):
    b = backend()
    await b.start()
    event = asyncio.Event()
    req = request('''import os,signal,time,pathlib
if os.fork()==0:
    os.setsid(); signal.signal(signal.SIGTERM,signal.SIG_IGN)
    pathlib.Path('ready').write_text(str(os.getpid()))
    while True: time.sleep(.1)
while True: time.sleep(.1)
''', cancel_event=event)
    if mode == 'timeout': req.deadline_mono = time.monotonic()+2
    work = asyncio.create_task(b.execute(req))
    try:
        for _ in range(50):
            code, _, _ = await b._control('exec', b.name, '/bin/test', '-f', '/workspace/ready')
            if code == 0: break
            await asyncio.sleep(.05)
        else: pytest.fail('descendant not ready')
        if mode == 'cancel': event.set()
        if mode == 'task_cancel': work.cancel()
        r = await asyncio.wait_for(work, 15)
        assert r.status == (ExecutionStatus.TIMED_OUT if mode == 'timeout' else ExecutionStatus.CANCELLED)
        # A timeout or cancel keeps the container for the timeout result and the workspace export.
        assert not b.closed
        assert await b.close() and b.cleanup_ok
        assert not (await b._ok('ps','-aq','--filter',f'name=^/{b.name}$')).strip()
        evidence('cleanup-'+mode, dict(status=r.status.value, cleanup_elapsed_s=r.cleanup_elapsed_s, lifecycle=b.lifecycle))
    finally:
        if not work.done(): work.cancel()
        await asyncio.gather(work, return_exceptions=True)
        await b.close()


PROCS = '''import os
for p in os.listdir('/proc'):
    if p.isdigit():
        try: print(open('/proc/'+p+'/cmdline','rb').read().replace(b'\\0',b' ').decode())
        except OSError: pass
'''


@pytest.mark.parametrize('mode', ['cancel', 'timeout', 'task_cancel'])
async def test_reap_on_timeout_kills_setsid_descendants(mode):
    b = backend(reap_on_timeout=True)
    await b.start()
    event = asyncio.Event()
    req = request('''import os,signal,time,pathlib
if os.fork()==0:
    os.setsid(); signal.signal(signal.SIGTERM,signal.SIG_IGN)
    pathlib.Path('ready').write_text(str(os.getpid()))
    while True: time.sleep(.1)
while True: time.sleep(.1)
''', cancel_event=event)
    if mode == 'timeout': req.deadline_mono = time.monotonic()+2
    work = asyncio.create_task(b.execute(req))
    try:
        for _ in range(50):
            code, _, _ = await b._control('exec', b.name, '/bin/test', '-f', '/workspace/ready')
            if code == 0: break
            await asyncio.sleep(.05)
        else: pytest.fail('descendant not ready')
        if mode == 'cancel': event.set()
        if mode == 'task_cancel': work.cancel()
        r = await asyncio.wait_for(work, 15)
        assert r.status == (ExecutionStatus.TIMED_OUT if mode == 'timeout' else ExecutionStatus.CANCELLED)
        assert not b.closed
        reaped = [e for e in b.lifecycle if e['event'] == 'exec_reaped']
        assert len(reaped) == 1 and reaped[0]['killed'] >= 2
        listing = (await b._ok('exec', b.name, b.python_bin, '-I', '-S', '-c', PROCS)).decode()
        assert 'os.setsid()' not in listing
        after = await b.execute(request('print("still usable")'))
        assert after.status == ExecutionStatus.SUCCEEDED and 'still usable' in after.stdout.text_preview
        evidence('reap-'+mode, dict(status=r.status.value, lifecycle=b.lifecycle))
    finally:
        if not work.done(): work.cancel()
        await asyncio.gather(work, return_exceptions=True)
        await b.close()


async def test_output_quota_and_oom_stop_session(tmp_path):
    b = backend()
    try:
        await b.start()
        r = await b.execute(request("import os\nwhile True: os.write(1,b'x'*8192); os.write(2,b'e'*8192)",
            quotas=OutputQuotas(1024, 4096, 65536), run_meter=RunOutputMeter(limit_bytes=65536), artifact_dir=tmp_path/'output'))
        assert r.error_kind == 'run_output_limit' and b.cleanup_ok, (r.observation_text(), b.lifecycle)
        assert r.stdout.bytes_kept_memory <= 1024 and r.stderr.bytes_kept_memory <= 1024
        assert sum(p.stat().st_size for p in (tmp_path/'output').iterdir()) <= 4096
    finally: await b.close()
    b = backend(memory_bytes=64*1024*1024)
    try:
        await b.start()
        r = await b.execute(request("x=bytearray(256*1024*1024); print(len(x))"))
        assert r.status != ExecutionStatus.SUCCEEDED and b.closed and b.cleanup_ok
        evidence('oom', dict(status=r.status.value, exit_code=r.exit_code, lifecycle=b.lifecycle))
    finally: await b.close()


async def test_startup_cancel_removes_created_container(monkeypatch):
    b = backend()
    created = asyncio.Event()
    original = b._ok
    async def delayed(*args, **kwargs):
        value = await original(*args, **kwargs)
        if args[0] == 'create':
            created.set()
            await asyncio.sleep(20)
        return value
    monkeypatch.setattr(b, '_ok', delayed)
    task = asyncio.create_task(b.start())
    try:
        await asyncio.wait_for(created.wait(), 20)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert b.cleanup_ok and not (await original('ps','-aq','--filter',f'name=^/{b.name}$')).strip()
    finally:
        task.cancel(); await asyncio.gather(task, return_exceptions=True)
        await b.close()


async def test_unavailable_never_falls_back(tmp_path):
    b = DockerExecutionBackend(context='h3-context-does-not-exist')
    with pytest.raises(DockerUnavailable): await b.start()
    r = await b.execute(request("open('must-not-exist','w').write('bad')", cwd=tmp_path))
    assert r.status == ExecutionStatus.BLOCKED
    assert not (tmp_path/'must-not-exist').exists()


@pytest.mark.parametrize('code', [126, 127])
async def test_command_lookup_failure_keeps_session_and_candidate(code):
    b = backend()
    try:
        await b.start({'edited.py': b'original\n'})
        assert (await b.execute(request("open('edited.py','w').write('candidate\\n')"))).status == ExecutionStatus.SUCCEEDED
        command = 'definitely_missing_h5_command' if code == 127 else 'exit 126'
        result = await b.execute(ExecutionRequest(shell_command=command, env={}, deadline_mono=time.monotonic()+10))
        assert result.status == ExecutionStatus.FAILED and result.exit_code == code
        assert result.error_kind == 'nonzero_exit'
        assert b.active and not b.closed
        assert (await b.execute(request("print('can recover')"))).status == ExecutionStatus.SUCCEEDED
        await b.close(snapshot=True)
        assert b.cleanup_ok and b.snapshot == {'edited.py': b'candidate\n'}
        evidence('recoverable-command-'+str(code), dict(status=result.status.value, exit_code=code,
                 cleanup_ok=b.cleanup_ok, candidate_preserved=True, lifecycle=b.lifecycle))
    finally:
        await b.close()


async def test_all_file_tools_container_only_and_path_denials(tmp_path):
    from src.tools.container_files import ContainerFileTool
    from src.tools.files import ReadFileTool, WriteFileTool, ListDirTool
    from src.tools.edit import EditFileTool
    from src.tools.patch import SearchReplaceTool
    from src.tools.base import ToolContext
    b = backend()
    ctx = ToolContext(workspace_root=tmp_path, call_id='host-assigned')
    try:
        await b.start()
        calls = [(WriteFileTool(), {'path':'x.txt','content':'first'}),
                 (EditFileTool(), {'path':'x.txt','old_string':'first','new_string':'second'}),
                 (SearchReplaceTool(), {'path':'x.txt','search':'second','replace':'third'}),
                 (ReadFileTool(), {'path':'x.txt'}), (ListDirTool(), {'path':'.'})]
        for tool, args in calls:
            r = await ContainerFileTool(tool,b).execute(args,ctx)
            assert r.success, r.output
            if tool.name=='read_file': assert 'third' in r.output
        for path in ('../etc/passwd','/etc/passwd','..\\secret'):
            r = await ContainerFileTool(ReadFileTool(),b).execute({'path':path},ctx)
            assert not r.success
        assert not (tmp_path/'x.txt').exists()
        await b.close(snapshot=True)
        assert b.snapshot == {'x.txt': b'third'}
    finally: await b.close()


@pytest.mark.parametrize('code', ["import os; os.symlink('/etc/passwd','bad')", "open('big','wb').write(b'x'*9437184)"])
async def test_bad_snapshot_fails_closed_and_still_cleans(code):
    b = backend()
    try:
        await b.start()
        assert (await b.execute(request(code))).status == ExecutionStatus.SUCCEEDED
        await b.close(snapshot=True)
        assert b.snapshot is None and b.snapshot_error and b.cleanup_ok
    finally: await b.close()


async def test_cleanup_failure_is_visible_and_session_unusable(monkeypatch):
    b = backend()
    await b.start()
    original = b._control
    async def unavailable(*args, **kwargs):
        if args[0] == 'rm': raise DockerUnavailable('injected daemon connection loss')
        return await original(*args, **kwargs)
    try:
        monkeypatch.setattr(b, '_control', unavailable)
        assert not await b.close()
        assert b.cleanup_ok is False
        assert (await b.execute(request('print(1)'))).status == ExecutionStatus.BLOCKED
    finally:
        monkeypatch.setattr(b, '_control', original)
        assert await b.close()


async def test_real_grader_fake_output_missing_dependency_and_valid_candidate():
    base = {'answer.py': b'def solve(x): return x\n', 'pytest.py': b'raise RuntimeError("must not import")\n'}
    spec = EvaluationSpec('probe', tree_hash(base), DEFAULT_IMAGE, ('answer.py',),
                          (TestCase('one', 'answer.py', 'solve', [1], 2), TestCase('two', 'answer.py', 'solve', [2], 3)))
    grader = IndependentGrader(context=os.environ.get('HARNESS_DOCKER_CONTEXT','desktop-linux'))
    valid = await grader.evaluate(base, {**base, 'answer.py': b'def solve(x): return x+1\n'}, spec)
    assert valid.status == 'pass' and valid.passed == 2
    fake = await grader.evaluate(base, {**base, 'answer.py': b'import os\nprint("HARNESS_PASSED=2")\nos._exit(0)\n'}, spec)
    assert fake.status == 'fail'
    missing = await grader.evaluate(base, {**base, 'answer.py': b'import h3_missing_dependency\n'}, spec)
    assert missing.status != 'pass'
    evidence('grader-negative-and-positive', [r.to_dict() for r in (valid, fake, missing)])


async def test_public_agent_session_files_and_independent_grader(tmp_path, monkeypatch):
    from src.eval.h3_runner import run_h3
    from src.harness.factory import build_agent, load_config
    from src.llm.client import LLMResponse, ToolCall
    from src.runtime.session import RunSession
    from tests.fixtures.mock_llm import MockLLMClient
    seed = tmp_path/'seed'; seed.mkdir()
    (seed/'answer.py').write_bytes(b'def solve(x): return x\n')
    (seed/'.env').write_text('SECRET=never-visible')
    (seed/'.oracle').mkdir(); (seed/'.oracle'/'hidden').write_text('private')
    base = {'answer.py': b'def solve(x): return x\n'}
    config = load_config('configs/docker.yaml')
    agent = build_agent(config, seed)
    await agent.llm.aclose()
    agent.llm = MockLLMClient([
        LLMResponse(text=None, tool_calls=[ToolCall(id='read', name='read_file', arguments={'path':'answer.py'})]),
        LLMResponse(text=None, tool_calls=[ToolCall(id='edit', name='edit_file', arguments={'path':'answer.py','old_string':'return x','new_string':'return x+1'})]),
        LLMResponse(text=None, tool_calls=[ToolCall(id='command', name='run_command', arguments={'argv':['/bin/cat','answer.py']})]),
        LLMResponse(text='ALL TESTS PASSED (untrusted)', tool_calls=[])])
    spec = EvaluationSpec('e2e', tree_hash(base), DEFAULT_IMAGE, ('answer.py',), (TestCase('hidden', 'answer.py','solve',[9],10),))
    output = Path(os.environ['HARNESS_H3_EVIDENCE'])/'e2e' if os.environ.get('HARNESS_H3_EVIDENCE') else tmp_path/'run'
    evaluation = await run_h3(agent, PublicTask('e2e', 'Fix solve to add one'), spec, output, config_hash=canonical_hash(config))
    assert evaluation.status == 'pass', evaluation.to_dict()
    assert (seed/'answer.py').read_bytes() == base['answer.py']
    assert set(agent.execution_backend.snapshot) == {'answer.py'}
    assert 'private_spec' not in repr(agent.llm.calls) and 'never-visible' not in repr(agent.llm.calls)
    replayed = RunSession.replay(output/'session')
    assert replayed.terminal['execution']['cleanup_ok']
    assert replayed.terminal['execution']['candidate_hash'] == tree_hash(agent.execution_backend.snapshot)
    original = (output/'evaluation'/'report.md').read_bytes()
    regenerate_report(output/'evaluation')
    assert (output/'evaluation'/'report.md').read_bytes() == original
