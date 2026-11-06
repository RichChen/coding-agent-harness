"""Local trusted_local / legacy_host execution backends (HP-03).

Linux controlled: new session/process group + TERM→grace→KILL→wait + group verify.
Windows controlled: blocked before spawn.
legacy_host: explicit host exec; no tree-cleanup claim.
Cleanup always runs in finally (task.cancel / cancel_event / deadline / quota).
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
from pathlib import Path

from src.execution.base import (
    CleanupState,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    OutputQuotas,
    ToolArtifactBudget,
)
from src.execution.collector import collect_stream
from src.runtime.deadline import CLEANUP_GRACE_S, CleanupReport


def platform_name() -> str:
    return sys.platform


def supports_process_group_cleanup(platform: str | None = None) -> bool:
    p = platform or platform_name()
    return p.startswith("linux")


def group_has_live_members(pgid: int) -> bool:
    """Linux zombies cannot execute or write. Permission/inspection failure is unknown/live."""
    try:
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                if int(fields[2]) == pgid and fields[0] not in ("Z", "X"):
                    return True
            except FileNotFoundError:
                continue
        return False
    except (OSError, ValueError, IndexError):
        try:
            os.killpg(pgid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True


class LocalExecutionBackend:
    def __init__(self, profile: str = "controlled"):
        if profile not in ("controlled", "legacy_host"):
            raise ValueError(f"unknown execution profile: {profile}")
        self.profile = profile
        self.platform = platform_name()

    @property
    def capabilities(self) -> dict:
        linux = supports_process_group_cleanup(self.platform)
        return {
            "profile": self.profile,
            "platform": self.platform,
            "cancel": True,
            "process_group_cleanup": bool(linux and self.profile == "controlled"),
            "filesystem_isolation": False,
            "network_control": False,
            "resource_limits": False,
            "complete_isolation": False,
            "tree_reclaim_guaranteed": bool(linux and self.profile == "controlled"),
            "notes": (
                "process group cannot prevent escape outside the group; "
                "legacy_host never claims tree reclaim; "
                "strong isolation is H3 containers, not this backend"
            ),
        }

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        start = time.monotonic()
        if not request.argv and not request.shell_command:
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                error_kind="invalid_request",
                error_message="Provide argv or shell_command",
                duration_ms=0.0,
                platform=self.platform,
                profile=self.profile,
            )

        if self.profile == "controlled" and not supports_process_group_cleanup(self.platform):
            return ExecutionResult(
                status=ExecutionStatus.BLOCKED,
                error_kind="unavailable",
                error_message=(
                    f"controlled execution profile cannot guarantee process-tree cleanup "
                    f"on platform={self.platform}; refusing to spawn. "
                    f"Use explicit legacy_host only with migration acknowledgement, "
                    f"or run on Linux."
                ),
                cleanup_state=CleanupState.NOT_REQUIRED,
                duration_ms=(time.monotonic() - start) * 1000,
                platform=self.platform,
                profile=self.profile,
            )

        if request.run_meter is not None and request.run_meter.producers_stopped:
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                error_kind="run_output_limit",
                error_message="run output quota already exhausted; refusing new producer",
                cleanup_state=CleanupState.NOT_REQUIRED,
                duration_ms=(time.monotonic() - start) * 1000,
                platform=self.platform,
                profile=self.profile,
            )

        if request.cancel_event is not None and request.cancel_event.is_set():
            return ExecutionResult(
                status=ExecutionStatus.CANCELLED,
                error_kind="user_cancelled",
                error_message="cancelled before spawn",
                cleanup_state=CleanupState.NOT_REQUIRED,
                duration_ms=(time.monotonic() - start) * 1000,
                platform=self.platform,
                profile=self.profile,
            )
        if request.deadline_mono is not None and time.monotonic() >= request.deadline_mono:
            return ExecutionResult(
                status=ExecutionStatus.TIMED_OUT,
                error_kind="tool_timeout",
                error_message="deadline already passed before spawn",
                cleanup_state=CleanupState.NOT_REQUIRED,
                duration_ms=(time.monotonic() - start) * 1000,
                platform=self.platform,
                profile=self.profile,
            )

        quotas = request.quotas or OutputQuotas()
        artifact_budget = request.artifact_budget or ToolArtifactBudget(
            limit_bytes=quotas.artifact_per_tool_bytes
        )
        cancel_producer = asyncio.Event()
        artifact_dir = request.artifact_dir
        stdout_art = (artifact_dir / "stdout.bin") if artifact_dir else None
        stderr_art = (artifact_dir / "stderr.bin") if artifact_dir else None
        use_session = self.profile == "controlled" and supports_process_group_cleanup(
            self.platform
        )
        grace = request.cleanup_grace_s or CLEANUP_GRACE_S

        kwargs: dict = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "cwd": str(request.cwd) if request.cwd else None,
            "env": request.env,
        }
        if use_session:
            kwargs["start_new_session"] = True

        proc: asyncio.subprocess.Process | None = None
        out_task: asyncio.Task | None = None
        err_task: asyncio.Task | None = None
        wait_task: asyncio.Task | None = None
        status = ExecutionStatus.SUCCEEDED
        error_kind: str | None = None
        error_message: str | None = None
        exit_code: int | None = None
        timed_out = False
        cancelled = False
        run_cap_hit = False
        externally_cancelled = False
        cleanup = CleanupReport(ok=True, elapsed_s=0.0, state="not_required")
        stdout_cap = None
        stderr_cap = None

        try:
            try:
                if request.argv:
                    spawn = asyncio.create_task(asyncio.create_subprocess_exec(*request.argv, **kwargs))
                else:
                    spawn = asyncio.create_task(asyncio.create_subprocess_shell(request.shell_command or "", **kwargs))
                try:
                    # Cancellation must not lose the handle of a process just created.
                    proc = await asyncio.shield(spawn)
                except asyncio.CancelledError:
                    await asyncio.wait({spawn}, timeout=grace / 2)
                    if spawn.done():
                        proc = spawn.result()
                    else:
                        cleanup = CleanupReport(ok=False, elapsed_s=grace / 2,
                                                state="cleanup_failed", detail="spawn still pending")
                        async def reclaim_late(task):
                            try:
                                late_proc = task.result()
                                await self._cleanup_process(late_proc, use_session=use_session, grace_s=grace)
                                transport = getattr(late_proc, "_transport", None)
                                if transport is not None:
                                    transport.close()
                            except (Exception, asyncio.CancelledError):
                                pass
                        spawn.add_done_callback(lambda t: asyncio.create_task(reclaim_late(t)))
                    raise
            except Exception as exc:
                return ExecutionResult(
                    status=ExecutionStatus.FAILED,
                    error_kind="spawn_failed",
                    error_message=str(exc),
                    duration_ms=(time.monotonic() - start) * 1000,
                    platform=self.platform,
                    profile=self.profile,
                )

            out_task = asyncio.create_task(
                collect_stream(
                    proc.stdout,
                    quotas=quotas,
                    run_meter=request.run_meter,
                    artifact_path=stdout_art,
                    artifact_budget=artifact_budget,
                    cancel_producer=cancel_producer,
                )
            )
            err_task = asyncio.create_task(
                collect_stream(
                    proc.stderr,
                    quotas=quotas,
                    run_meter=request.run_meter,
                    artifact_path=stderr_art,
                    artifact_budget=artifact_budget,
                    cancel_producer=cancel_producer,
                )
            )
            wait_task = asyncio.create_task(proc.wait())

            while not wait_task.done():
                if proc.returncode is not None:
                    # wait() can still await pipe EOF held open by a surviving child.
                    exit_code = proc.returncode
                    break
                if request.cancel_event is not None and request.cancel_event.is_set():
                    cancelled = True
                    cancel_producer.set()
                    break
                if request.deadline_mono is not None and time.monotonic() >= request.deadline_mono:
                    timed_out = True
                    cancel_producer.set()
                    break
                if request.run_meter is not None and request.run_meter.cancelled_producer:
                    run_cap_hit = True
                    cancel_producer.set()
                    break
                # Collector failure must not leave producer running.
                for t in (out_task, err_task):
                    if t.done() and t.exception() is not None:
                        cancel_producer.set()
                        error_kind = "collector_error"
                        error_message = str(t.exception())
                        break
                if error_kind == "collector_error":
                    break
                done, _ = await asyncio.wait({wait_task}, timeout=0.05)
                if done:
                    break

            if wait_task.done():
                exit_code = wait_task.result()

        except asyncio.CancelledError:
            # Do not re-raise: finally reclaims process, then we return CANCELLED.
            # Re-raising would skip structured ToolResult and confuse outer await_with_control.
            externally_cancelled = True
            cancelled = True
            cancel_producer.set()
        finally:
            async def reclaim():
                nonlocal cleanup, stdout_cap, stderr_cap, exit_code
                # Always reclaim: cancel_event, deadline, quota, task.cancel, collector errors.
                if proc is not None:
                    needs_cleanup = (
                        cancelled
                        or timed_out
                        or run_cap_hit
                        or externally_cancelled
                        or error_kind == "collector_error"
                        or (wait_task is not None and not wait_task.done() and proc.returncode is None)
                        or (proc.returncode is None)
                    )
                    if needs_cleanup:
                        cleanup = await self._cleanup_process(
                            proc,
                            use_session=use_session,
                            grace_s=grace,
                            pgid=proc.pid if use_session else None,
                        )
                    elif proc.returncode is not None and use_session:
                        # Leader exited — still verify group is empty when controlled.
                        cleanup = await self._verify_group_gone(
                            proc.pid, grace_s=min(0.5, grace), already=cleanup
                        )

                # Drain normal EOF before cancellation, preserving the final pipe buffers.
                pending_collectors = {t for t in (out_task, err_task) if t is not None and not t.done()}
                if pending_collectors:
                    await asyncio.wait(pending_collectors, timeout=min(0.2, grace))
                # Bound collector wait — never hang on EOF forever.
                for t in (out_task, err_task):
                    if t is None:
                        continue
                    if not t.done():
                        t.cancel()
                caps = []
                for t in (out_task, err_task):
                    if t is None:
                        caps.append(None)
                        continue
                    try:
                        caps.append(await asyncio.wait_for(asyncio.shield(t), timeout=grace))
                    except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                        from src.execution.base import StreamCapture

                        caps.append(StreamCapture(truncated=True))
                stdout_cap, stderr_cap = caps[0], caps[1]

                if wait_task is not None and not wait_task.done():
                    wait_task.cancel()
                    try:
                        await asyncio.wait_for(wait_task, timeout=0.2)
                    except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                        pass
                elif wait_task is not None and wait_task.done() and exit_code is None:
                    try:
                        exit_code = wait_task.result()
                    except (Exception, asyncio.CancelledError):
                        pass

                # Close pipe transports to avoid Windows ProactorBasePipeTransport.__del__ warnings.
                if proc is not None:
                    for pipe_attr in ("stdout", "stderr", "stdin"):
                        pipe = getattr(proc, pipe_attr, None)
                        if pipe is None:
                            continue
                        transport = getattr(pipe, "_transport", None)
                        if transport is not None:
                            try:
                                transport.close()
                            except Exception:
                                pass
                        feeder = getattr(pipe, "feed_eof", None)
                        if callable(feeder):
                            try:
                                feeder()
                            except Exception:
                                pass

            # A second cancel (e.g. the outer tool deadline) can land while
            # reclaiming; the reclaim still runs to the end and the result is kept.
            settle = asyncio.ensure_future(reclaim())
            while True:
                try:
                    await asyncio.shield(settle)
                    break
                except asyncio.CancelledError:
                    if settle.done(): raise

        if externally_cancelled and not cancelled:
            cancelled = True

        from src.execution.base import StreamCapture

        if stdout_cap is None:
            stdout_cap = StreamCapture()
        if stderr_cap is None:
            stderr_cap = StreamCapture()

        if cancelled:
            status = ExecutionStatus.CANCELLED
            error_kind = error_kind or "user_cancelled"
            error_message = error_message or "cancelled during execution"
        elif timed_out:
            status = ExecutionStatus.TIMED_OUT
            error_kind = "tool_timeout"
            error_message = "tool/request deadline exceeded"
        elif run_cap_hit:
            status = ExecutionStatus.FAILED
            error_kind = "run_output_limit"
            error_message = "run output quota exceeded; producer cancelled"
        elif error_kind == "collector_error":
            status = ExecutionStatus.OUTCOME_UNKNOWN
        elif exit_code not in (0, None):
            status = ExecutionStatus.FAILED
            error_kind = "nonzero_exit"
            error_message = f"exit_code={exit_code}"

        if cleanup.state == "cleanup_failed":
            status = ExecutionStatus.OUTCOME_UNKNOWN
            error_kind = "cleanup_failed"
            error_message = (
                (error_message + "; " if error_message else "")
                + "cleanup could not be confirmed; outcome_unknown"
            )

        truncated = bool(stdout_cap.truncated or stderr_cap.truncated)
        bytes_seen = stdout_cap.bytes_seen + stderr_cap.bytes_seen
        bytes_kept = stdout_cap.bytes_kept_artifact + stderr_cap.bytes_kept_artifact
        bytes_kept_memory = stdout_cap.bytes_kept_memory + stderr_cap.bytes_kept_memory
        cleanup_state = {
            "cleaned": CleanupState.CLEANED,
            "cleanup_failed": CleanupState.CLEANUP_FAILED,
            "not_required": CleanupState.NOT_REQUIRED,
            "skipped": CleanupState.SKIPPED,
        }.get(cleanup.state, CleanupState.NOT_REQUIRED)

        return ExecutionResult(
            status=status,
            exit_code=exit_code,
            truncated=truncated,
            stdout=stdout_cap,
            stderr=stderr_cap,
            bytes_seen=bytes_seen,
            bytes_kept=bytes_kept,
            bytes_kept_memory=bytes_kept_memory,
            legacy_trailer_bytes_kept=bytes_kept_memory + bytes_kept,
            cleanup_state=cleanup_state,
            cleanup_elapsed_s=cleanup.elapsed_s,
            error_kind=error_kind,
            error_message=error_message,
            duration_ms=(time.monotonic() - start) * 1000,
            platform=self.platform,
            profile=self.profile,
        )

    async def _verify_group_gone(
        self, pgid: int | None, *, grace_s: float, already: CleanupReport
    ) -> CleanupReport:
        if pgid is None or not supports_process_group_cleanup(self.platform):
            return already
        started = time.monotonic()
        # Signal 0 to process group — if any member alive, escalate to KILL.
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return already
        except PermissionError:
            return CleanupReport(
                ok=False,
                elapsed_s=time.monotonic() - started,
                state="cleanup_failed",
                detail="cannot signal process group after leader exit",
            )
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            return already
        except Exception as exc:
            return CleanupReport(
                ok=False,
                elapsed_s=time.monotonic() - started,
                state="cleanup_failed",
                detail=f"group kill after leader exit failed: {exc}",
            )
        while group_has_live_members(pgid) and time.monotonic() - started < grace_s:
            await asyncio.sleep(.02)
        if not group_has_live_members(pgid):
            return CleanupReport(ok=True, elapsed_s=time.monotonic() - started,
                                 state="cleaned", detail="no live process-group members")
        try:
            os.killpg(pgid, 0)
            return CleanupReport(
                ok=False,
                elapsed_s=time.monotonic() - started,
                state="cleanup_failed",
                detail="process group still alive after leader exit + KILL",
            )
        except ProcessLookupError:
            return CleanupReport(
                ok=True,
                elapsed_s=time.monotonic() - started,
                state="cleaned",
                detail="group reaped after leader exit",
            )

    async def _cleanup_process(
        self,
        proc: asyncio.subprocess.Process,
        *,
        use_session: bool,
        grace_s: float,
        pgid: int | None = None,
    ) -> CleanupReport:
        """TERM → grace → KILL → wait. Total wall budget ≈ grace_s (split across phases)."""
        started = time.monotonic()
        budget = max(0.05, grace_s)
        half = budget / 2.0
        pid = pgid or proc.pid

        def _alive_group() -> bool:
            if not use_session or not pid:
                return proc.returncode is None
            return group_has_live_members(pid)

        # Phase 1: TERM
        try:
            if use_session and pid:
                try:
                    os.killpg(pid, signal.SIGTERM)
                except ProcessLookupError:
                    return CleanupReport(
                        ok=True,
                        elapsed_s=time.monotonic() - started,
                        state="cleaned",
                        detail="gone before/at term",
                    )
                except PermissionError:
                    if proc.returncode is None:
                        proc.terminate()
            elif proc.returncode is None:
                proc.terminate()
        except ProcessLookupError:
            return CleanupReport(
                ok=True,
                elapsed_s=time.monotonic() - started,
                state="cleaned",
                detail="gone at term",
            )

        try:
            await asyncio.wait_for(proc.wait(), timeout=half)
        except asyncio.TimeoutError:
            pass

        if not _alive_group() and proc.returncode is not None:
            return CleanupReport(
                ok=True,
                elapsed_s=time.monotonic() - started,
                state="cleaned",
                detail="terminated within grace",
            )

        # Phase 2: KILL (group) — required even if leader already exited but children remain.
        try:
            if use_session and pid:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    if proc.returncode is None:
                        proc.kill()
            elif proc.returncode is None:
                proc.kill()
        except ProcessLookupError:
            pass

        remaining = max(0.01, budget - (time.monotonic() - started))
        try:
            if proc.returncode is None:
                await asyncio.wait_for(proc.wait(), timeout=remaining)
        except asyncio.TimeoutError:
            pass

        while _alive_group() and time.monotonic() - started < budget:
            await asyncio.sleep(.02)
        if _alive_group() or proc.returncode is None:
            return CleanupReport(
                ok=False,
                elapsed_s=time.monotonic() - started,
                state="cleanup_failed",
                detail=(
                    "process/group still alive after TERM/KILL/wait within "
                    f"{budget:.2f}s total grace; outcome_unknown"
                ),
            )
        return CleanupReport(
            ok=True,
            elapsed_s=time.monotonic() - started,
            state="cleaned",
            detail="killed within grace",
        )


def build_minimal_env(
    env_whitelist: list[str] | None,
    *,
    controlled_tmpdir: Path | None = None,
) -> dict[str, str]:
    """Build subprocess env — never default-inherit HOME/USERPROFILE or full host env."""
    if sys.platform.startswith("win"):
        # PATHEXT required so cmd resolves `python` → `python.exe` without full env.
        minimal_keys = (
            "PATH",
            "PATHEXT",
            "SystemRoot",
            "SYSTEMROOT",
            "WINDIR",
            "COMSPEC",
            "TEMP",
            "TMP",
        )
    else:
        minimal_keys = ("PATH", "TMPDIR", "LANG", "LC_ALL")

    out: dict[str, str] = {}
    host = os.environ

    for key in minimal_keys:
        if key in host:
            out[key] = host[key]

    # Sensible Windows default if host somehow lacks PATHEXT.
    if sys.platform.startswith("win") and "PATHEXT" not in out:
        out["PATHEXT"] = ".COM;.EXE;.BAT;.CMD;.VBS;.JS;.WS;.MSC"

    if controlled_tmpdir is not None:
        controlled_tmpdir.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            out["TEMP"] = str(controlled_tmpdir)
            out["TMP"] = str(controlled_tmpdir)
        else:
            out["TMPDIR"] = str(controlled_tmpdir)

    if env_whitelist:
        allowed = {k.upper() for k in env_whitelist}
        for key, value in host.items():
            if key.upper() in allowed:
                out[key] = value

    return out
