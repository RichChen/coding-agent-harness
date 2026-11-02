"""Thin legacy ExecutionRequest bridge to the single workspace coordinator.

Container creation/capture/validation remain in the P2 services. This adapter only
maps a bounded CommandResult into existing tool output/accounting contracts.
"""
from __future__ import annotations

import time

from src.execution.base import (CleanupState, ExecutionResult, ExecutionStatus,
                                StreamCapture, ToolArtifactBudget)
from src.workspace.control import CaptureControl, CaptureError


class WorkspaceExecutionBackend:
    profile = "docker_linux"
    platform = "linux"

    def __init__(self, controller):
        self.controller = controller

    @property
    def active(self):
        return self.controller.coordinator is not None and self.controller.coordinator.state == "active"

    @property
    def capabilities(self):
        return dict(profile=self.profile, platform=self.platform, transport="workspace-v1",
                    network="none", host_mounts=False, snapshot=False)

    async def execute(self, request):
        started = time.monotonic()
        c = self.controller

        def stopped(status, reason):
            return ExecutionResult(status, error_kind=reason, error_message=reason,
                duration_ms=(time.monotonic()-started)*1000, profile=self.profile, platform=self.platform,
                cleanup_state=CleanupState.CLEANED if c.coordinator and c.coordinator.cleanup_ok else CleanupState.NOT_REQUIRED)

        if c.sealed:
            return stopped(ExecutionStatus.BLOCKED, "workspace_sealed")
        if not self.active:
            return stopped(ExecutionStatus.BLOCKED, "workspace_unavailable")
        if bool(request.argv) == bool(request.shell_command):
            return stopped(ExecutionStatus.BLOCKED, "invalid_request")
        meter = request.run_meter
        budget = request.artifact_budget or ToolArtifactBudget(request.quotas.artifact_per_tool_bytes)
        wire = min(request.quotas.artifact_per_tool_bytes, budget.remaining(),
                   meter.remaining() if meter else request.quotas.run_total_bytes)
        if wire <= 0 or meter and meter.producers_stopped:
            return stopped(ExecutionStatus.BLOCKED, "run_output_limit")
        try:
            # Includes both staging and subsequent atomic publication. The same
            # ArtifactStore's final reserve remains unavailable to ordinary tools.
            c.storage.require_capacity(2 * wire)
            limit = min(request.deadline_mono or c.agent_deadline, c.agent_deadline)
            control = CaptureControl(request.operation_id or "workspace-tool", limit,
                                     request.cancel_event, wire_limit=wire)
            control.remaining(None)
            c.dirty = True  # Every command may write, including commands named "read".
            result = await c.coordinator.execute(request.argv or ["/bin/sh", "-c", request.shell_command],
                                                control, stdin=request.stdin_bytes, env=request.env)
            streams = []
            for name, data in (("stdout", result.stdout), ("stderr", result.stderr)):
                if meter:
                    meter.note_read(len(data), len(data))
                    if meter.try_keep(len(data)) != len(data):
                        return stopped(ExecutionStatus.OUTCOME_UNKNOWN, "run_output_limit")
                kept = budget.take(len(data))
                preview = data[:request.quotas.memory_per_stream_bytes]
                path = None
                if request.artifact_dir is not None:
                    request.artifact_dir.mkdir(parents=True, exist_ok=True)
                    path = request.artifact_dir / (name + ".bin")
                    with path.open("xb") as stream:
                        stream.write(data[:kept])
                streams.append(StreamCapture(bytes_seen=len(data), bytes_kept_memory=len(preview),
                    bytes_kept_artifact=kept if path else 0, truncated=len(preview)<len(data) or kept<len(data),
                    text_preview=preview.decode("utf-8", "replace"), artifact_path=str(path) if path else None,
                    peak_buffer_bytes=len(data), read_chunks=1 if data else 0))
            return ExecutionResult(ExecutionStatus.SUCCEEDED if result.exit_code==0 else ExecutionStatus.FAILED,
                exit_code=result.exit_code, stdout=streams[0], stderr=streams[1],
                truncated=any(s.truncated for s in streams), bytes_seen=sum(s.bytes_seen for s in streams),
                bytes_kept=sum(s.bytes_kept_artifact for s in streams),
                duration_ms=(time.monotonic()-started)*1000, platform=self.platform, profile=self.profile)
        except CaptureError as exc:
            status = (ExecutionStatus.CANCELLED if exc.reason=="cancelled" else ExecutionStatus.TIMED_OUT
                      if exc.reason in ("deadline", "operation_timeout") else ExecutionStatus.OUTCOME_UNKNOWN)
            return stopped(status, "user_cancelled" if exc.reason=="cancelled" else
                           "tool_timeout" if status==ExecutionStatus.TIMED_OUT else exc.reason)
