"""重试策略：每次实际 attempt 独立预留/结算，退避受 deadline/cancel 约束。"""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from src.llm.client import LLMError, LLMErrorKind, provider_metadata
from src.policy.model_budget import BudgetLedgerError, ModelBudgetLedger
from src.runtime.awaitctl import await_with_control, estimate_input_tokens
from src.runtime.deadline import DeadlineClock, DeadlineExceeded, UserCancelled

logger = logging.getLogger(__name__)


@dataclass
class RetryConfig:
    max_retries: int = 2
    backoff_base_seconds: float = 1.0
    backoff_factor: float = 2.0
    retry_on: list[str] = field(default_factory=lambda: ["timeout", "model_error", "http", "network"])
    stop_on_unknown_usage: bool = False


@dataclass
class AttemptRecord:
    attempt: int
    success: bool
    error_kind: str | None = None
    status_code: int | None = None
    message: str | None = None
    elapsed_ms: float = 0.0
    usage: dict[str, Any] | None = None
    usage_provenance: str | None = None
    reservation_id: int | None = None
    estimate_method: str | None = None
    estimated_input_tokens: int | None = None
    provider_metadata: dict | None = None
    billing_stop_reason: str | None = None


@dataclass
class AttemptSink:
    attempts: list[AttemptRecord] = field(default_factory=list)
    on_record: Callable | None = None

    def record(self, record: AttemptRecord) -> None:
        self.attempts.append(record)
        if self.on_record:
            self.on_record(record)


class PaidCallStopped(LLMError):
    """Opt-in stop: evidence is settled; do not retry or consume its tool calls."""
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason, kind=LLMErrorKind.INCOMPLETE)


def complete_billing_usage(usage):
    if not isinstance(usage, dict) or usage.get('provenance') != 'provider': return False
    values = [usage.get(k) for k in ('prompt_tokens','completion_tokens','total_tokens')]
    return (all(type(v) is int and 0 <= v <= 2**63-1 for v in values)
            and values[0] + values[1] == values[2])


class RetryPolicy:
    def __init__(
        self,
        config: RetryConfig,
        sink: AttemptSink | None = None,
        deadline: DeadlineClock | None = None,
        model_ledger: ModelBudgetLedger | None = None,
    ):
        self.config = config
        self.sink = sink or AttemptSink()
        self.deadline = deadline
        self.model_ledger = model_ledger
        # Optional pilot ledger is host-owned and may be attached by a runner.
        self.cny_ledger = None
        self.cny_phase = "pilot"
        self.cny_component = "agent"
        self.session = None
        self.billing_stop_reason = None

    def should_retry(self, error: Exception) -> bool:
        if isinstance(error, (UserCancelled, DeadlineExceeded, asyncio.CancelledError, BudgetLedgerError)):
            return False
        if isinstance(error, LLMError):
            if error.kind in (LLMErrorKind.PARSE, LLMErrorKind.INCOMPLETE):
                return False
            if error.kind == LLMErrorKind.HTTP:
                if "http" not in self.config.retry_on:
                    return False
                code = error.status_code
                if code is None:
                    return True
                if code == 429 or code >= 500:
                    return True
                return False
            if error.kind == LLMErrorKind.NETWORK:
                return "network" in self.config.retry_on or "model_error" in self.config.retry_on
            if error.kind == LLMErrorKind.TIMEOUT:
                return "timeout" in self.config.retry_on
            return error.kind.value in self.config.retry_on
        return self._classify(error) in self.config.retry_on

    async def execute(
        self,
        fn: Callable[..., Any],
        *args,
        messages: list[dict] | None = None,
        tools: list[dict] | None = None,
        max_output_tokens: int = 4096,
        **kwargs,
    ):
        """Each loop iteration is one real attempt: reserve → call → settle."""
        strict = self.config.stop_on_unknown_usage
        if strict:
            reason = self.billing_stop_reason or next((a.billing_stop_reason for a in self.sink.attempts
                                                       if a.billing_stop_reason), None)
            if reason: raise PaidCallStopped(reason)
            if self.model_ledger is None: raise ValueError('paid audit requires an existing model ledger')
        last_error: Exception | None = None
        # Prefer explicit messages/tools kwargs; else first two positional for chat-like fns.
        msg_arg = messages if messages is not None else (args[0] if args else None)
        tool_arg = tools if tools is not None else (args[1] if len(args) > 1 else None)
        operation_id = uuid.uuid4().hex

        for attempt in range(self.config.max_retries + 1):
            attempt_id = uuid.uuid4().hex
            if self.session:
                self.sink.on_record = lambda record: self.session.model_settled(
                    self.model_ledger, operation_id, attempt_id, record)
            if self.deadline is not None:
                self.deadline.raise_if_expired_or_cancelled()

            reservation = None
            cny_reservation = None
            estimate_method = None
            estimated_input = None
            if self.model_ledger is not None:
                estimated_input, estimate_method = estimate_input_tokens(
                    msg_arg if isinstance(msg_arg, list) else None,
                    tool_arg if isinstance(tool_arg, list) else None,
                )
                reservation = self.model_ledger.reserve(
                    estimated_input_tokens=estimated_input,
                    max_output_tokens=max_output_tokens,
                    estimate_method=estimate_method,
                )
            if self.cny_ledger is not None:
                try:
                    cny_reservation = self.cny_ledger.reserve(
                        phase=self.cny_phase, operation_id=operation_id,
                        component=self.cny_component, prompt_tokens=estimated_input,
                        max_output_tokens=max_output_tokens,
                    )
                except Exception:
                    if reservation is not None and self.model_ledger is not None:
                        self.model_ledger.settle_unknown(reservation.reservation_id, reason="cny_budget")
                    raise
            if self.session:
                self.session.model_started(self.model_ledger, operation_id, attempt_id)

            started = time.monotonic()
            started_at = datetime.now(timezone.utc).isoformat()
            owner = getattr(fn, '__self__', None)
            client = getattr(owner, 'llm', owner)
            if strict and hasattr(client, 'last_provider_metadata'):
                client.last_provider_metadata = {}
            def audit(response=None):
                if not strict: return None
                data = getattr(response, 'provider_metadata', None) or getattr(client, 'last_provider_metadata', None)
                data = copy.deepcopy(data) if data else provider_metadata(getattr(client, 'model', None))
                data['started_at'] = data.get('started_at') or started_at
                data['finished_at'] = datetime.now(timezone.utc).isoformat()
                return data
            try:
                call = fn(*args, **kwargs)
                if self.deadline is not None:
                    result = await await_with_control(
                        call,
                        deadline=self.deadline,
                        cancel_event=self.deadline.cancel_event,
                    )
                else:
                    result = await call
            except (UserCancelled, DeadlineExceeded, asyncio.CancelledError) as e:
                elapsed_ms = (time.monotonic() - started) * 1000
                if reservation is not None and self.model_ledger is not None:
                    self.model_ledger.settle_unknown(
                        reservation.reservation_id, reason=type(e).__name__
                    )
                if cny_reservation is not None:
                    self.cny_ledger.settle(cny_reservation.reservation_id, None)
                if strict: self.billing_stop_reason = "billing_usage_unknown"
                self.sink.record(
                    AttemptRecord(
                        attempt=attempt,
                        success=False,
                        error_kind=type(e).__name__,
                        message=(type(e).__name__ if strict else str(e)[:500]),
                        elapsed_ms=elapsed_ms,
                        usage={"provenance": "unknown"},
                        usage_provenance="unknown",
                        reservation_id=reservation.reservation_id if reservation else None,
                        estimate_method=estimate_method,
                        estimated_input_tokens=estimated_input,
                        provider_metadata=audit(), billing_stop_reason=self.billing_stop_reason if strict else None,
                    )
                )
                raise
            except Exception as e:
                elapsed_ms = (time.monotonic() - started) * 1000
                last_error = e
                kind = None
                status_code = None
                if isinstance(e, LLMError):
                    kind = e.kind.value
                    status_code = e.status_code
                else:
                    kind = self._classify(e)
                if reservation is not None and self.model_ledger is not None:
                    self.model_ledger.settle_unknown(
                        reservation.reservation_id, reason=kind or "error"
                    )
                if cny_reservation is not None:
                    self.cny_ledger.settle(cny_reservation.reservation_id, None)
                if strict: self.billing_stop_reason = "billing_usage_unknown"
                self.sink.record(
                    AttemptRecord(
                        attempt=attempt,
                        success=False,
                        error_kind=kind,
                        status_code=status_code,
                        message=(type(e).__name__ if strict else str(e)[:500]),
                        elapsed_ms=elapsed_ms,
                        usage={"provenance": "unknown"},
                        usage_provenance="unknown",
                        reservation_id=reservation.reservation_id if reservation else None,
                        estimate_method=estimate_method,
                        estimated_input_tokens=estimated_input,
                        provider_metadata=audit(), billing_stop_reason=self.billing_stop_reason if strict else None,
                    )
                )
                if strict: raise PaidCallStopped(self.billing_stop_reason) from None
                if attempt == self.config.max_retries or not self.should_retry(e):
                    raise
                if self.deadline is not None:
                    self.deadline.raise_if_expired_or_cancelled()
                delay = self.config.backoff_base_seconds * (
                    self.config.backoff_factor ** attempt
                )
                if self.deadline is not None:
                    remaining = self.deadline.remaining_or_zero()
                    if remaining <= 0:
                        self.deadline.raise_if_expired_or_cancelled()
                    delay = min(delay, remaining)
                logger.warning(
                    f"Retry attempt {attempt + 1}/{self.config.max_retries} "
                    f"after error: {e}. Waiting {delay:.1f}s."
                )
                await self._sleep_interruptible(delay)
                continue

            elapsed_ms = (time.monotonic() - started) * 1000
            usage = None
            provenance = None
            if hasattr(result, "usage") and isinstance(result.usage, dict):
                usage = dict(result.usage)
                provenance = result.usage.get("provenance")
            stop_reason = None
            metadata = audit(result) if strict else None
            observed = (getattr(result, 'provider_metadata', None)
                        or getattr(client, 'last_provider_metadata', None) or {})
            raw_usage = observed.get('raw_usage')
            raw_incomplete = ('raw_usage' in observed and
                not complete_billing_usage(dict(raw_usage, provenance='provider')
                    if isinstance(raw_usage, dict) else None))
            if strict and (not complete_billing_usage(usage) or raw_incomplete or metadata.get('raw_usage_omitted')):
                stop_reason = "billing_usage_unknown"
            elif strict and ((getattr(result, 'provider_metadata', {}) or {}).get('finish_reason',
                    getattr(result, 'finish_reason', None))) not in ('stop', 'tool_calls'):
                stop_reason = "response_incomplete"
            if strict and stop_reason: self.billing_stop_reason = stop_reason
            if reservation is not None and self.model_ledger is not None:
                settlement = (self.model_ledger.settle_unknown(reservation.reservation_id, reason=stop_reason)
                    if stop_reason == 'billing_usage_unknown' else self.model_ledger.settle(reservation.reservation_id, usage))
                provenance = settlement.provenance
            if cny_reservation is not None:
                self.cny_ledger.settle(cny_reservation.reservation_id, usage)
            self.sink.record(
                AttemptRecord(
                    attempt=attempt,
                    success=stop_reason is None,
                    error_kind=stop_reason,
                    elapsed_ms=elapsed_ms,
                    usage=usage,
                    usage_provenance=provenance,
                    reservation_id=reservation.reservation_id if reservation else None,
                    estimate_method=estimate_method,
                    estimated_input_tokens=estimated_input,
                    provider_metadata=metadata, billing_stop_reason=stop_reason,
                )
            )
            if stop_reason: raise PaidCallStopped(stop_reason)
            return result

        raise last_error  # pragma: no cover

    async def _sleep_interruptible(self, delay: float) -> None:
        if delay <= 0:
            return
        end = time.monotonic() + delay
        while True:
            if self.deadline is not None:
                self.deadline.raise_if_expired_or_cancelled()
                rem = self.deadline.remaining_or_zero()
                left = min(end - time.monotonic(), rem)
            else:
                left = end - time.monotonic()
            if left <= 0:
                return
            await asyncio.sleep(min(0.05, left))

    @staticmethod
    def _classify(error: Exception) -> str:
        msg = str(error).lower()
        if "timeout" in msg or "timed out" in msg:
            return "timeout"
        if "network" in msg or "connection" in msg:
            return "network"
        if "model" in msg or "api" in msg or "parse" in msg:
            return "model_error"
        return "unknown"
