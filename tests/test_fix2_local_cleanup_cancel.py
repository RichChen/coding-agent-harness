"""A cancel that lands in LocalExecutionBackend's cleanup must not skip the rest of it."""
import asyncio
import time

import pytest

from src.execution.base import ExecutionRequest, ExecutionStatus
from src.execution.local import LocalExecutionBackend


class StubbornProcess:
    """Ignores terminate(); only kill() ends it."""

    pid = 4321

    def __init__(self):
        self.stdout, self.stderr, self.stdin = asyncio.StreamReader(), asyncio.StreamReader(), None
        self.returncode = None
        self.terminated = False
        self._exited = asyncio.Event()

    def terminate(self):
        self.terminated = True

    def kill(self):
        if self.returncode is None:
            self.returncode = -9
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self._exited.set()

    async def wait(self):
        await self._exited.wait()
        return self.returncode


@pytest.fixture
def spawned(monkeypatch):
    procs = []

    async def spawn(*args, **kwargs):
        procs.append(StubbornProcess())
        return procs[-1]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    return procs


async def start_and_wait_for_terminate(spawned):
    request = ExecutionRequest(argv=["sleep", "60"], deadline_mono=time.monotonic() + .05, cleanup_grace_s=2)
    work = asyncio.create_task(LocalExecutionBackend("legacy_host").execute(request))
    while not (spawned and spawned[0].terminated):
        await asyncio.sleep(.01)
    return work, spawned[0]


@pytest.mark.asyncio
async def test_cancel_during_process_reclaim_still_kills_and_returns(spawned):
    work, proc = await start_and_wait_for_terminate(spawned)
    work.cancel()
    try:
        result = await work
    except asyncio.CancelledError:
        result = None
    assert proc.returncode == -9
    assert result is not None and result.status == ExecutionStatus.TIMED_OUT
    assert result.cleanup_state.value == "cleaned"


@pytest.mark.asyncio
async def test_repeated_cancels_during_reclaim_do_not_escape(spawned):
    work, proc = await start_and_wait_for_terminate(spawned)
    for _ in range(3):
        work.cancel()
        await asyncio.sleep(.05)
    result = await work
    assert proc.returncode == -9
    assert result.status == ExecutionStatus.TIMED_OUT
