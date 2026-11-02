"""Short local subprocess checks of Docker transport deadlines; no Docker calls."""
import asyncio
import json
import sys
import time

import pytest

from src.workspace.control import CaptureControl, CaptureError
from src.workspace.coordinator import WorkspaceCoordinator
from src.workspace.docker_transport import DockerWorkspace


class ShortManagementControl(CaptureControl):
    """Scale the default management cap to 20ms without altering the deadline."""
    def remaining(self, maximum=30.0):
        return super().remaining(None if maximum is None else min(maximum, 0.02))


class LocalOwnedTransport(DockerWorkspace):
    """Use real CLI I/O/reaping; only ownership startup/cleanup are in memory."""
    async def start(self, control):
        self.container_id = "local-process-fixture"

    async def close(self, control):
        self.cleanup_ok = True
        return True


async def local_transport(monkeypatch):
    # Acquire the real child handle first so the shortened cap tests execution,
    # independently of Windows process creation latency.
    child = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-S", "-c", "import time; time.sleep(.15); print('finished')",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

    async def spawn(*args, **kwargs):
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    transport = LocalOwnedTransport(image="fixture@sha256:" + "1" * 64)
    coordinator = WorkspaceCoordinator(transport)
    await coordinator.start(CaptureControl("start", time.monotonic() + 5))
    return child, transport, coordinator


@pytest.mark.asyncio
async def test_ordinary_command_outlives_management_cap_with_90_second_deadline(monkeypatch):
    child, transport, coordinator = await local_transport(monkeypatch)
    try:
        result = await coordinator.execute(["public-test-command"], ShortManagementControl(
            "public-check", time.monotonic() + 90))
        assert result.exit_code == 0 and result.stdout.strip() == b"finished"
        assert coordinator.state == "active" and transport.cleanup_ok is None
    finally:
        assert await coordinator.close()
        assert child.returncode is not None


@pytest.mark.asyncio
async def test_management_rpc_still_has_its_internal_cap(monkeypatch):
    child, transport, coordinator = await local_transport(monkeypatch)
    try:
        with pytest.raises(CaptureError, match="operation_timeout"):
            await transport.cli(ShortManagementControl("management", time.monotonic() + 90), "info")
        assert child.returncode is not None
    finally:
        assert await coordinator.close()


@pytest.mark.asyncio
async def test_ordinary_command_never_extends_its_absolute_deadline(monkeypatch):
    child, transport, coordinator = await local_transport(monkeypatch)
    with pytest.raises(CaptureError, match="deadline"):
        await coordinator.execute(["public-test-command"], ShortManagementControl(
            "public-check", time.monotonic() + .02))
    assert child.returncode is not None
    assert coordinator.state == "failed" and transport.cleanup_ok and coordinator.cleanup_ok


@pytest.mark.asyncio
async def test_helper_uses_caller_deadline_instead_of_management_cap(monkeypatch):
    child, transport, coordinator = await local_transport(monkeypatch)
    try:
        result = await transport.helper({"op": "seed"}, ShortManagementControl(
            "initial-seed", time.monotonic()+60))
        assert result.strip() == b"finished" and child.returncode == 0
    finally:
        assert await coordinator.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["deadline", "cancel"])
async def test_helper_reaps_process_and_closes_workspace_at_caller_stop(monkeypatch, stop):
    child, transport, coordinator = await local_transport(monkeypatch)
    cancelled = asyncio.Event()
    control = CaptureControl("seed-stop", time.monotonic()+(.02 if stop=="deadline" else 60), cancelled)
    if stop == "cancel": asyncio.get_running_loop().call_later(.02, cancelled.set)
    with pytest.raises(CaptureError, match="deadline" if stop=="deadline" else "cancelled"):
        async with coordinator.operation(control):
            await transport.helper({"op": "seed"}, control)
    assert child.returncode is not None and coordinator.state == "failed" and coordinator.cleanup_ok


@pytest.mark.asyncio
async def test_cleanup_waits_for_uncertain_daemon_create_then_removes_owned_id():
    transport = DockerWorkspace(image="fixture@sha256:"+"1"*64)
    transport.reserved = transport.create_pending = True
    calls = []
    queries = 0
    async def ok(control, *args, **kwargs):
        nonlocal queries
        calls.append(args)
        if args[0] == "ps":
            queries += 1
            return b"" if queries in (1, 3) else b"owned-id"
        if args[0] == "inspect":
            return json.dumps([dict(Id="owned-id", Config=dict(Labels={"coding-harness.workspace-v1": transport.owner}))]).encode()
        return b""
    transport.ok = ok
    assert await transport.close(CaptureControl("cleanup", time.monotonic()+2))
    assert ("rm", "-f", "owned-id") in calls and not transport.create_pending


@pytest.mark.asyncio
async def test_absent_but_unsettled_daemon_create_cannot_claim_cleanup():
    transport = DockerWorkspace(image="fixture@sha256:"+"1"*64)
    transport.reserved = transport.create_pending = True
    async def ok(control, *args, **kwargs): return b""
    transport.ok = ok
    with pytest.raises(CaptureError, match="deadline"):
        await transport.close(CaptureControl("cleanup", time.monotonic()+.05))
    assert transport.cleanup_ok is not True and transport.create_pending
