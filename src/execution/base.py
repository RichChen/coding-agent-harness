"""Execution request/result contracts and output quotas (HP-03)."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


# Fixed defaults — recorded in manifests / evidence.
MEMORY_PER_STREAM_BYTES = 64 * 1024  # 64 KiB retained in memory per stdout/stderr
ARTIFACT_PER_TOOL_BYTES = 1 * 1024 * 1024  # 1 MiB per-tool artifact on disk
RUN_OUTPUT_TOTAL_BYTES = 16 * 1024 * 1024  # 16 MiB total per run (memory+disk accounted)


class ExecutionStatus(str, enum.Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    BLOCKED = "blocked"
    OUTCOME_UNKNOWN = "outcome_unknown"
    UNAVAILABLE = "unavailable"


class CleanupState(str, enum.Enum):
    NOT_REQUIRED = "not_required"
    CLEANED = "cleaned"
    CLEANUP_FAILED = "cleanup_failed"
    SKIPPED = "skipped"


@dataclass
class OutputQuotas:
    memory_per_stream_bytes: int = MEMORY_PER_STREAM_BYTES
    artifact_per_tool_bytes: int = ARTIFACT_PER_TOOL_BYTES
    run_total_bytes: int = RUN_OUTPUT_TOTAL_BYTES


@dataclass
class ToolArtifactBudget:
    """Shared per-tool artifact disk budget across stdout+stderr."""

    limit_bytes: int = ARTIFACT_PER_TOOL_BYTES
    bytes_written: int = 0

    def remaining(self) -> int:
        return max(0, self.limit_bytes - self.bytes_written)

    def take(self, n: int) -> int:
        allow = min(n, self.remaining())
        self.bytes_written += allow
        return allow


@dataclass
class RunOutputMeter:
    """Shared run-level output accounting (bytes kept toward run cap)."""

    limit_bytes: int = RUN_OUTPUT_TOTAL_BYTES
    bytes_kept: int = 0
    bytes_seen: int = 0
    peak_read_buffer_bytes: int = 0
    cancelled_producer: bool = False
    producers_stopped: bool = False

    def remaining(self) -> int:
        return max(0, self.limit_bytes - self.bytes_kept)

    def note_read(self, chunk_len: int, buffer_size: int) -> None:
        self.bytes_seen += chunk_len
        if buffer_size > self.peak_read_buffer_bytes:
            self.peak_read_buffer_bytes = buffer_size

    def try_keep(self, n: int) -> int:
        """Return how many of n bytes may still be kept under the run cap."""
        allow = min(n, self.remaining())
        self.bytes_kept += allow
        if allow < n:
            self.cancelled_producer = True
            self.producers_stopped = True
        return allow

    def snapshot(self) -> dict[str, Any]:
        return {
            "limit_bytes": self.limit_bytes,
            "bytes_kept": self.bytes_kept,
            "bytes_seen": self.bytes_seen,
            "peak_read_buffer_bytes": self.peak_read_buffer_bytes,
            "cancelled_producer": self.cancelled_producer,
            "producers_stopped": self.producers_stopped,
        }


@dataclass
class ExecutionRequest:
    """Host-only request — never inject these fields into model tool arguments."""

    argv: list[str] | None = None
    shell_command: str | None = None
    cwd: Path | None = None
    env: dict[str, str] | None = None
    operation_id: str = ""
    attempt_id: str = ""
    deadline_mono: float | None = None
    cancel_event: Any = None  # asyncio.Event | None
    quotas: OutputQuotas = field(default_factory=OutputQuotas)
    run_meter: RunOutputMeter | None = None
    artifact_budget: ToolArtifactBudget | None = None
    artifact_dir: Path | None = None
    cleanup_grace_s: float = 2.0
    stdin_bytes: bytes | None = None  # host-controlled container RPC input


@dataclass
class StreamCapture:
    bytes_seen: int = 0
    bytes_kept_memory: int = 0
    bytes_kept_artifact: int = 0
    truncated: bool = False
    text_preview: str = ""
    artifact_path: str | None = None
    peak_buffer_bytes: int = 0
    read_chunks: int = 0


@dataclass
class ExecutionResult:
    status: ExecutionStatus
    exit_code: int | None = None
    truncated: bool = False
    stdout: StreamCapture = field(default_factory=StreamCapture)
    stderr: StreamCapture = field(default_factory=StreamCapture)
    bytes_seen: int = 0
    # Bytes persisted to the per-tool artifact files. Not RunOutputMeter.bytes_kept.
    bytes_kept: int = 0
    cleanup_state: CleanupState = CleanupState.NOT_REQUIRED
    cleanup_elapsed_s: float = 0.0
    error_kind: str | None = None
    error_message: str | None = None
    duration_ms: float = 0.0
    platform: str = ""
    profile: str = ""
    bytes_kept_memory: int = 0
    # What the trailer showed before bytes_kept was corrected (memory + artifact).
    legacy_trailer_bytes_kept: int | None = None

    def observation_text(self, trailer_bytes_kept: str = "legacy") -> str:
        """Safe model-visible observation including partial stdout/stderr on failure."""
        if trailer_bytes_kept not in ("legacy", "persisted"):
            raise ValueError(f"unknown trailer_bytes_kept: {trailer_bytes_kept}")
        shown = self.bytes_kept
        if trailer_bytes_kept == "legacy" and self.legacy_trailer_bytes_kept is not None:
            shown = self.legacy_trailer_bytes_kept
        parts: list[str] = []
        if self.stdout.text_preview:
            parts.append(self.stdout.text_preview)
        if self.stderr.text_preview:
            parts.append("[stderr]\n" + self.stderr.text_preview)
        meta = (
            f"[exit_code={self.exit_code if self.exit_code is not None else 'null'} "
            f"status={self.status.value} truncated={str(self.truncated).lower()} "
            f"bytes_seen={self.bytes_seen} bytes_kept={shown} "
            f"cleanup={self.cleanup_state.value} duration_ms={self.duration_ms:.0f}]"
        )
        parts.append(meta)
        if self.error_message:
            parts.append(f"Error: {self.error_message}")
        return "\n".join(p for p in parts if p).strip() or meta


class ExecutionBackend(Protocol):
    profile: str
    platform: str

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        ...
