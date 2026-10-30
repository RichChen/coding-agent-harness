"""LLMClient：对 OpenAI 兼容 `/v1/chat/completions` 的薄封装。

English: async HTTP client with honest non-streaming `chat()` and real SSE `stream_chat()`,
reusable connection pool, owning/borrowed lifecycle, and usage provenance.
"""

from __future__ import annotations

import enum
import copy
import math
from datetime import datetime, timezone
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol

import httpx

# CancelledError is BaseException — must not be swallowed as a stream error.
from asyncio import CancelledError as _CancelledError

from src.llm.stream import OpenAIStreamAggregator, StreamFailKind
from src.tools.validation import (
    DEFAULT_MAX_ARGUMENT_BYTES,
    ToolValidationFailure,
    parse_tool_arguments_json,
)

logger = logging.getLogger(__name__)


class StreamCapability(str, enum.Enum):
    STREAMING = "streaming"
    NON_STREAMING = "non_streaming"


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] | None = None
    raw_arguments: str = ""
    parse_error: ToolValidationFailure | None = None


@dataclass
class LLMResponse:
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = "stop"
    usage: dict[str, Any] = field(default_factory=dict)
    capability: StreamCapability | None = None
    first_chunk_monotonic: float | None = None
    complete_monotonic: float | None = None
    provider_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0

    @property
    def usage_provenance(self) -> str:
        return str(self.usage.get("provenance", "unknown"))


class LLMErrorKind(str, enum.Enum):
    TIMEOUT = "timeout"
    HTTP = "http"
    PARSE = "parse"
    INCOMPLETE = "incomplete"
    NETWORK = "network"
    UNKNOWN = "unknown"


class LLMError(Exception):
    """Raised when the LLM API returns an error or times out."""

    def __init__(
        self,
        message: str,
        kind: LLMErrorKind = LLMErrorKind.UNKNOWN,
        status_code: int | None = None,
        provider_metadata: dict | None = None,
    ):
        self.provider_metadata = provider_metadata or {}
        self.kind = kind
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True)
class LLMStreamEvent:
    event: str
    data: dict[str, Any] = field(default_factory=dict)


class BaseLLMClient(Protocol):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        ...


def normalize_usage(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Map provider usage to a provenance-aware dict. Missing → unknown, never invent 0."""
    if not isinstance(raw, dict) or not raw:
        return {
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "provenance": "unknown",
        }

    def _opt_int(key: str) -> int | None:
        val = raw.get(key)
        if val is None:
            return None
        if isinstance(val, bool):
            return None
        if isinstance(val, int):
            return val
        if isinstance(val, float) and val.is_integer():
            return int(val)
        return None

    prompt = _opt_int("prompt_tokens")
    completion = _opt_int("completion_tokens")
    total = _opt_int("total_tokens")
    # Only mark provider-reported when at least one token field is present.
    if prompt is None and completion is None and total is None:
        return {
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "provenance": "unknown",
        }
    normalized = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "provenance": "provider",
    }
    # Keep documented billing counters without retaining arbitrary provider data.
    for key in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
        value = _opt_int(key)
        if value is not None and value >= 0:
            normalized[key] = value
    details = raw.get("completion_tokens_details")
    if isinstance(details, dict):
        reasoning = details.get("reasoning_tokens")
        if type(reasoning) is int and reasoning >= 0:
            normalized["completion_tokens_details"] = {"reasoning_tokens": reasoning}
    return normalized


def _audit_copy(value):
    """Copy usage only, excluding credential-shaped fields; never capture headers/body."""
    if isinstance(value, dict):
        return {str(k): ("[REDACTED]" if any(x in str(k).lower() for x in
                ("api_key", "apikey", "authorization", "credential", "password", "secret", "access_token", "refresh_token", "headers")) else _audit_copy(v))
                for k, v in value.items()}
    if isinstance(value, list): return [_audit_copy(v) for v in value]
    if type(value) is float and not math.isfinite(value): return "[nonfinite]"
    if value is None or type(value) in (str, int, float, bool): return value
    return "[unsupported]"


def provider_metadata(request_model, data=None, *, headers=None, started_at=None):
    """A small whitelist. Missing provider fields stay null, never use requested model as returned model."""
    data = data if isinstance(data, dict) else {}
    def identifier(v):
        if not isinstance(v, str) or len(v) > 256: return None
        return v if all(c.isascii() and (c.isalnum() or c in '._:/-') for c in v) else None
    header_id = None
    if headers:
        header_id = headers.get('x-request-id') or headers.get('request-id')
    choices = data.get('choices') or []
    finish = choices[0].get('finish_reason') if choices and isinstance(choices[0], dict) else None
    raw_usage = _audit_copy(data.get('usage'))
    import json, hashlib
    raw_bytes = json.dumps(raw_usage, ensure_ascii=False).encode('utf-8')
    omitted = len(raw_bytes) > 65536
    return dict(request_model=identifier(request_model), response_model=identifier(data.get('model')),
        system_fingerprint=identifier(data.get('system_fingerprint')), response_id=identifier(data.get('id')),
        provider_request_id=identifier(header_id or data.get('request_id')),
        started_at=started_at, finished_at=datetime.now(timezone.utc).isoformat(),
        raw_usage=None if omitted else raw_usage, finish_reason=finish,
        raw_usage_omitted='size_limit' if omitted else None, raw_usage_sha256=hashlib.sha256(raw_bytes).hexdigest())


@dataclass(frozen=True)
class OpenAICompatibleAdapter:
    """Provider adapter for OpenAI-compatible chat completions."""

    base_url: str

    @property
    def chat_completions_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"

    def build_payload(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        temperature: float,
        max_tokens: int,
        stream: bool = False,
        stream_options: dict[str, Any] | None = None,
        thinking: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if stream and stream_options:
            payload["stream_options"] = stream_options
        if thinking is not None:
            if thinking != "disabled":
                raise ValueError("only thinking='disabled' is supported; reasoning replay is not implemented")
            payload["thinking"] = {"type": thinking}
        return payload

    def headers(self, api_key: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }


class LLMClient:
    """异步调用 OpenAI 兼容的 chat completions（URL = base_url + "/chat/completions"）。

    Lifecycle:
      - Owns an httpx.AsyncClient by default (lazy-created, bounded pool).
      - Injected ``http_client`` is borrowed unless ``owns_http_client=True``.
      - ``aclose()`` / async context manager are idempotent; borrowed clients are not closed.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        timeout_seconds: float = 120.0,
        max_tool_argument_bytes: int = DEFAULT_MAX_ARGUMENT_BYTES,
        *,
        supports_streaming: bool = True,
        http_client: httpx.AsyncClient | None = None,
        owns_http_client: bool | None = None,
        max_connections: int = 10,
        max_keepalive_connections: int = 5,
        trust_env: bool = True,
        thinking: str | None = None,
        audit_metadata: bool = False,
    ):
        self.audit_metadata = audit_metadata
        self.last_provider_metadata = {}
        if thinking not in (None, "disabled"):
            raise ValueError("only thinking='disabled' is supported; reasoning replay is not implemented")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        self.max_tool_argument_bytes = max_tool_argument_bytes
        self.supports_streaming = supports_streaming
        self.trust_env = trust_env
        self.thinking = thinking
        self.adapter = OpenAICompatibleAdapter(base_url=self.base_url)
        self._max_connections = max_connections
        self._max_keepalive_connections = max_keepalive_connections
        self._closed = False
        self._active_response: httpx.Response | None = None

        if http_client is not None:
            self._http: httpx.AsyncClient | None = http_client
            self._owns_http_client = False if owns_http_client is None else owns_http_client
        else:
            self._http = None
            self._owns_http_client = True if owns_http_client is None else owns_http_client

    @property
    def capability(self) -> StreamCapability:
        if self.supports_streaming:
            return StreamCapability.STREAMING
        return StreamCapability.NON_STREAMING

    @property
    def http_client(self) -> httpx.AsyncClient | None:
        return self._http

    @property
    def owns_http_client(self) -> bool:
        return self._owns_http_client

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._closed and self._owns_http_client and self._http is None:
            # Allow reopen after aclose for owned clients (new run).
            self._closed = False
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_seconds),
                limits=httpx.Limits(
                    max_connections=self._max_connections,
                    max_keepalive_connections=self._max_keepalive_connections,
                ),
                trust_env=self.trust_env,
            )
        return self._http

    async def _close_active_response(self) -> None:
        resp = self._active_response
        self._active_response = None
        if resp is None:
            return
        try:
            await resp.aclose()
        except Exception:
            logger.debug("error closing active stream response", exc_info=True)

    async def aclose(self) -> None:
        """Idempotent close of owned client and any active stream response."""
        await self._close_active_response()

        if self._owns_http_client and self._http is not None:
            client = self._http
            self._http = None
            try:
                await client.aclose()
            except Exception:
                logger.debug("error closing httpx client", exc_info=True)
        self._closed = True

    async def __aenter__(self) -> LLMClient:
        await self._ensure_client()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """Honest non-streaming chat completions path (stream=false)."""
        payload = self.adapter.build_payload(
            model=self.model,
            messages=messages,
            tools=tools,
            temperature=self.temperature if temperature is None else temperature,
            max_tokens=self.max_tokens if max_tokens is None else max_tokens,
            stream=False,
            thinking=self.thinking,
        )
        payload.pop("stream", None)

        started_at = datetime.now(timezone.utc).isoformat()
        if self.audit_metadata:
            self.last_provider_metadata = provider_metadata(self.model, started_at=started_at)
        client = await self._ensure_client()
        try:
            resp = await client.post(
                self.adapter.chat_completions_url,
                json=payload,
                headers=self.adapter.headers(self.api_key),
            )
            if self.audit_metadata:
                self.last_provider_metadata = provider_metadata(self.model, headers=resp.headers, started_at=started_at)
            resp.raise_for_status()
        except httpx.TimeoutException:
            raise LLMError(
                f"LLM request timed out after {self.timeout_seconds}s",
                kind=LLMErrorKind.TIMEOUT,
            )
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500] if e.response is not None else "(no body)"
            status_code = e.response.status_code if e.response is not None else None
            raise LLMError(
                f"LLM API error {status_code}: {body}",
                kind=LLMErrorKind.HTTP,
                status_code=status_code,
            )
        except httpx.HTTPError as e:
            raise LLMError(f"LLM network error: {e}", kind=LLMErrorKind.NETWORK)

        try:
            data = resp.json()
        except Exception as e:
            raise LLMError(f"Unable to decode LLM JSON body: {e}", kind=LLMErrorKind.PARSE)

        if self.audit_metadata:
            self.last_provider_metadata = provider_metadata(self.model, data, headers=resp.headers, started_at=started_at)
        response = self._parse_response(data)
        if self.audit_metadata:
            response.provider_metadata = copy.deepcopy(self.last_provider_metadata)
        response.capability = StreamCapability.NON_STREAMING
        return response

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[LLMStreamEvent]:
        """Real SSE streaming when capability=streaming; otherwise honest non-stream fallback.

        Yields observation events (message_delta / tool_call_delta) and a terminal
        ``llm_stream_done`` carrying one complete ``LLMResponse`` (HP-01 argument parse
        already applied). On failure yields ``llm_error`` and returns.
        """
        if not self.supports_streaming:
            # Honest non-streaming path — no fake chunked deltas / no TTFT.
            try:
                response = await self.chat(messages, tools)
            except LLMError as e:
                yield LLMStreamEvent(
                    "llm_error",
                    {
                        "kind": e.kind.value,
                        "message": type(e).__name__ if self.audit_metadata else str(e),
                        "status_code": e.status_code,
                        "capability": StreamCapability.NON_STREAMING.value,
                    },
                )
                return
            response.capability = StreamCapability.NON_STREAMING
            response.first_chunk_monotonic = None
            yield LLMStreamEvent(
                "llm_stream_done",
                {
                    "finish_reason": response.finish_reason,
                    "usage": response.usage,
                    "has_tool_calls": response.has_tool_calls,
                    "capability": StreamCapability.NON_STREAMING.value,
                    "response": response,
                    "first_chunk_monotonic": None,
                    "complete_monotonic": response.complete_monotonic,
                },
            )
            return

        payload = self.adapter.build_payload(
            model=self.model,
            messages=messages,
            tools=tools,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            stream=True,
            stream_options={"include_usage": True},
            thinking=self.thinking,
        )
        client = await self._ensure_client()
        aggregator = OpenAIStreamAggregator(
            max_tool_argument_bytes=self.max_tool_argument_bytes
        )
        started_at = datetime.now(timezone.utc).isoformat()
        if self.audit_metadata:
            self.last_provider_metadata = provider_metadata(self.model, started_at=started_at)
        response_cm = None
        try:
            request = client.build_request(
                "POST",
                self.adapter.chat_completions_url,
                json=payload,
                headers=self.adapter.headers(self.api_key),
            )
            response_cm = await client.send(request, stream=True)
            self._active_response = response_cm
            if self.audit_metadata:
                self.last_provider_metadata = provider_metadata(self.model, headers=response_cm.headers, started_at=started_at)
            try:
                response_cm.raise_for_status()
            except httpx.HTTPStatusError as e:
                body = (await response_cm.aread())[:500].decode("utf-8", errors="replace")
                status_code = e.response.status_code if e.response is not None else None
                raise LLMError(
                    f"LLM API error {status_code}: {body}",
                    kind=LLMErrorKind.HTTP,
                    status_code=status_code,
                )

            async for chunk in response_cm.aiter_bytes():
                observations = aggregator.feed_bytes(chunk)
                if self.audit_metadata:
                    self.last_provider_metadata = provider_metadata(self.model, aggregator.provider_fields,
                        headers=response_cm.headers, started_at=started_at)
                for obs in observations:
                    yield LLMStreamEvent(obs["event"], {k: v for k, v in obs.items() if k != "event"})

            result = aggregator.finish()
            if self.audit_metadata:
                self.last_provider_metadata = provider_metadata(self.model, aggregator.provider_fields,
                    headers=response_cm.headers, started_at=started_at)
        except _CancelledError:
            # Ensure active response is cleared via finally, then propagate cancellation.
            raise
        except LLMError as e:
            yield LLMStreamEvent(
                "llm_error",
                {
                    "kind": e.kind.value,
                    "message": type(e).__name__ if self.audit_metadata else str(e),
                    "status_code": e.status_code,
                    "capability": StreamCapability.STREAMING.value,
                },
            )
            return
        except httpx.TimeoutException:
            yield LLMStreamEvent(
                "llm_error",
                {
                    "kind": LLMErrorKind.TIMEOUT.value,
                    "message": f"LLM stream timed out after {self.timeout_seconds}s",
                    "capability": StreamCapability.STREAMING.value,
                },
            )
            return
        except httpx.HTTPError as e:
            yield LLMStreamEvent(
                "llm_error",
                {
                    "kind": LLMErrorKind.NETWORK.value,
                    "message": "network" if self.audit_metadata else f"LLM network error: {e}",
                    "capability": StreamCapability.STREAMING.value,
                },
            )
            return
        except Exception as e:
            yield LLMStreamEvent(
                "llm_error",
                {
                    "kind": LLMErrorKind.UNKNOWN.value,
                    "message": type(e).__name__ if self.audit_metadata else str(e),
                    "capability": StreamCapability.STREAMING.value,
                },
            )
            return
        finally:
            if self._active_response is response_cm:
                self._active_response = None
            if response_cm is not None:
                try:
                    await response_cm.aclose()
                except Exception:
                    logger.debug("error closing stream response", exc_info=True)

        if result.fail is not None:
            kind_map = {
                StreamFailKind.INCOMPLETE: LLMErrorKind.INCOMPLETE,
                StreamFailKind.PARSE: LLMErrorKind.PARSE,
                StreamFailKind.NETWORK: LLMErrorKind.NETWORK,
                StreamFailKind.TOO_LARGE: LLMErrorKind.PARSE,
            }
            yield LLMStreamEvent(
                "llm_error",
                {
                    "kind": kind_map[result.fail.kind].value,
                    "message": result.fail.kind.value if self.audit_metadata else result.fail.message,
                    "capability": StreamCapability.STREAMING.value,
                    "first_chunk_monotonic": result.first_chunk_monotonic,
                    "complete_monotonic": result.complete_monotonic,
                },
            )
            return

        tool_calls = [
            ToolCall(
                id=tc["id"],
                name=tc["name"],
                arguments=tc["arguments"],
                raw_arguments=tc.get("raw_arguments", ""),
                parse_error=tc.get("parse_error"),
            )
            for tc in result.tool_calls
        ]
        response = LLMResponse(
            text=result.text,
            tool_calls=tool_calls,
            finish_reason=result.finish_reason or "stop",
            usage=normalize_usage(result.usage),
            capability=StreamCapability.STREAMING,
            first_chunk_monotonic=result.first_chunk_monotonic,
            complete_monotonic=result.complete_monotonic,
            provider_metadata=copy.deepcopy(self.last_provider_metadata) if self.audit_metadata else {},
        )
        yield LLMStreamEvent(
            "llm_stream_done",
            {
                "finish_reason": response.finish_reason,
                "usage": response.usage,
                "has_tool_calls": response.has_tool_calls,
                "capability": StreamCapability.STREAMING.value,
                "response": response,
                "first_chunk_monotonic": response.first_chunk_monotonic,
                "complete_monotonic": response.complete_monotonic,
            },
        )

    def _parse_response(self, data: dict[str, Any]) -> LLMResponse:
        """Strict non-streaming response parse.

        Tool-call arguments are checked for size, JSON validity, and object
        shape before becoming ``ToolCall.arguments``. Illegal JSON is never
        silently coerced to ``{}``; failures are attached as ``parse_error``.
        Missing id/function/name raise ``LLMError(PARSE)`` for the response.
        """
        try:
            choice = data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"Unable to parse LLM response: {e}", kind=LLMErrorKind.PARSE)

        text = message.get("content") or None

        tool_calls: list[ToolCall] = []
        raw_tool_calls = message.get("tool_calls") or []
        if not isinstance(raw_tool_calls, list):
            raise LLMError(
                "Unable to parse LLM response: tool_calls must be a list",
                kind=LLMErrorKind.PARSE,
            )

        for index, tc in enumerate(raw_tool_calls):
            if not isinstance(tc, dict):
                raise LLMError(
                    f"Unable to parse LLM response: tool_calls[{index}] is not an object",
                    kind=LLMErrorKind.PARSE,
                )
            call_id = tc.get("id")
            if not isinstance(call_id, str) or not call_id:
                raise LLMError(
                    f"Unable to parse LLM response: tool_calls[{index}] missing id",
                    kind=LLMErrorKind.PARSE,
                )
            func = tc.get("function")
            if not isinstance(func, dict):
                raise LLMError(
                    f"Unable to parse LLM response: tool_calls[{index}] missing function",
                    kind=LLMErrorKind.PARSE,
                )
            name = func.get("name")
            if not isinstance(name, str) or not name:
                raise LLMError(
                    f"Unable to parse LLM response: tool_calls[{index}] missing function.name",
                    kind=LLMErrorKind.PARSE,
                )

            parsed = parse_tool_arguments_json(
                call_id=call_id,
                name=name,
                raw_arguments=func.get("arguments"),
                max_argument_bytes=self.max_tool_argument_bytes,
            )
            tool_calls.append(
                ToolCall(
                    id=call_id,
                    name=name,
                    arguments=parsed.arguments,
                    raw_arguments=parsed.raw,
                    parse_error=parsed.error,
                )
            )

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason"),
            usage=normalize_usage(data.get("usage") if isinstance(data.get("usage"), dict) else None),
            capability=StreamCapability.NON_STREAMING,
            provider_metadata=provider_metadata(self.model, data) if self.audit_metadata else {},
        )
