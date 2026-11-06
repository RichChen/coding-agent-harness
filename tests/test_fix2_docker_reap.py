"""docker_reap_on_timeout: a timed-out or cancelled command's processes are killed inside the kept container.

No Docker here: the docker CLI process and the control calls are faked. The real-container check is
test_docker_execution.py::test_reap_on_timeout_kills_setsid_descendants (HARNESS_DOCKER_TESTS=1).
"""
import asyncio
import copy
import time
from pathlib import Path

import pytest

from src.eval.contracts import canonical_hash
from src.execution.base import ExecutionRequest, ExecutionStatus
from src.execution.docker import REAP_CODE, DockerExecutionBackend, DockerUnavailable
from src.harness.factory import build_agent, load_config
from src.runtime.session import runtime_fingerprint

ROOT = Path(__file__).resolve().parents[1]


class HangingCli:
    """A `docker exec` client that only ends when killed."""

    def __init__(self, exit_code=None):
        self.stdout, self.stderr, self.stdin = asyncio.StreamReader(), asyncio.StreamReader(), None
        self.returncode = None
        self._exited = asyncio.Event()
        if exit_code is not None:
            self._end(exit_code)

    def _end(self, code):
        self.returncode = code
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        self._exited.set()

    def kill(self):
        if self.returncode is None:
            self._end(-9)

    async def wait(self):
        await self._exited.wait()
        return self.returncode


@pytest.fixture
def docker(monkeypatch):
    def make(reap, *, exit_code=None, reap_error=None):
        b = DockerExecutionBackend(reap_on_timeout=reap)
        b.active = True
        b.commands, b.controls = [], []

        async def spawn(*argv, **kwargs):
            b.commands.append(list(argv))
            return HangingCli(exit_code)

        async def ok(*args, **kwargs):
            b.controls.append(args)
            if reap_error:
                raise reap_error
            return b"2\n"

        async def close(**kwargs):
            b.closed, b.active = True, False
            return True

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        b._ok, b.close = ok, close
        return b
    return make


def request(**kwargs):
    return ExecutionRequest(argv=["/bin/sleep", "60"], **kwargs)


def exec_id_of(command):
    marks = [a for a in command if a.startswith("HARNESS_EXEC_ID=")]
    return marks[0].split("=", 1)[1] if marks else None


@pytest.mark.asyncio
async def test_default_leaves_the_command_alone(docker):
    b = docker(False)
    r = await b.execute(request(deadline_mono=time.monotonic() + .1))
    assert r.status == ExecutionStatus.TIMED_OUT
    assert exec_id_of(b.commands[0]) is None
    assert b.controls == [] and not b.closed
    assert "exec_reap_on_timeout" not in b.capabilities


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["timeout", "cancel"])
async def test_reap_kills_marked_processes_and_keeps_the_container(docker, mode):
    b = docker(True)
    event = asyncio.Event()
    deadline = time.monotonic() + (.1 if mode == "timeout" else 20)
    work = asyncio.create_task(b.execute(request(deadline_mono=deadline, cancel_event=event)))
    if mode == "cancel":
        await asyncio.sleep(.1)
        event.set()
    r = await work
    assert r.status == (ExecutionStatus.TIMED_OUT if mode == "timeout" else ExecutionStatus.CANCELLED)
    exec_id = exec_id_of(b.commands[0])
    assert exec_id and len(exec_id) == 32
    assert b.controls == [("exec", b.name, b.python_bin, "-I", "-S", "-c", REAP_CODE, exec_id)]
    assert b.lifecycle[-1] == dict(event="exec_reaped", kind="tool_timeout" if mode == "timeout" else "user_cancelled",
                                   killed=2)
    assert not b.closed
    assert b.capabilities["exec_reap_on_timeout"] is True


@pytest.mark.asyncio
async def test_failed_reap_destroys_the_container(docker):
    b = docker(True, reap_error=DockerUnavailable("Docker exec failed"))
    r = await b.execute(request(deadline_mono=time.monotonic() + .1))
    assert r.status == ExecutionStatus.TIMED_OUT
    assert b.lifecycle[-1]["event"] == "exec_reap_failed"
    assert b.closed


@pytest.mark.asyncio
async def test_finished_command_is_not_reaped_and_ids_differ(docker):
    b = docker(True, exit_code=0)
    for _ in range(2):
        r = await b.execute(request(deadline_mono=time.monotonic() + 20))
        assert r.status == ExecutionStatus.SUCCEEDED
    first, second = (exec_id_of(c) for c in b.commands)
    assert first and second and first != second
    assert b.controls == []


def test_reap_flag_must_be_bool():
    with pytest.raises(ValueError):
        DockerExecutionBackend(reap_on_timeout="yes")


def test_config_reaches_the_backend_and_changes_hash_and_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "offline")
    base = load_config(ROOT / "configs/docker.yaml")
    base["tools"]["enabled"] = ["run_command"]
    assert "docker_reap_on_timeout" not in base["policy"]["command"]
    changed = copy.deepcopy(base)
    changed["policy"]["command"]["docker_reap_on_timeout"] = True

    off, on = build_agent(base, tmp_path / "off"), build_agent(changed, tmp_path / "on")
    backend = {name: next(t for t in agent.tools.tools() if t.name == "run_command").backend
               for name, agent in (("off", off), ("on", on))}
    assert backend["off"].reap_on_timeout is False and backend["on"].reap_on_timeout is True
    assert canonical_hash(changed) != canonical_hash(base)
    assert runtime_fingerprint(on) != runtime_fingerprint(off)


def test_workspace_transport_refuses_the_option(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "offline")
    config = load_config(ROOT / "configs/workspace/p4-sphinx-deepseek-delayed-mask.yaml")
    config["policy"]["command"]["docker_reap_on_timeout"] = True
    with pytest.raises(ValueError, match="workspace transport"):
        build_agent(config, tmp_path)
