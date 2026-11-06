"""Per-run Linux containers. No host mounts, no implicit pulls, no host fallback.

Workspace/tmp are size-limited tmpfs. Inputs and candidate snapshots cross the
boundary as bounded data, never as host-extracted tar files or executable hooks.
"""
from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import re
import time
import uuid
from pathlib import Path, PurePosixPath

from src.execution.base import (CleanupState, ExecutionRequest, ExecutionResult,
    ExecutionStatus, OutputQuotas, RunOutputMeter, StreamCapture, ToolArtifactBudget)
from src.execution.collector import collect_stream

DEFAULT_IMAGE = "python@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9"
TRANSFER_LIMIT = 8 * 1024 * 1024
MAX_FILES = 2048


class DockerUnavailable(RuntimeError):
    pass


RETRY_WAITS = (2, 5, 10)
_sleep = asyncio.sleep


def is_transient(exc) -> bool:
    """A Docker CLI/daemon failure worth retrying. A harness script that fails inside the container is not."""
    if isinstance(exc, (ConnectionResetError, TimeoutError)):
        return True
    if not isinstance(exc, DockerUnavailable):
        return False
    text = str(exc)
    return not any(marker in text for marker in ("Traceback (most recent call last)", "delta_too_large", "output quota exceeded"))


async def _reap_cli(proc, tasks):
    """Kill, drain, then wait: wait alone can deadlock on paused asyncio pipes."""
    for task in tasks:
        if not task.done(): task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    if proc is None: return
    if proc.returncode is None:
        try: proc.kill()
        except ProcessLookupError: pass
    async def discard(pipe):
        while await pipe.read(8192): pass
    await asyncio.wait_for(asyncio.gather(discard(proc.stdout), discard(proc.stderr), proc.wait()), 3)


def image_is_pinned(image):
    return isinstance(image, str) and re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", image) is not None


def validate_files(files):
    if not isinstance(files, dict) or len(files) > MAX_FILES:
        raise ValueError("invalid snapshot/file count")
    total = 0
    for name, content in files.items():
        path = PurePosixPath(name)
        if (not name or not path.parts or str(path) != name or path.is_absolute() or ".." in path.parts
                or "\\" in name or ":" in name or "\x00" in name or not isinstance(content, bytes)):
            raise ValueError("unsafe snapshot path or content")
        total += len(content)
    if total > TRANSFER_LIMIT:
        raise ValueError("snapshot transfer quota exceeded")
    return files


def read_workspace(root):
    """Explicit public seed directory. Never point this at the harness/credential root."""
    root = Path(root).resolve(strict=True)
    files = {}
    if (root / 'src' / 'harness' / 'factory.py').exists():
        raise ValueError("use a dedicated public seed directory, not the harness checkout")
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if any(p.startswith('.env') or p in (".git", ".ssh", ".aws", ".azure", ".config", ".oracle", ".grader_private", ".harness_artifacts", "__pycache__") for p in rel.parts):
            continue
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("workspace seed contains symlink/junction")
        if path.is_dir():
            continue
        if not path.is_file() or path.stat().st_size > TRANSFER_LIMIT:
            raise ValueError("workspace seed contains special/oversized file")
        files[rel.as_posix()] = path.read_bytes()
        validate_files(files)
    return files


IMPORT_CODE = r'''
import base64,json,pathlib,sys
for name,data in json.load(sys.stdin).items():
    p=pathlib.Path('/workspace')/name
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_bytes(base64.b64decode(data))
'''
EXPORT_CODE = r'''
import base64,json,os,pathlib,stat,sys
root=pathlib.Path('/workspace'); files={}; total=0
for parent,dirs,names in os.walk(root,followlinks=False):
    for name in dirs+names:
        p=pathlib.Path(parent)/name
        mode=p.lstat().st_mode
        if stat.S_ISLNK(mode): raise ValueError('snapshot contains symlink')
        if stat.S_ISDIR(mode): continue
        if not stat.S_ISREG(mode): raise ValueError('snapshot contains special file')
        with p.open('rb') as f: data=f.read(8388609)
        total+=len(data)
        if total>8388608 or len(files)>=2048: raise ValueError('snapshot quota exceeded')
        files[p.relative_to(root).as_posix()]=base64.b64encode(data).decode('ascii')
print(json.dumps(files))
'''


# Kills every process whose environment carries HARNESS_EXEC_ID=<argv[1]>, including setsid
# descendants. Repeats until none is left, since a dying parent may still fork; exit 3 if it never settles.
REAP_CODE = r'''import os,signal,sys,time
mark=b"HARNESS_EXEC_ID="+sys.argv[1].encode()
me=os.getpid(); killed=set()
for _ in range(50):
    found=[]
    for p in os.listdir("/proc"):
        if not p.isdigit() or int(p)==me: continue
        try: env=open("/proc/"+p+"/environ","rb").read().split(b"\0")
        except OSError: continue
        if mark in env: found.append(int(p))
    if not found: break
    for pid in found:
        try: os.kill(pid,signal.SIGKILL); killed.add(pid)
        except ProcessLookupError: pass
    time.sleep(.05)
else: sys.exit(3)
print(len(killed))
'''


class DockerExecutionBackend:
    profile = "docker_linux"
    platform = "linux-container"

    def __init__(self, *, image=DEFAULT_IMAGE, context="desktop-linux", cpus=1.0,
                 memory_bytes=512*1024*1024, pids_limit=128, network="none",
                 tmpfs_bytes=16*1024*1024, workspace_bytes=64*1024*1024, docker_bin="docker",
                 python_bin="/usr/local/bin/python", reap_on_timeout=False):
        if not image_is_pinned(image):
            raise ValueError("Docker image must be pinned by sha256 digest")
        if type(reap_on_timeout) is not bool:
            raise ValueError("reap_on_timeout must be a bool")
        if isinstance(cpus, bool) or not isinstance(cpus, (int, float)) or not math.isfinite(cpus) or cpus <= 0:
            raise ValueError("invalid CPU limit")
        if any(type(n) is not int or n <= 0 for n in (memory_bytes, pids_limit, tmpfs_bytes, workspace_bytes)):
            raise ValueError("resource limits must be positive integers")
        if network not in ("none", "bridge"):
            raise ValueError("network must be none or bridge; no domain allowlist is implied")
        self.image, self.context, self.docker_bin = image, context, docker_bin
        self.python_bin = python_bin
        self.cpus, self.memory_bytes, self.pids_limit = cpus, memory_bytes, pids_limit
        self.network, self.tmpfs_bytes, self.workspace_bytes = network, tmpfs_bytes, workspace_bytes
        # A timed-out or cancelled command keeps the container up; when set, its processes are killed in place.
        self.reap_on_timeout = reap_on_timeout
        self.name = "h3-" + uuid.uuid4().hex
        self.active = False
        self.closed = False
        self._owned = False
        self._busy = False
        self.snapshot = None
        self.snapshot_error = None
        self.lifecycle = []
        self.last_inspect = None
        self.cleanup_ok = None
        self.cleanup_elapsed_s = 0.0
        self._meter = RunOutputMeter()
        self.seed_files = None
        self.docker_retries = []

    @property
    def capabilities(self):
        caps = dict(profile=self.profile, platform=self.platform, image=self.image,
            context=self.context, cancel=True, filesystem_isolation=True, network_control=True,
            network_default=self.network, resource_limits=True, complete_isolation=False,
            cpu_limit=self.cpus, memory_limit_bytes=self.memory_bytes, pids_limit=self.pids_limit,
            workspace_limit_bytes=self.workspace_bytes, tmpfs_limit_bytes=self.tmpfs_bytes,
            tree_reclaim_guaranteed=True, privileged=False, docker_socket_mounted=False,
            host_mounts=False, workspace_transfer_limit=TRANSFER_LIMIT)
        if self.reap_on_timeout:
            caps["exec_reap_on_timeout"] = True
        return caps

    async def _control(self, *args, data=None, limit=16*1024*1024, timeout=10):
        """Bound even control output. Host credentials are used ONLY by the trusted Docker CLI."""
        proc = None
        tasks = []
        spawn = asyncio.create_task(asyncio.create_subprocess_exec(
            self.docker_bin, "--context", self.context, *args,
            stdin=asyncio.subprocess.PIPE if data is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE))
        async def read(pipe):
            output = bytearray()
            while chunk := await pipe.read(8192):
                if len(output) + len(chunk) > limit:
                    raise DockerUnavailable("Docker control output quota exceeded")
                output.extend(chunk)
            return bytes(output)
        async def write():
            if data is not None:
                proc.stdin.write(data)
                await proc.stdin.drain()
                proc.stdin.close()
        try:
            proc = await asyncio.shield(spawn)
            tasks = [asyncio.create_task(read(proc.stdout)), asyncio.create_task(read(proc.stderr)),
                     asyncio.create_task(write()), asyncio.create_task(proc.wait())]
            async with asyncio.timeout(timeout):
                out, err, _, _ = await asyncio.gather(*tasks)
            return proc.returncode, out, err
        except (OSError, TimeoutError) as exc:
            raise DockerUnavailable(f"Docker control unavailable: {type(exc).__name__}") from exc
        finally:
            if proc is None:
                # Shield process creation so cancellation cannot discard a newly acquired handle.
                try:
                    proc = await asyncio.wait_for(asyncio.shield(spawn), timeout=2)
                except (Exception, asyncio.CancelledError):
                    spawn.add_done_callback(lambda t: t.result().kill() if not t.cancelled() and t.exception() is None else None)
            await _reap_cli(proc, tasks)

    async def _ok(self, *args, op=None, **kwargs):
        async def once():
            code, out, err = await self._control(*args, **kwargs)
            if code:
                raise DockerUnavailable(f"Docker {args[0]} failed: {err.decode('utf-8','replace')[-500:]}")
            return out
        return await self._retrying(op or args[0], once, recreate=args[0] == "create")

    async def _retrying(self, op, attempt, *, recreate=False):
        """Retry transient Docker control failures after RETRY_WAITS seconds. Every retry is recorded."""
        last = None
        for index in range(len(RETRY_WAITS) + 1):
            if index:
                await _sleep(RETRY_WAITS[index - 1])
                if recreate:
                    try:
                        await self._control("rm", "-f", self.name, timeout=10)
                    except (DockerUnavailable, OSError, TimeoutError):
                        pass
            try:
                return await attempt()
            except (DockerUnavailable, ConnectionResetError, TimeoutError) as exc:
                if not is_transient(exc):
                    raise
                last = exc
                if index < len(RETRY_WAITS):
                    self.docker_retries.append(dict(
                        op=op, retry=index + 1, wait_s=RETRY_WAITS[index],
                        error=(type(exc).__name__ + ": " + str(exc))[:300],
                        at=time.strftime("%Y-%m-%dT%H:%M:%S%z")))
        raise DockerUnavailable(
            f"Docker {op} failed after {len(RETRY_WAITS)} retries: {type(last).__name__}: {str(last)[:300]}") from last

    def create_args(self):
        return ["create", "--pull=never", "--name", self.name, "--label", "coding-harness.h3=true",
            "--read-only", "--network", self.network, "--cpus", str(self.cpus),
            "--memory", str(self.memory_bytes), "--memory-swap", str(self.memory_bytes),
            "--pids-limit", str(self.pids_limit), "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true", "--user", "1000:1000",
            "--log-driver", "none", "--workdir", "/workspace",
            "--tmpfs", f"/workspace:rw,nosuid,nodev,size={self.workspace_bytes},mode=1777",
            "--tmpfs", f"/tmp:rw,noexec,nosuid,nodev,size={self.tmpfs_bytes},mode=1777",
            "--entrypoint", self.python_bin, self.image, "-I", "-S", "-c",
            "import time; time.sleep(604800)"]

    async def start(self, files=None):
        if self.active or self.closed or self._owned:
            raise DockerUnavailable("Docker session cannot be restarted/reused")
        files = validate_files(files or {})
        try:
            info = json.loads(await self._ok("info", "--format", "{{json .}}", limit=256*1024))
            if info.get("OSType") != "linux" or not all(info.get(k) for k in ("MemoryLimit", "PidsLimit", "CpuCfsQuota")):
                raise DockerUnavailable("Linux daemon lacks required resource limits")
            # Fail before create if the digest is absent; users provision images explicitly.
            await self._ok("image", "inspect", self.image, "--format", "{{.Id}}", limit=4096)
            self._owned = True  # name reserved BEFORE create; cancellation must still remove it.
            await self._ok(*self.create_args())
            await self._ok("start", self.name)
            inspect = json.loads(await self._ok("inspect", self.name))[0]
            self.last_inspect = inspect
            host = inspect["HostConfig"]
            if (not host["ReadonlyRootfs"] or host["Privileged"] or host.get("Binds")
                    or host["Memory"] != self.memory_bytes or host["PidsLimit"] != self.pids_limit
                    or host["MemorySwap"] != self.memory_bytes or host["LogConfig"]["Type"] != "none"
                    or 'ALL' not in host.get('CapDrop', [])
                    or 'no-new-privileges:true' not in host.get('SecurityOpt', [])
                    or host["NetworkMode"] != self.network or inspect["Config"]["User"] != "1000:1000"
                    or host["NanoCpus"] != round(self.cpus * 1e9)
                    or any(m["Type"] != "tmpfs" for m in inspect.get("Mounts", []))):
                raise DockerUnavailable("daemon did not enforce requested container profile")
            self.active = True
            self.lifecycle.append(dict(event="created", container=self.name, image_id=inspect["Image"]))
            payload = json.dumps({n: base64.b64encode(d).decode() for n, d in files.items()}).encode()
            await self._ok("exec", "-i", self.name, self.python_bin, "-I", "-S", "-c", IMPORT_CODE, data=payload)
        except BaseException:
            await self.close()
            raise

    async def close(self, *, snapshot=False):
        if self.closed and self.cleanup_ok is True:
            return True
        started = time.monotonic()
        if snapshot and self.active:
            try:
                data = await self._ok("exec", self.name, self.python_bin, "-I", "-S", "-c", EXPORT_CODE, op="snapshot")
                self.snapshot = validate_files({n: base64.b64decode(d, validate=True) for n, d in json.loads(data).items()})
            except (Exception, asyncio.CancelledError) as exc:
                self.snapshot_error = type(exc).__name__
        try:
            if self._owned:
                # rm acknowledgement plus daemon listing is the cleanup proof, not CLI exit alone.
                await self._retrying("rm", lambda: self._control("rm", "-f", self.name, timeout=5))
                left = await self._ok("ps", "-aq", "--filter", f"name=^/{self.name}$", timeout=3, limit=4096)
                self.cleanup_ok = not left.strip()
            else:
                self.cleanup_ok = True
        except (Exception, asyncio.CancelledError) as exc:
            self.cleanup_ok = False
            self.lifecycle.append(dict(event='cleanup_error', error=repr(exc)))
        self.active = False
        self.closed = True
        self.cleanup_elapsed_s = time.monotonic() - started
        self.lifecycle.append(dict(event="destroyed" if self.cleanup_ok else "cleanup_failed", container=self.name,
                                   elapsed_s=self.cleanup_elapsed_s))
        return self.cleanup_ok

    async def _reap_exec(self, exec_id, kind):
        """Kill the stopped command's processes inside the container; destroy the container if that fails."""
        try:
            out = await self._ok("exec", self.name, self.python_bin, "-I", "-S", "-c", REAP_CODE, exec_id,
                                 limit=4096, op="exec reap")
            self.lifecycle.append(dict(event="exec_reaped", kind=kind, killed=int(out.strip() or 0)))
        except (Exception, asyncio.CancelledError) as exc:
            self.lifecycle.append(dict(event="exec_reap_failed", kind=kind, error=repr(exc)[:300]))
            await self.close()

    async def _settle_exec(self, proc, tasks, kind, exec_id=None):
        if tasks:
            await asyncio.wait(tasks, timeout=.2)
        cli_cleanup_error = None
        prior_kind = kind
        try:
            await _reap_cli(proc, tasks)
        except (Exception, asyncio.CancelledError) as exc:
            if prior_kind not in ("tool_timeout", "user_cancelled"):
                kind = 'cleanup_failed'
            cli_cleanup_error = exc
            self.lifecycle.append(dict(event='cli_cleanup_error', error=repr(exc)))
        for t in tasks:
            if not t.done(): t.cancel()
        if exec_id and kind in ("tool_timeout", "user_cancelled") and self.active and not self.closed:
            await self._reap_exec(exec_id, kind)
        if kind and kind not in ("tool_timeout", "user_cancelled"):
            # A command timeout or a parent deadline must leave the container
            # up: the model needs the timeout result, and the loop exports
            # the workspace before it destroys the session.
            await self.close()
        if cli_cleanup_error is not None and kind not in ("tool_timeout", "user_cancelled"):
            self.cleanup_ok = False
        captured = [None, None]
        for idx, t in enumerate(tasks[:2]):
            if t.done() and not t.cancelled() and t.exception() is None:
                captured[idx] = t.result()
        return kind, captured

    async def execute(self, request: ExecutionRequest):
        start = time.monotonic()
        def result(status, kind=None, **kw):
            return ExecutionResult(status=status, error_kind=kind, platform=self.platform, profile=self.profile,
                                   duration_ms=(time.monotonic()-start)*1000, **kw)
        if not self.active or self.closed:
            return result(ExecutionStatus.BLOCKED, "unavailable", error_message="Docker run session is not active")
        if self._busy:
            return result(ExecutionStatus.BLOCKED, "concurrent_operation")
        if bool(request.argv) == bool(request.shell_command):
            return result(ExecutionStatus.BLOCKED, "invalid_request")
        if request.cancel_event and request.cancel_event.is_set():
            return result(ExecutionStatus.CANCELLED, "user_cancelled")
        if request.deadline_mono is not None and time.monotonic() >= request.deadline_mono:
            return result(ExecutionStatus.TIMED_OUT, "tool_timeout")
        meter = request.run_meter or self._meter
        if meter.producers_stopped:
            return result(ExecutionStatus.FAILED, "run_output_limit")
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": "/tmp", "TMPDIR": "/tmp"}
        env.update(request.env or {})
        if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k) or not isinstance(v, str) or "\x00" in v for k,v in env.items()):
            return result(ExecutionStatus.BLOCKED, "invalid_environment")
        exec_id = uuid.uuid4().hex if self.reap_on_timeout else None
        if exec_id:
            env["HARNESS_EXEC_ID"] = exec_id
        argv = request.argv or ["/bin/sh", "-c", request.shell_command]
        # env -i strips image ENV, docker exec receives only explicitly allowed values.
        command = [self.docker_bin, "--context", self.context, "exec", *(["-i"] if request.stdin_bytes is not None else []), self.name,
                   "/usr/bin/env", "-i", *[f"{k}={v}" for k,v in env.items()], *argv]
        proc = None
        tasks = []
        self._busy = True
        kind = None
        out = StreamCapture()
        err = StreamCapture()
        exit_code = None
        try:
            spawn = asyncio.create_task(asyncio.create_subprocess_exec(*command,
                stdin=asyncio.subprocess.PIPE if request.stdin_bytes is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE))
            try:
                proc = await asyncio.shield(spawn)
            except asyncio.CancelledError:
                try:
                    proc = await asyncio.wait_for(asyncio.shield(spawn), 2)
                except TimeoutError:
                    def reap(t):
                        if not t.cancelled() and t.exception() is None:
                            late = t.result()
                            if late.returncode is None: late.kill()
                            asyncio.create_task(late.wait())
                    spawn.add_done_callback(reap)
                raise
            budget = request.artifact_budget or ToolArtifactBudget(request.quotas.artifact_per_tool_bytes)
            tasks = [asyncio.create_task(collect_stream(pipe, quotas=request.quotas, run_meter=meter,
                        artifact_budget=budget, artifact_path=request.artifact_dir / name if request.artifact_dir else None))
                     for pipe,name in ((proc.stdout,"stdout.bin"),(proc.stderr,"stderr.bin"))]
            waiter = asyncio.create_task(proc.wait())
            tasks.append(waiter)
            async def feed():
                if request.stdin_bytes is not None:
                    try:
                        proc.stdin.write(request.stdin_bytes)
                        await proc.stdin.drain()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    finally:
                        proc.stdin.close()
            tasks.append(asyncio.create_task(feed()))
            while not waiter.done() or not all(t.done() for t in tasks[:2]):
                if request.cancel_event and request.cancel_event.is_set():
                    kind = "user_cancelled"; break
                if request.deadline_mono is not None and time.monotonic() >= request.deadline_mono:
                    kind = "tool_timeout"; break
                if meter.producers_stopped:
                    kind = "run_output_limit"; break
                if any(t.done() and not t.cancelled() and t.exception() for t in tasks):
                    kind = "collector_error"; break
                await asyncio.wait({t for t in tasks if not t.done()}, timeout=.025)
            exit_code = proc.returncode
        except asyncio.CancelledError:
            kind = "user_cancelled"
        except (Exception,):
            kind = "docker_exec_failed"
        finally:
            # The outer tool deadline fires at the same instant as ours, so its
            # cancel can land here. Cleanup still runs to the end, and the
            # timeout result is returned rather than lost.
            settle = asyncio.ensure_future(self._settle_exec(proc, tasks, kind, exec_id))
            try:
                while True:
                    try:
                        kind, captured = await asyncio.shield(settle)
                        break
                    except asyncio.CancelledError:
                        if settle.done(): raise
                out, err = captured[0] or out, captured[1] or err
            finally:
                self._busy = False
        if kind and not self.cleanup_ok and kind not in ("tool_timeout", "user_cancelled"):
            status, kind = ExecutionStatus.OUTCOME_UNKNOWN, "cleanup_failed"
        elif kind == "user_cancelled": status = ExecutionStatus.CANCELLED
        elif kind == "tool_timeout": status = ExecutionStatus.TIMED_OUT
        elif kind: status = ExecutionStatus.FAILED
        elif exit_code != 0:
            status, kind = ExecutionStatus.FAILED, "nonzero_exit"
        else: status = ExecutionStatus.SUCCEEDED
        if exit_code == 137 and not self.closed:
            # SIGKILL/OOM is ambiguous and must stop the session. Shell/env exit
            # 126/127 instead means a command cannot run or is missing: expose
            # it as a normal tool failure so the agent can recover. Those codes
            # alone provide no evidence of resource damage or a dead container.
            await self.close()
            kind = "resource_or_exec_failure" if self.cleanup_ok else "cleanup_failed"
            status = ExecutionStatus.FAILED if self.cleanup_ok else ExecutionStatus.OUTCOME_UNKNOWN
        return result(status, kind, exit_code=exit_code, stdout=out, stderr=err,
            truncated=out.truncated or err.truncated, bytes_seen=out.bytes_seen+err.bytes_seen,
            bytes_kept=out.bytes_kept_artifact+err.bytes_kept_artifact,
            bytes_kept_memory=out.bytes_kept_memory+err.bytes_kept_memory,
            legacy_trailer_bytes_kept=out.bytes_kept_memory+err.bytes_kept_memory+out.bytes_kept_artifact+err.bytes_kept_artifact,
            cleanup_state=(CleanupState.CLEANED if self.cleanup_ok else CleanupState.CLEANUP_FAILED) if self.closed else CleanupState.NOT_REQUIRED,
            cleanup_elapsed_s=self.cleanup_elapsed_s,
            error_message=kind if status != ExecutionStatus.SUCCEEDED else None)
