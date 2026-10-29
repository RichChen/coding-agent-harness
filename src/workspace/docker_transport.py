"""Bounded Docker CLI transport for new, uniquely owned workspace containers.

No image pulls, mounts, legacy backend calls or host shell interpolation. Use only
through WorkspaceCoordinator; its lock covers this container's entire lifecycle.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from src.workspace.contracts import canonical_json
from src.workspace.control import CaptureControl, CaptureError

HELPER_SOURCE = Path(__file__).with_name("helper.py").read_text(encoding="utf-8")
SLEEP_CODE = "import time; time.sleep(604800)"
OWNER_LABEL = "coding-harness.workspace-v1"


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: bytes
    stderr: bytes


class DockerWorkspace:
    def __init__(self, *, image: str, context="desktop-linux", python="/usr/local/bin/python",
                 docker_bin="docker", cpus="1", memory_bytes=512 * 1024 * 1024,
                 workspace_bytes=128 * 1024 * 1024, tmp_bytes=32 * 1024 * 1024, pids_limit=64):
        from src.workspace.contracts import ArtifactRef
        digest = image.rsplit("@sha256:", 1)
        if len(digest) != 2 or not digest[0]:
            raise ValueError("local pinned image reference required")
        ArtifactRef(digest[1], 0, "validate-image-digest")
        if not python.startswith("/") or "\0" in python:
            raise ValueError("absolute trusted Python path required")
        if not isinstance(cpus, str) or not Decimal(cpus).is_finite() or Decimal(cpus) <= 0:
            raise ValueError("cpus must be an explicit positive decimal string")
        nano = Decimal(cpus) * 1000000000
        if nano != nano.to_integral_value():
            raise ValueError("CPU limit must have exact nanocpu representation")
        if any(type(n) is not int or n <= 0 for n in (memory_bytes, workspace_bytes, tmp_bytes, pids_limit)):
            raise ValueError("positive integer resource limits required")
        self.image, self.image_digest = image, "sha256:" + digest[1]
        self.context, self.python, self.docker_bin = context, python, docker_bin
        self.owner = uuid4().hex
        self.name = "workspace-v1-" + self.owner
        self.container_id = None
        self.reserved = False
        self.cleanup_ok = None
        self.profile = dict(cpus=cpus, nanocpus=int(nano), memory_bytes=memory_bytes,
                            workspace_bytes=workspace_bytes, tmp_bytes=tmp_bytes, pids_limit=pids_limit)
        self.image_id = None

    async def cli(self, control, *args, data=None, limit=None, timeout=30.0):
        """Management RPCs are capped; timeout=None uses the caller's deadline."""
        maximum = min(limit or control.wire_limit, control.wire_limit)
        if data is not None and len(data) > control.wire_limit:
            raise CaptureError("wire_quota")
        process = None
        spawn = asyncio.create_task(asyncio.create_subprocess_exec(
            self.docker_bin, "--context", self.context, *args,
            stdin=asyncio.subprocess.PIPE if data is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE))
        tasks = []
        total = 0

        async def read(pipe):
            nonlocal total
            output = bytearray()
            while chunk := await pipe.read(65536):
                total += len(chunk)
                if total > maximum:
                    raise CaptureError("wire_quota")
                output.extend(chunk)
            return bytes(output)

        async def write():
            if data is not None:
                for offset in range(0, len(data), 65536):
                    process.stdin.write(data[offset:offset + 65536])
                    await process.stdin.drain()
                process.stdin.close()

        try:
            process = await control.wait(asyncio.shield(spawn), maximum=timeout)
            tasks = [asyncio.create_task(read(process.stdout)), asyncio.create_task(read(process.stderr)),
                     asyncio.create_task(write()), asyncio.create_task(process.wait())]
            out, err, _, code = await control.wait(asyncio.gather(*tasks), maximum=timeout)
            return CommandResult(code, out, err)
        except (OSError, BrokenPipeError) as exc:
            raise CaptureError("docker_unavailable") from exc
        finally:
            if process is None:
                # Cancellation must not lose a newly created CLI process handle.
                def reap(future):
                    if not future.cancelled() and future.exception() is None:
                        future.result().kill()
                if spawn.done():
                    if not spawn.cancelled() and spawn.exception() is None:
                        process = spawn.result()
                else:
                    spawn.add_done_callback(reap)
            if process is not None and process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if process is not None:
                await asyncio.wait_for(process.wait(), 2)

    async def ok(self, control, *args, **kwargs):
        result = await self.cli(control, *args, **kwargs)
        if result.exit_code:
            raise CaptureError("docker_control_failed", evidence={"operation": args[0], "exit_code": result.exit_code})
        return result.stdout

    async def start(self, control):
        info = json.loads(await self.ok(control, "info", "--format", "{{json .}}", limit=256 * 1024))
        if info.get("OSType") != "linux" or not all(info.get(k) for k in ("MemoryLimit", "PidsLimit", "CpuCfsQuota")):
            raise CaptureError("unsupported_daemon")
        self.image_id = (await self.ok(control, "image", "inspect", self.image,
                                      "--format", "{{.Id}}", limit=4096)).decode().strip()
        p = self.profile
        self.reserved = True  # Record the unique owned name before create.
        await self.ok(control, "create", "--pull=never", "--name", self.name,
            "--label", OWNER_LABEL + "=" + self.owner, "--read-only", "--network", "none",
            "--cpus", p["cpus"], "--memory", str(p["memory_bytes"]), "--memory-swap", str(p["memory_bytes"]),
            "--pids-limit", str(p["pids_limit"]), "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--user", "1000:1000", "--log-driver", "none", "--workdir", "/workspace",
            "--tmpfs", f'/workspace:rw,nosuid,nodev,size={p["workspace_bytes"]},mode=1777',
            "--tmpfs", f'/tmp:rw,noexec,nosuid,nodev,size={p["tmp_bytes"]},mode=1777',
            "--entrypoint", self.python, self.image, "-I", "-S", "-c", SLEEP_CODE, limit=4096)
        await self.ok(control, "start", self.name, limit=4096)
        item = json.loads(await self.ok(control, "inspect", self.name, limit=256 * 1024))[0]
        host = item["HostConfig"]
        self.container_id = item["Id"]
        if (item["Config"].get("Labels", {}).get(OWNER_LABEL) != self.owner
                or item["Image"] != self.image_id or not host["ReadonlyRootfs"] or host["Privileged"]
                or host.get("Binds") or host["NetworkMode"] != "none" or host["Memory"] != p["memory_bytes"]
                or host["MemorySwap"] != p["memory_bytes"] or host["NanoCpus"] != p["nanocpus"]
                or host["PidsLimit"] != p["pids_limit"] or item["Config"]["User"] != "1000:1000"
                or "ALL" not in host.get("CapDrop", [])
                or "no-new-privileges:true" not in host.get("SecurityOpt", [])
                or host["LogConfig"]["Type"] != "none"
                or set(host.get("Tmpfs", {})) != {"/workspace", "/tmp"}
                or any(m["Type"] != "tmpfs" for m in item.get("Mounts", []))):
            raise CaptureError("container_profile_mismatch")

    async def helper(self, request, control):
        request = dict(request, python=self.python)
        result = await self.cli(control, "exec", "-i", self.container_id, self.python, "-I", "-S", "-c",
                                HELPER_SOURCE, data=canonical_json(request))
        # The helper's stable error record is parsed by CaptureProvider even on exit 2.
        if result.exit_code not in (0, 2) or result.stderr:
            raise CaptureError("helper_failed")
        return result.stdout

    async def execute(self, argv, control, *, stdin=None, env=None):
        variables = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": "/tmp", "TMPDIR": "/tmp"}
        variables.update(env or {})
        if (not argv or any(not isinstance(a, str) or "\0" in a for a in argv)
                or any(not isinstance(k, str) or not k or not k.isascii()
                       or not k.replace("_", "a").isalnum() or k[0].isdigit() or not isinstance(v, str)
                       or "\0" in v for k, v in variables.items())):
            raise CaptureError("invalid_command")
        return await self.cli(control, "exec", *(["-i"] if stdin is not None else []), self.container_id,
                              "/usr/bin/env", "-i", *[f"{k}={v}" for k, v in variables.items()], *argv,
                              data=stdin, timeout=None)

    async def close(self, control):
        if not self.reserved:
            self.cleanup_ok = True
            return True
        found = await self.ok(control, "ps", "-aq", "--no-trunc", "--filter", "name=^/" + self.name + "$", limit=4096)
        if found.strip():
            item = json.loads(await self.ok(control, "inspect", self.name, limit=256 * 1024))[0]
            if (item["Config"].get("Labels", {}).get(OWNER_LABEL) != self.owner
                    or self.container_id is not None and item["Id"] != self.container_id):
                raise CaptureError("cleanup_ownership_mismatch")
            await self.ok(control, "rm", "-f", item["Id"], limit=4096)
        left = await self.ok(control, "ps", "-aq", "--filter", "name=^/" + self.name + "$", limit=4096)
        self.cleanup_ok = not left.strip()
        if not self.cleanup_ok:
            raise CaptureError("cleanup_failed")
        return True
