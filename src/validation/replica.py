"""Injected replica boundary. LocalFixtureExecutor is deliberately not Docker."""
from __future__ import annotations

import asyncio
import os
import shutil
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol
from uuid import uuid4

from src.workspace.contracts import WorkspaceCapture, blob_sha256, content_id, canonical_json
from .contracts import PROTOCOL, PublicValidationSpec, ValidationEnvironment

RUNNER_BYTES = Path(__file__).with_name("pytest_runner.py").read_bytes()
RUNNER_SHA256 = blob_sha256(RUNNER_BYTES)
RUNNER_ID = content_id("runner-v1", {"protocol": PROTOCOL, "sha256": RUNNER_SHA256})


@dataclass(frozen=True)
class ReplicaRequest:
    capture: WorkspaceCapture
    spec: PublicValidationSpec
    environment: ValidationEnvironment
    operation_id: str
    deadline_mono: float
    cancelled: Callable[[], bool]


@dataclass(frozen=True)
class ReplicaResult:
    execution_status: str
    exit_code: int | None
    report: bytes
    stdout: bytes
    stderr: bytes
    environment_id: str
    before_manifest_id: str | None
    after_manifest_id: str | None
    cleanup_state: str
    truncated: bool = False
    runner_unchanged: bool = True
    cleanup_evidence: dict | None = None


class ReplicaExecutor(Protocol):
    async def execute(self, request: ReplicaRequest, started: Callable[[str], None]) -> ReplicaResult:
        """Call started(unique_owned_replica_id) BEFORE any replica side effect.

        Production foundation must verify base identity, safely apply the shared
        base-relative delta, enforce protected inputs/environment, verify the full
        manifest before/after, persist complete bounded evidence, and clean up all
        owned processes/containers on every exit. No hidden grader inputs.
        """
        ...


class FixtureMaterializer(Protocol):
    def prepare(self, capture: WorkspaceCapture, workspace: Path) -> str:
        """Populate a fresh trusted fixture; return independently verified tree ID."""
        ...

    def verify(self, capture: WorkspaceCapture, workspace: Path) -> str:
        """Return independently rechecked full fixture tree identity."""
        ...


class LocalFixtureExecutor:
    """Trusted local fixture testing only, with injected materialization/verification.

    Does not scan/capture arbitrary repositories, enforce Docker isolation, or
    guarantee cleanup of deliberately detached adversarial child processes.
    temporary_root must be explicitly supplied outside the editor workspace.
    """
    def __init__(self, temporary_root: Path, materializer: FixtureMaterializer):
        self.root = Path(temporary_root).resolve(strict=True)
        self.materializer = materializer

    async def execute(self, request, started):
        if request.environment.profile != "local_fixture":
            raise ValueError("local executor refuses production environment")
        if Path(request.environment.python_bin).resolve() != Path(sys.executable).resolve():
            raise ValueError("fixture executor requires its own interpreter")
        folder = self.root / ("public-validation-" + uuid4().hex)
        workspace = folder / "workspace"
        control = folder / "control"
        proc = None
        tasks = []
        before = after = None
        raw = out = err = b""
        status, code, cleanup = "failed", None, "cleaned"
        truncated = False
        runner_unchanged = True
        started(folder.name)
        try:
            workspace.mkdir(parents=True)
            control.mkdir()
            before = self.materializer.prepare(request.capture, workspace)
            if before != request.capture.manifest_id:
                raise ValueError("materialized manifest mismatch")
            runner = control / "runner.py"
            runner.write_bytes(RUNNER_BYTES)
            args = [*request.spec.selectors, *(arg.format(temp=str(control / "pytest-tmp"))
                                             for arg in request.spec.pytest_args)]
            payload = dict(workspace=str(workspace), operation_id=request.operation_id,
                           imports=list(request.spec.imports), args=args)
            (control / "request.json").write_bytes(canonical_json(payload))
            env = dict(request.environment.env)
            env["PYTHONPATH"] = str(workspace) + os.pathsep + str(workspace / "src")
            env.update(HOME=str(control), TMPDIR=str(control))
            substitutions = dict(python=request.environment.python_bin, runner=str(runner),
                                 request=str(control / "request.json"), report=str(control / "report.json"))
            command = [arg.format(**substitutions) for arg in request.spec.argv_template]
            if request.cancelled() or time.monotonic() >= request.deadline_mono:
                status = "cancelled" if request.cancelled() else "timed_out"
            else:
                proc = await asyncio.create_subprocess_exec(*command, cwd=workspace, env=env,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    **({"start_new_session": True} if os.name != "nt" else {}))
                async def collect(stream):
                    nonlocal truncated
                    chunks, kept = [], 0
                    while True:
                        chunk = await stream.read(8192)
                        if not chunk: break
                        keep = min(len(chunk), max(0, request.spec.limits.max_output_bytes - kept))
                        chunks.append(chunk[:keep]); kept += keep
                        if keep < len(chunk): truncated = True
                    return b"".join(chunks)
                tasks = [asyncio.create_task(collect(proc.stdout)), asyncio.create_task(collect(proc.stderr)),
                         asyncio.create_task(proc.wait())]
                test_deadline = min(request.deadline_mono, time.monotonic() + request.spec.limits.test_seconds)
                while not all(t.done() for t in tasks):
                    if request.cancelled() or time.monotonic() >= test_deadline or truncated:
                        status = ("cancelled" if request.cancelled() else
                                  "failed" if truncated else "timed_out")
                        break
                    await asyncio.wait(tasks, timeout=0.02, return_when=asyncio.FIRST_COMPLETED)
                    await asyncio.sleep(0)
                else:
                    status = "succeeded" if proc.returncode == 0 else "failed"
                if proc.returncode is None:
                    await self._stop(proc)
                await asyncio.wait_for(asyncio.gather(*tasks), 5)
                out, err, code = tasks[0].result(), tasks[1].result(), proc.returncode
            report = control / "report.json"
            if report.is_file() and not report.is_symlink():
                with report.open("rb") as stream:
                    raw = stream.read(request.spec.limits.max_report_bytes + 1)
            runner_unchanged = runner.read_bytes() == RUNNER_BYTES
            after = self.materializer.verify(request.capture, workspace)
        except asyncio.CancelledError:
            status = "cancelled"
        except Exception:
            status = "failed"
        finally:
            try:
                if proc is not None and proc.returncode is None:
                    await self._stop(proc)
                if tasks:
                    await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 5)
            except (Exception, asyncio.CancelledError):
                cleanup = "cleanup_failed"
                for task in tasks: task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            try:
                if not folder.resolve().is_relative_to(self.root):
                    raise ValueError("cleanup target escaped fixture root")
                shutil.rmtree(folder)
            except OSError:
                cleanup = "cleanup_failed"
        return ReplicaResult(status, code, raw, out, err, request.environment.environment_id,
                             before, after, cleanup, truncated, runner_unchanged)

    @staticmethod
    async def _stop(proc):
        if os.name == "nt":
            killer = await asyncio.create_subprocess_exec("taskkill", "/PID", str(proc.pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(killer.wait(), 3)
        else:
            try: os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError: pass
        if proc.returncode is None:
            try: proc.kill()
            except ProcessLookupError: pass
        await asyncio.wait_for(proc.wait(), 3)
