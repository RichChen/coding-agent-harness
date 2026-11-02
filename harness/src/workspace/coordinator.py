"""Single per-container lock for edits, helper calls, capture, and close."""
from __future__ import annotations

import asyncio
import copy
import time
from contextlib import asynccontextmanager

from src.workspace.control import CaptureControl, CaptureError


def cleanup_exception(exc):
    """No arbitrary exception message, environment, or unbounded traceback."""
    return dict(exception_type=type(exc).__name__, reason=getattr(exc, "reason", None),
                cause_type=type(exc.__cause__).__name__ if exc.__cause__ else None)


class WorkspaceCoordinator:
    def __init__(self, transport):
        self._transport = transport
        self._lock = asyncio.Lock()
        self.state = "new"
        self.cleanup_ok = None
        self.cleanup_evidence = []

    @property
    def transport_identity(self):
        t = self._transport
        return dict(container_id=t.container_id, container_name=t.name, context=t.context,
                    image_digest=t.image_digest, image_id=t.image_id, workspace="/workspace")

    async def _cleanup(self):
        # Cleanup is the sole bounded operation allowed after cancellation/deadline.
        cleanup = CaptureControl("owned-cleanup", time.monotonic() + 10)
        error = None
        try:
            self.cleanup_ok = await self._transport.close(cleanup)
        except (Exception, asyncio.CancelledError) as exc:
            self.cleanup_ok = False
            error = cleanup_exception(exc)
        finally:
            self.cleanup_evidence.append(dict(cleanup_ok=self.cleanup_ok, error=error,
                transport=copy.deepcopy(getattr(self._transport, "cleanup_evidence", None))))

    @asynccontextmanager
    async def _locked(self, control):
        acquired = False

        async def acquire():
            nonlocal acquired
            await self._lock.acquire()
            # No suspension between successful acquisition and recording ownership.
            acquired = True

        try:
            await control.wait(acquire(), maximum=None)
            # Cancellation/deadline can coincide with a successful acquisition.
            # Reject work before entering, while still releasing our own lock.
            control.remaining(None)
            yield
        finally:
            if acquired:
                self._lock.release()

    @asynccontextmanager
    async def operation(self, control, *, starting=False):
        async with self._locked(control):
            if self.state != ("new" if starting else "active"):
                raise CaptureError("workspace_unavailable")
            try:
                yield self._transport
            except BaseException as exc:
                self.state = "failed"
                await self._cleanup()
                if isinstance(exc, CaptureError):
                    exc.evidence["cleanup_ok"] = self.cleanup_ok
                elif isinstance(exc, Exception):
                    raise CaptureError("workspace_operation_failed", evidence={
                        "exception_type": type(exc).__name__, "cleanup_ok": self.cleanup_ok}) from exc
                raise

    async def start(self, control):
        async with self.operation(control, starting=True) as transport:
            await transport.start(control)
            self.state = "active"

    async def execute(self, argv, control, *, stdin=None, env=None):
        async with self.operation(control) as transport:
            return await transport.execute(argv, control, stdin=stdin, env=env)

    async def close(self, control=None):
        # The ongoing operation is bounded by its own control; close never races it.
        control = control or CaptureControl("owned-close", time.monotonic() + 15)
        async with self._locked(control):
            await self._cleanup()
            self.state = "closed"
            return self.cleanup_ok
