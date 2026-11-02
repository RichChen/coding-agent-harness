"""Foundation tests with an injected fake Docker boundary, no daemon required."""
import asyncio
import base64
import json
import time
from dataclasses import replace

import pytest

from src.workspace.blobs import WorkspaceBlobStore
from src.workspace.capture import CaptureProvider, parse_snapshot
from src.workspace.contracts import ArtifactRef, FileEntry, blob_sha256
from src.workspace.control import CaptureControl, CaptureError
from src.workspace.coordinator import WorkspaceCoordinator
from src.workspace.docker_transport import CommandResult
from src.workspace.materialize import materialize
from src.workspace.policy import CapturePolicy


def control(**kwargs):
    return CaptureControl("test", time.monotonic() + 10, **kwargs)


class FakeDocker:
    image_digest = "sha256:" + "1" * 64
    image_id = "sha256:" + "2" * 64
    name, container_id, context = "fake-owned", "fake-id", "fake"

    def __init__(self):
        self.files = {"sphinx/a.py": b"x = 0\n", "tests/test_a.py": b"assert True\n", "data.bin": b"\x00\xff"}
        self.active_calls = self.maximum_calls = 0
        self.closed = False
        self.error = None
        self.truncate = False
        self.delay = 0

    async def start(self, control):
        pass

    async def helper(self, request, control):
        assert self.active_calls == 0
        if self.error:
            return json.dumps({"error": self.error}).encode()
        if request["op"] == "apply":
            for path in request["deleted"]:
                del self.files[path]
            for path, data in request["changed"]:
                self.files[path] = base64.b64decode(data)
        entries = [FileEntry(p, "regular", 0o644, len(b), blob_sha256(b)).to_dict()
                   for p, b in sorted(self.files.items())]
        base = {e["path"]: e for e in request.get("base") or entries}
        changed = [e["path"] for e in entries if e["path"] not in base or e != base[e["path"]]]
        corpus = [e["path"] for e in entries if e["path"].endswith(".py")]
        lines = [{"entry": e} for e in entries]
        if request["op"] != "apply":
            for path in sorted(set(changed) | set(corpus)):
                body = self.files[path]
                for offset in range(0, max(1, len(body)), 48 * 1024):
                    lines.append(dict(blob=path, offset=offset, data=base64.b64encode(body[offset:offset+48*1024]).decode()))
        records = [dict(pid=p, starttime=100+p, threads=[p]) for p in (1, 7)]
        lines.append(dict(complete=True, changed=changed, deleted=sorted(set(base) - set(self.files)),
                          corpus=corpus, excluded=[], barrier=dict(protocol="quiescence-v1", before=records,
                          after=records, pid1=records[0])))
        wire = b"\n".join(json.dumps(line).encode() for line in lines) + b"\n"
        return wire.rsplit(b"\n", 2)[0] if self.truncate else wire

    async def execute(self, argv, control, **kwargs):
        self.active_calls += 1
        self.maximum_calls = max(self.maximum_calls, self.active_calls)
        try:
            await control.wait(asyncio.sleep(self.delay))
            return CommandResult(0, b"", b"")
        finally:
            self.active_calls -= 1

    async def close(self, control):
        assert self.active_calls == 0
        self.closed = True
        return True


async def started(tmp_path, transport=None, policy=None):
    transport = transport or FakeDocker()
    coordinator = WorkspaceCoordinator(transport)
    await coordinator.start(control())
    provider = CaptureProvider(coordinator, WorkspaceBlobStore(tmp_path), base_revision="test-base", policy=policy)
    return provider, coordinator, transport


@pytest.mark.asyncio
async def test_capture_delta_is_base_relative_and_published_atomically(tmp_path):
    provider, coordinator, transport = await started(tmp_path)
    base = await provider.capture(control())
    transport.files["sphinx/a.py"] = b"x = 1\n"
    changed = await provider.capture(control())
    repeated = await provider.capture(control())
    assert base.changed == base.deleted == ()
    assert changed.changed == repeated.changed
    assert changed.candidate_id == repeated.candidate_id != base.candidate_id
    assert len(list((tmp_path / "captures").iterdir())) == 3
    changed.validate_against_base(base.entries)
    changed.validate_blobs(provider.store)
    transport.error = "workspace_not_quiescent"
    with pytest.raises(CaptureError, match="workspace_not_quiescent"):
        await provider.capture(control())
    assert provider.last_capture == repeated
    assert len(list((tmp_path / "captures").iterdir())) == 3
    assert coordinator.state == "failed" and coordinator.cleanup_ok and transport.closed


@pytest.mark.asyncio
async def test_frozen_change_saved_but_not_admissible_and_fresh_materialization(tmp_path):
    provider, _, t = await started(tmp_path / "edit")
    base = await provider.capture(control())
    t.files["sphinx/a.py"] = b"x = 1\n"
    capture = await provider.capture(control())
    replica, _, _ = await started(tmp_path / "replica")
    entries = await materialize(capture, provider.store, replica, control())
    assert entries == capture.entries
    t.files["tests/test_a.py"] = b"assert False\n"
    bad = await provider.capture(control())
    assert not bad.admissible and bad.candidate_id != capture.candidate_id
    fresh, _, _ = await started(tmp_path / "reject")
    with pytest.raises(CaptureError, match="unsupported_change"):
        await materialize(bad, provider.store, fresh, control())
    assert fresh.last_capture is None
    assert base.base_manifest_id == capture.base_manifest_id


@pytest.mark.asyncio
async def test_lock_serializes_commands_capture_close_and_timeout_poisons(tmp_path):
    provider, coordinator, transport = await started(tmp_path)
    transport.delay = 0.025
    await asyncio.gather(*(coordinator.execute(["true"], control()) for _ in range(3)), provider.capture(control()))
    assert transport.maximum_calls == 1
    transport.delay = 2
    with pytest.raises(CaptureError, match="deadline"):
        await coordinator.execute(["sleep"], CaptureControl("timeout", time.monotonic() + 0.02))
    assert transport.closed and coordinator.state == "failed"
    with pytest.raises(CaptureError, match="unavailable"):
        await provider.capture(control())


@pytest.mark.asyncio
async def test_cancellation_and_waiting_lock_do_not_run_work(tmp_path):
    _, coordinator, transport = await started(tmp_path)
    event = asyncio.Event()
    event.set()
    with pytest.raises(CaptureError, match="cancelled"):
        await coordinator.execute(["true"], control(cancel=event))
    assert transport.maximum_calls == 0
    await coordinator.close()


@pytest.mark.asyncio
async def test_transport_corruption_and_quotas_do_not_publish_capture(tmp_path):
    provider, coordinator, transport = await started(tmp_path)
    transport.truncate = True
    with pytest.raises(CaptureError, match="incomplete_capture"):
        await provider.capture(control())
    assert not list((tmp_path / "captures").iterdir()) and coordinator.cleanup_ok
    provider, _, transport = await started(tmp_path / "quota")
    with pytest.raises(CaptureError, match="artifact_quota"):
        await provider.capture(control(artifact_limit=1))
    assert provider.last_capture is None


@pytest.mark.asyncio
async def test_multichunk_offsets_hashes_and_limit(tmp_path):
    provider, _, t = await started(tmp_path)
    t.files["sphinx/a.py"] = b"#" * (48 * 1024 * 2 + 1)
    raw = await t.helper(provider.request("seed"), control())
    entries, blobs, _, _ = parse_snapshot(raw, provider.policy, control())
    assert blobs["sphinx/a.py"] == t.files["sphinx/a.py"]
    damaged = raw.replace(b'"offset": 49152', b'"offset": 49151')
    with pytest.raises(CaptureError, match="chunk"):
        parse_snapshot(damaged, provider.policy, control())
    with pytest.raises(CaptureError, match="wire_quota"):
        parse_snapshot(raw, provider.policy, control(wire_limit=10))
    with pytest.raises(CaptureError, match="manifest_limit"):
        parse_snapshot(raw, replace(provider.policy, max_entries=1), control())


def test_store_corruption_ownership_and_disk_quota(tmp_path):
    store = WorkspaceBlobStore(tmp_path)
    ref = store.put(b"abc")
    assert store.read(ref) == b"abc"
    with pytest.raises(CaptureError, match="reference_invalid"):
        store.read(ArtifactRef(ref.sha256, ref.size, "other/" + ref.sha256))
    (tmp_path / ref.key).write_bytes(b"abd")
    with pytest.raises(CaptureError, match="artifact_corrupt"):
        store.read(ref)
    with pytest.raises(CaptureError, match="artifact_quota"):
        WorkspaceBlobStore(tmp_path / "small", limit_bytes=2).put(b"abc")


def test_full_policy_identity_and_mode_frozen():
    p = CapturePolicy()
    assert replace(p, max_delta_bytes=p.max_delta_bytes + 1).rules_id != p.rules_id
    old = FileEntry("sphinx/a.py", "regular", 0o644, 0, blob_sha256(b""))
    assert p.violations([old], [replace(old, mode=0o755)])
    for path in ("sphinx/conftest.py", "sphinx/setup.py", "sphinx/build/a.py", "sphinx/a.egg-info/a.py"):
        assert p.violations([], [replace(old, path=path)])


@pytest.mark.asyncio
async def test_base_mismatch_or_blob_corruption_cannot_materialize(tmp_path):
    provider, _, t = await started(tmp_path / "edit")
    await provider.capture(control())
    t.files["sphinx/a.py"] = b"x = 2\n"
    capture = await provider.capture(control())
    other = FakeDocker()
    other.files["data.bin"] = b"different-image-seed"
    replica, coordinator, _ = await started(tmp_path / "replica", other)
    with pytest.raises(CaptureError, match="base_manifest_mismatch"):
        await materialize(capture, provider.store, replica, control())
    assert coordinator.state == "failed" and coordinator.cleanup_ok
    ref = capture.changed[0][1]
    (provider.store.root / ref.key).write_bytes(b"corrupt")
    fresh, _, _ = await started(tmp_path / "corrupt")
    with pytest.raises(CaptureError, match="artifact_corrupt"):
        await materialize(capture, provider.store, fresh, control())
    assert fresh.last_capture is None


@pytest.mark.asyncio
async def test_task_cancellation_settles_execution_before_owned_cleanup(tmp_path):
    _, coordinator, transport = await started(tmp_path)
    transport.delay = 5
    task = asyncio.create_task(coordinator.execute(["sleep"], control()))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert coordinator.state == "failed" and coordinator.cleanup_ok and transport.active_calls == 0


@pytest.mark.asyncio
async def test_cancel_event_during_command_is_not_cli_completion(tmp_path):
    _, coordinator, transport = await started(tmp_path)
    transport.delay = 5
    event = asyncio.Event()
    task = asyncio.create_task(coordinator.execute(["sleep"], control(cancel=event)))
    await asyncio.sleep(0.01)
    event.set()
    with pytest.raises(CaptureError, match="cancelled"):
        await task
    assert coordinator.state == "failed" and coordinator.cleanup_ok and transport.active_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["execute", "close"])
async def test_cancel_and_successful_lock_acquire_together_release_ownership(tmp_path, entry):
    _, coordinator, transport = await started(tmp_path)
    event = asyncio.Event()

    class CancelOnAcquire(asyncio.Lock):
        async def acquire(self):
            acquired = await super().acquire()
            event.set()  # Both acquisition and the cancel watcher finish this turn.
            return acquired

    coordinator._lock = CancelOnAcquire()
    with pytest.raises(CaptureError, match="cancelled"):
        if entry == "execute":
            await coordinator.execute(["true"], control(cancel=event))
        else:
            await coordinator.close(control(cancel=event))
    assert not coordinator._lock.locked()
    assert coordinator.state == "active" and transport.maximum_calls == 0
    assert not transport.closed  # Rejected before any container operation began.
    assert await asyncio.wait_for(coordinator.close(), 1)
    assert coordinator.state == "closed" and transport.closed and coordinator.cleanup_ok


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["execute", "close"])
@pytest.mark.parametrize("stop", ["cancelled", "deadline"])
async def test_stopped_waiter_never_releases_another_operations_lock(tmp_path, entry, stop):
    _, coordinator, transport = await started(tmp_path)
    event, waiting = asyncio.Event(), asyncio.Event()

    class ObserveWait(asyncio.Lock):
        async def acquire(self):
            waiting.set()
            return await super().acquire()

    coordinator._lock = ObserveWait()
    await coordinator._lock.acquire()  # A separate operation owns the lock.
    waiting.clear()
    stop_control = CaptureControl("waiting", time.monotonic() + (.02 if stop == "deadline" else 10), event)
    command = (coordinator.execute(["true"], stop_control) if entry == "execute"
               else coordinator.close(stop_control))
    task = asyncio.create_task(command)
    await asyncio.wait_for(waiting.wait(), 1)
    if stop == "cancelled":
        event.set()
    with pytest.raises(CaptureError, match=stop):
        await task
    assert coordinator._lock.locked() and not transport.closed
    assert transport.maximum_calls == 0
    coordinator._lock.release()
    assert await coordinator.close() and transport.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["execute", "close"])
async def test_deadline_at_successful_acquisition_releases_before_any_work(tmp_path, entry, monkeypatch):
    import src.workspace.control as control_module
    from types import SimpleNamespace

    _, coordinator, transport = await started(tmp_path)
    clock = SimpleNamespace(now=time.monotonic())
    monkeypatch.setattr(control_module, "time", SimpleNamespace(monotonic=lambda: clock.now))
    expired = CaptureControl("acquire-boundary", clock.now + 1)

    class ExpireOnAcquire(asyncio.Lock):
        async def acquire(self):
            acquired = await super().acquire()
            clock.now += 2
            return acquired

    coordinator._lock = ExpireOnAcquire()
    with pytest.raises(CaptureError, match="deadline"):
        if entry == "execute":
            await coordinator.execute(["true"], expired)
        else:
            await coordinator.close(expired)
    assert not coordinator._lock.locked()
    assert coordinator.state == "active" and transport.maximum_calls == 0
    assert await coordinator.close() and transport.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["execute", "close"])
async def test_async_task_cancellation_at_successful_acquisition_releases(tmp_path, entry):
    _, coordinator, transport = await started(tmp_path)
    task = None

    class CancelOwnerOnAcquire(asyncio.Lock):
        triggered = False

        async def acquire(self):
            acquired = await super().acquire()
            if not self.triggered:
                self.triggered = True
                task.cancel()
            return acquired

    lock = CancelOwnerOnAcquire()
    coordinator._lock = lock
    command = coordinator.execute(["true"], control()) if entry == "execute" else coordinator.close()
    task = asyncio.create_task(command)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not lock.locked() and transport.maximum_calls == 0
    assert await coordinator.close() and transport.closed
