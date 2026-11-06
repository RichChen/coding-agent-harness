"""The Docker backend's busy flag must not outlive a timed-out command (fix plan 1, F2)."""
import asyncio
import time

import pytest

from src.execution import docker
from src.execution.base import ExecutionRequest, ExecutionStatus
from src.execution.docker import DockerExecutionBackend
from src.runtime.awaitctl import await_with_control
from src.runtime.deadline import DeadlineClock, DeadlineExceeded


class Process:
    """A `docker exec` CLI that stays silent until killed, or exits at once with *output*."""

    def __init__(self, output=None):
        self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
        self.stdin = None
        self.returncode = None
        self._exited = asyncio.Event()
        if output is not None:
            self.stdout.feed_data(output)
            self._exit(0)

    def _exit(self, code):
        self.returncode = code
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        self._exited.set()

    def kill(self):
        if self.returncode is None:
            self._exit(-9)

    async def wait(self):
        await self._exited.wait()
        return self.returncode


@pytest.fixture
def backend(monkeypatch):
    spawned = []

    async def spawn(*args, **kwargs):
        proc = Process(b"ok\n" if args[-1] == "quick" else None)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    b = DockerExecutionBackend()
    b.active = True
    b.spawned = spawned
    return b


def request(command, deadline_mono=None):
    return ExecutionRequest(argv=["/bin/sh", "-c", command], deadline_mono=deadline_mono)


async def assert_usable(backend):
    assert backend._busy is False
    after = await backend.execute(request("quick"))
    assert after.error_kind != "concurrent_operation"
    assert after.status == ExecutionStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_cancel_during_timeout_cleanup_releases_busy(backend):
    # execute() notices its own deadline first and enters cleanup; the outer
    # tool deadline (same instant in production) then cancels it mid-cleanup.
    work = asyncio.create_task(backend.execute(request("sleep 60", time.monotonic() + .05)))
    await asyncio.sleep(.12)
    assert backend._busy and not work.done()
    work.cancel()
    try:
        result = await work
    except asyncio.CancelledError:
        result = None
    assert backend._busy is False
    assert result is not None and result.status == ExecutionStatus.TIMED_OUT
    assert all(p.returncode is not None for p in backend.spawned)
    await assert_usable(backend)


@pytest.mark.asyncio
async def test_tool_timeout_through_await_with_control_releases_busy(backend):
    for _ in range(8):
        clock = DeadlineClock()
        clock.begin_tool(.05)
        with pytest.raises(DeadlineExceeded):
            await await_with_control(backend.execute(request("sleep 60", clock.effective_deadline_mono())),
                                     deadline=clock, cleanup_grace_s=12)
        clock.clear_tool()
        await assert_usable(backend)


@pytest.mark.asyncio
async def test_failed_cli_reap_with_running_collectors_releases_busy(backend, monkeypatch):
    reap = docker._reap_cli

    async def reap_fails(proc, tasks):
        raise RuntimeError("reap failed")

    monkeypatch.setattr(docker, "_reap_cli", reap_fails)
    result = await backend.execute(request("sleep 60", time.monotonic() + .05))
    assert result.status == ExecutionStatus.TIMED_OUT
    for proc in backend.spawned:
        proc.kill()
    monkeypatch.setattr(docker, "_reap_cli", reap)
    await assert_usable(backend)


@pytest.mark.asyncio
async def test_truly_concurrent_execute_is_still_rejected(backend):
    first = asyncio.create_task(backend.execute(request("sleep 60", time.monotonic() + .3)))
    await asyncio.sleep(.05)
    second = await backend.execute(request("quick"))
    assert second.status == ExecutionStatus.BLOCKED
    assert second.error_kind == "concurrent_operation"
    assert (await first).status == ExecutionStatus.TIMED_OUT
    await assert_usable(backend)
