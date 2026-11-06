"""run_command：经 execution backend 有界执行 shell/argv（HP-03）。

English: policy gate + LocalExecutionBackend; host metadata stays in ToolContext.
"""

from __future__ import annotations

import time
import os
import uuid
from pathlib import Path

from src.execution.base import (
    ExecutionRequest,
    ExecutionBackend,
    ExecutionStatus,
    OutputQuotas,
    RunOutputMeter,
    ToolArtifactBudget,
)
from src.execution.local import LocalExecutionBackend, build_minimal_env
from src.tools.base import SideEffectClass, Tool, ToolContext, ToolResult, ToolStatus


class RunCommandTool(Tool):
    name = "run_command"
    description = (
        "Execute a command inside the workspace directory. "
        "Provide either `command` (shell string) or `argv` (argument array for non-shell exec)."
    )
    side_effect = SideEffectClass.NON_IDEMPOTENT
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command string (mutually exclusive with argv).",
            },
            "argv": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Argv list for non-shell execution (mutually exclusive with command).",
            },
        },
        "additionalProperties": False,
    }

    def __init__(
        self,
        command_policy=None,
        timeout_seconds: int = 30,
        env_whitelist: list[str] | None = None,
        prefer_argv: bool = False,
        execution_backend: ExecutionBackend | None = None,
        execution_profile: str | None = None,
        output_quotas: OutputQuotas | None = None,
        observation_bytes_kept: str = "legacy",
    ):
        self.command_policy = command_policy
        self.observation_bytes_kept = observation_bytes_kept
        self.timeout_seconds = timeout_seconds
        self.env_whitelist = env_whitelist or []
        self.prefer_argv = prefer_argv
        if execution_backend is not None:
            self.backend = execution_backend
        else:
            profile = execution_profile or "legacy_host"
            self.backend = LocalExecutionBackend(profile=profile)
        self.output_quotas = output_quotas or OutputQuotas()

    def _emit_command_event(
        self,
        ctx: ToolContext,
        *,
        mode: str,
        exit_code: int | None,
        duration_ms: float,
        timed_out: bool,
        success: bool,
        status: str | None = None,
        cleanup_state: str | None = None,
    ) -> None:
        event_bus = ctx.extra.get("event_bus")
        if not event_bus:
            return
        event_bus.emit(
            "command_executed",
            mode=mode,
            exit_code=exit_code,
            duration_ms=round(duration_ms, 2),
            timed_out=timed_out,
            success=success,
            status=status,
            cleanup_state=cleanup_state,
        )

    @staticmethod
    def _map_status(exec_status: ExecutionStatus) -> ToolStatus:
        return {
            ExecutionStatus.SUCCEEDED: ToolStatus.SUCCEEDED,
            ExecutionStatus.FAILED: ToolStatus.FAILED,
            ExecutionStatus.CANCELLED: ToolStatus.CANCELLED,
            ExecutionStatus.TIMED_OUT: ToolStatus.TIMED_OUT,
            ExecutionStatus.BLOCKED: ToolStatus.BLOCKED,
            ExecutionStatus.UNAVAILABLE: ToolStatus.BLOCKED,
            ExecutionStatus.OUTCOME_UNKNOWN: ToolStatus.OUTCOME_UNKNOWN,
        }.get(exec_status, ToolStatus.FAILED)

    async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
        call_id = self.resolve_call_id(params, ctx)
        command = str(params.get("command", "")).strip()
        argv = params.get("argv")
        use_argv = isinstance(argv, list) and len(argv) > 0

        if use_argv and command:
            return ToolResult(
                call_id=call_id,
                name=self.name,
                success=False,
                output="",
                error="Provide either command or argv, not both.",
                status=ToolStatus.FAILED,
                error_kind="invalid_arguments",
            )
        if not use_argv and not command:
            return ToolResult(
                call_id=call_id,
                name=self.name,
                success=False,
                output="",
                error="No command or argv provided.",
                status=ToolStatus.FAILED,
                error_kind="invalid_arguments",
            )

        mode = "argv" if use_argv else "shell"
        if use_argv:
            argv_list = [str(part) for part in argv]
            if self.command_policy:
                decision = self.command_policy.check_argv(argv_list)
                if not decision.allowed:
                    return ToolResult(
                        call_id=call_id,
                        name=self.name,
                        success=False,
                        output="",
                        error=f"Command blocked by policy: {decision.reason}",
                        status=ToolStatus.BLOCKED,
                        error_kind="policy_block",
                    )
        else:
            argv_list = None
            if self.command_policy:
                decision = self.command_policy.check(command)
                if not decision.allowed:
                    return ToolResult(
                        call_id=call_id,
                        name=self.name,
                        success=False,
                        output="",
                        error=f"Command blocked by policy: {decision.reason}",
                        status=ToolStatus.BLOCKED,
                        error_kind="policy_block",
                    )

        # Host metadata from context only — never from model params.
        deadline = ctx.extra.get("deadline")
        cancel_event = ctx.extra.get("cancel_event")
        run_meter: RunOutputMeter | None = ctx.extra.get("run_output_meter")
        operation_id = ctx.extra.get("operation_id") or call_id or str(uuid.uuid4())
        attempt_id = ctx.extra.get("attempt_id") or "0"
        controlled_tmp = ctx.extra.get("controlled_tmpdir")

        if self.backend.profile == "docker_linux":
            env = {k: os.environ[k] for k in self.env_whitelist if k in os.environ}
        else:
            env = build_minimal_env(self.env_whitelist, controlled_tmpdir=controlled_tmp)

        tool_timeout = self.timeout_seconds
        deadline_mono = None
        if deadline is not None:
            rem = deadline.remaining()
            if rem is not None:
                deadline_mono = time.monotonic() + rem
            # Also bind tool-specific timeout into effective deadline.
            deadline.begin_tool(float(tool_timeout))
            deadline_mono = deadline.effective_deadline_mono()
        else:
            deadline_mono = time.monotonic() + float(tool_timeout)

        artifact_dir = None
        if ctx.extra.get("artifact_dir"):
            artifact_dir = Path(ctx.extra["artifact_dir"]) / operation_id

        request = ExecutionRequest(
            argv=argv_list,
            shell_command=None if use_argv else command,
            cwd=ctx.workspace_root,
            env=env,
            operation_id=operation_id,
            attempt_id=str(attempt_id),
            deadline_mono=deadline_mono,
            cancel_event=cancel_event or (
                deadline.cancel_event if deadline is not None else None
            ),
            quotas=self.output_quotas,
            run_meter=run_meter,
            artifact_budget=ToolArtifactBudget(
                limit_bytes=self.output_quotas.artifact_per_tool_bytes
            ),
            artifact_dir=artifact_dir,
        )

        start = time.monotonic()
        try:
            result = await self.backend.execute(request)
        finally:
            if deadline is not None:
                deadline.clear_tool()

        duration_ms = result.duration_ms or (time.monotonic() - start) * 1000
        tool_status = self._map_status(result.status)
        success = tool_status == ToolStatus.SUCCEEDED
        observation = result.observation_text(self.observation_bytes_kept)
        if result.status == ExecutionStatus.TIMED_OUT:
            observation = f"命令在 {self.timeout_seconds} 秒后超时，输出已截断\n" + observation
        # Truncate body but always retain trailing status/truncation markers.
        if ctx.tool_output_max_chars is not None and len(observation) > ctx.tool_output_max_chars:
            marker = ""
            if "\n[" in observation:
                # Keep last bracketed meta line(s).
                parts = observation.rsplit("\n[", 1)
                if len(parts) == 2:
                    marker = "\n[" + parts[1]
                    body = parts[0]
                else:
                    body = observation
            else:
                body = observation
            keep = max(0, ctx.tool_output_max_chars - len(marker) - 40)
            observation = body[:keep] + "\n... (output truncated)" + marker

        self._emit_command_event(
            ctx,
            mode=mode,
            exit_code=result.exit_code,
            duration_ms=duration_ms,
            timed_out=result.status == ExecutionStatus.TIMED_OUT,
            success=success,
            status=result.status.value,
            cleanup_state=result.cleanup_state.value,
        )

        return ToolResult(
            call_id=call_id,
            name=self.name,
            success=success,
            output=observation,
            error=result.error_message if not success else None,
            status=tool_status,
            error_kind=result.error_kind,
            exit_code=result.exit_code,
            cleanup_state=result.cleanup_state.value,
            cleanup_elapsed_s=result.cleanup_elapsed_s,
            truncated=result.truncated,
            bytes_seen=result.bytes_seen,
            bytes_kept=result.bytes_kept,
        )
