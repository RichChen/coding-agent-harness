"""Opt-in local mechanism checks; pinned cached image only, no solver/test runner.

PowerShell: $env:WORKSPACE_DOCKER_TESTS='1'; .venv/Scripts/python.exe -m pytest
tests/test_workspace_capture_docker.py -q. Every container has a unique owner token.
"""
import os
import json
import asyncio
import time
from contextlib import asynccontextmanager

import pytest

from src.workspace.blobs import WorkspaceBlobStore
from src.workspace.capture import CaptureProvider
from src.workspace.control import CaptureControl, CaptureError
from src.workspace.coordinator import WorkspaceCoordinator
from src.workspace.docker_transport import DockerWorkspace
from src.workspace.materialize import materialize
from src.workspace.policy import CapturePolicy

pytestmark = pytest.mark.skipif(os.environ.get("WORKSPACE_DOCKER_TESTS") != "1", reason="opt-in cached Docker image check")
IMAGE = "coding-harness-a5-clean@sha256:1a6e20fb4b37194f8596111bc3658261cc40dbd85dc09a0e5be6d6690220b4e2"
REVISION = "82ef497a8c88f0f6e50d84520e7276bfbf65025d"


def control(operation="docker-mechanism", **kwargs):
    return CaptureControl(operation, time.monotonic() + 120, **kwargs)


@asynccontextmanager
async def workspace(root, policy=None):
    docker = DockerWorkspace(image=IMAGE)
    coordinator = WorkspaceCoordinator(docker)
    provider = CaptureProvider(coordinator, WorkspaceBlobStore(root), base_revision=REVISION, policy=policy)
    try:
        await coordinator.start(control("start"))
        yield provider, coordinator, docker
    finally:
        assert await coordinator.close(), "owned container cleanup must be confirmed"


async def python(coordinator, code):
    result = await coordinator.execute(["/usr/local/bin/python", "-I", "-S", "-c", code], control("edit"))
    assert result.exit_code == 0, result.stderr
    return result


@pytest.mark.asyncio
async def test_real_initial_edit_fresh_replica_and_frozen_change(tmp_path):
    async with workspace(tmp_path / "edit") as (provider, coordinator, docker):
        base = await provider.capture(control("initial"))
        assert base.admissible and base.changed == base.deleted == ()
        assert len(base.entries) > 100
        await python(coordinator, "with open('/workspace/sphinx/util/__init__.py','ab') as f: f.write(b'\\n# workspace mechanism probe\\n')")
        capture = await provider.capture(control("candidate"))
        assert capture.admissible and capture.candidate_id != base.candidate_id
        assert [p for p, _ in capture.changed] == ["sphinx/util/__init__.py"]
        capture.validate_against_base(base.entries)
        capture.validate_blobs(provider.store)
        print(json.dumps({"base_manifest_id": base.manifest_id, "entries": len(base.entries),
                          "tree_bytes": sum(e.size for e in base.entries), "corpus_files": len(base.corpus_blobs),
                          "corpus_bytes": sum(ref.size for _, ref in base.corpus_blobs),
                          "candidate_id": capture.candidate_id, "manifest_id": capture.manifest_id}))
        async with workspace(tmp_path / "replica") as (replica, _, replica_docker):
            entries = await materialize(capture, provider.store, replica, control("materialize"))
            assert entries == capture.entries
            assert docker.container_id != replica_docker.container_id
        await python(coordinator, "with open('/workspace/tests/test_util.py','ab') as f: f.write(b'\\n# frozen probe\\n')")
        rejected = await provider.capture(control("frozen"))
        assert not rejected.admissible
        assert any(v["path"] == "tests/test_util.py" for v in rejected.violations)
        await python(coordinator, "import os; os.chmod('/workspace/sphinx/util/__init__.py',0o755)")
        mode_rejected = await provider.capture(control("mode-change"))
        assert not mode_rejected.admissible and mode_rejected.candidate_id != rejected.candidate_id


@pytest.mark.asyncio
@pytest.mark.parametrize("child", [
    "import time; time.sleep(60)",
    "import time; p='/workspace/sphinx/_writer.py'; exec('while True:\\n open(p,\"w\").write(\"# changing\")\\n time.sleep(.02)')",
    "import subprocess,time; subprocess.Popen(['/usr/local/bin/python','-c','import time; time.sleep(60)']); time.sleep(60)",
])
async def test_real_cli_ends_but_background_or_descendant_rejects_and_cleans(tmp_path, child):
    async with workspace(tmp_path) as (provider, coordinator, _):
        base = await provider.capture(control("initial"))
        result = await python(coordinator,
            "import subprocess,time; p=subprocess.Popen(['/usr/local/bin/python','-I','-S','-c'," + repr(child) +
            "],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True);"
            "time.sleep(.1); assert p.poll() is None")
        if "_writer.py" in child:
            await python(coordinator, "from pathlib import Path; assert Path('/workspace/sphinx/_writer.py').read_bytes()==b'# changing'")
        assert result.exit_code == 0  # CLI completion is not workspace quiescence.
        with pytest.raises(CaptureError, match="workspace_not_quiescent") as error:
            await provider.capture(control("background-rejection"))
        assert provider.last_capture == base
        assert error.value.evidence.get("processes")
        if "subprocess,time" in child:
            assert len(error.value.evidence["processes"]) >= 4
        assert coordinator.state == "failed" and coordinator.cleanup_ok


@pytest.mark.asyncio
@pytest.mark.parametrize("code,reason", [
    ("import os; os.symlink('/etc/passwd','/workspace/sphinx/link.py')", "unsupported_file_type"),
    ("import os; os.mkfifo('/workspace/sphinx/fifo')", "unsupported_file_type"),
    ("open('/workspace/.env-secret','wb').write(b'DO_NOT_PROPAGATE')", "private_path"),
    ("open('/workspace/sphinx/back\\\\slash.py','wb').close()", "invalid_path"),
    ("open('/workspace/sphinx/COLLISION.py','wb').close();open('/workspace/sphinx/collision.py','wb').close()", "path_collision"),
    ("import os; os.chmod('/workspace/sphinx/util/__init__.py',0)", "capture_io_error"),
])
async def test_real_unsafe_trees_never_publish_capture_or_secret(tmp_path, code, reason):
    async with workspace(tmp_path) as (provider, coordinator, _):
        base = await provider.capture(control("initial"))
        files_before = set((tmp_path / "blobs").iterdir())
        await python(coordinator, code)
        with pytest.raises(CaptureError, match=reason):
            await provider.capture(control("unsafe"))
        assert provider.last_capture == base and coordinator.cleanup_ok
        assert set((tmp_path / "blobs").iterdir()) == files_before
        assert len(list((tmp_path / "captures").iterdir())) == 1


@pytest.mark.asyncio
async def test_real_four_mib_multichunk_boundary_and_one_byte_over(tmp_path):
    async with workspace(tmp_path) as (provider, coordinator, _):
        await provider.capture(control("initial"))
        await python(coordinator, "open('/workspace/sphinx/_boundary.py','wb').write(b'#'*(4*1024*1024))")
        boundary = await provider.capture(control("four-mib"))
        ref = dict(boundary.changed)["sphinx/_boundary.py"]
        assert ref.size == 4 * 1024 * 1024 and boundary.admissible
        assert len(provider.store.read(ref)) == ref.size
        # Base64 alone exceeds the old 5 MiB exporter output quota.
        assert (ref.size + 2) // 3 * 4 > 5 * 1024 * 1024
        await python(coordinator, "with open('/workspace/sphinx/_boundary.py','ab') as f: f.write(b'x')")
        with pytest.raises(CaptureError, match="delta_byte_limit"):
            await provider.capture(control("over-limit"))
        assert provider.last_capture == boundary and coordinator.cleanup_ok


@pytest.mark.asyncio
async def test_real_delta_file_count_boundary(tmp_path):
    async with workspace(tmp_path) as (provider, coordinator, _):
        await provider.capture(control("initial"))
        await python(coordinator, "for n in range(200): open('/workspace/sphinx/_boundary%03d.py'%n,'wb').close()")
        boundary = await provider.capture(control("two-hundred"))
        assert len(boundary.changed) == 200 and boundary.admissible
        await python(coordinator, "open('/workspace/sphinx/_boundary200.py','wb').close()")
        with pytest.raises(CaptureError, match="delta_file_limit"):
            await provider.capture(control("two-hundred-one"))
        assert provider.last_capture == boundary and coordinator.cleanup_ok


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["deadline", "cancelled"])
async def test_real_inflight_command_stop_destroys_owned_session(tmp_path, stop):
    async with workspace(tmp_path) as (provider, coordinator, _):
        base = await provider.capture(control("initial"))
        event = asyncio.Event()
        limit = time.monotonic() + (0.2 if stop == "deadline" else 30)
        operation = CaptureControl("stopped-command", limit, event)
        task = asyncio.create_task(coordinator.execute(
            ["/usr/local/bin/python", "-I", "-S", "-c", "import time; time.sleep(60)"], operation))
        if stop == "cancelled":
            await asyncio.sleep(.2)
            event.set()
        with pytest.raises(CaptureError, match=stop):
            await task
        assert coordinator.state == "failed" and coordinator.cleanup_ok
        assert provider.last_capture == base
