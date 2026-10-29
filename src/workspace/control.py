"""Shared deadlines, cancellation and stable foundation errors."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


class CaptureError(RuntimeError):
    def __init__(self, reason: str, *, evidence=None):
        super().__init__(reason)
        self.reason = reason
        self.evidence = evidence or {}


@dataclass(frozen=True)
class CaptureControl:
    operation_id: str
    deadline: float  # absolute time.monotonic(), not wall time / content identity
    cancel: asyncio.Event | None = None
    wire_limit: int = 32 * 1024 * 1024
    artifact_limit: int = 16 * 1024 * 1024

    def __post_init__(self):
        if not isinstance(self.operation_id, str) or not self.operation_id:
            raise ValueError("operation_id required")
        if not isinstance(self.deadline, (int, float)) or not 0 < self.deadline < float("inf"):
            raise ValueError("finite absolute monotonic deadline required")
        if any(type(value) is not int or value <= 0 for value in (self.wire_limit, self.artifact_limit)):
            raise ValueError("positive byte quotas required")

    def remaining(self, maximum=30.0):
        """Remaining deadline, optionally capped for a management RPC.

        ``None`` removes only the internal cap, never the absolute deadline.
        """
        if self.cancel is not None and self.cancel.is_set():
            raise CaptureError("cancelled")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise CaptureError("deadline")
        return remaining if maximum is None else min(maximum, remaining)

    async def wait(self, awaitable, maximum=30.0):
        """Wait with cancellation priority when both waiter tasks finish together.

        Settle the work even when its result is discarded. Resource-acquiring
        callers must track ownership inside that work and release it on rejection.
        """
        task = asyncio.ensure_future(awaitable)
        cancelled = None
        try:
            timeout = self.remaining(maximum)
            cancelled = asyncio.create_task(self.cancel.wait()) if self.cancel is not None else None
            done, _ = await asyncio.wait([task, *([cancelled] if cancelled else [])], timeout=timeout,
                                         return_when=asyncio.FIRST_COMPLETED)
            if cancelled is not None and cancelled in done:
                raise CaptureError("cancelled")
            if task not in done:
                raise CaptureError("deadline" if time.monotonic() >= self.deadline else "operation_timeout")
            return task.result()
        finally:
            for pending in (task, cancelled):
                if pending is not None and not pending.done():
                    pending.cancel()
            await asyncio.gather(*[t for t in (task, cancelled) if t is not None], return_exceptions=True)
