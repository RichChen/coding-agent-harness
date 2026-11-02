"""OpenAI-compatible SSE byte parser and stream aggregator for chat completions.

English: incremental SSE framing (cross-chunk lines) + content/tool aggregation by index.
Completion requires an explicit termination marker; raw EOF alone is incomplete.
"""

from __future__ import annotations

import enum
import json
import time
from dataclasses import dataclass, field
from typing import Any

from src.tools.validation import (
    DEFAULT_MAX_ARGUMENT_BYTES,
    parse_tool_arguments_json,
)


class StreamFailKind(str, enum.Enum):
    INCOMPLETE = "incomplete"
    PARSE = "parse"
    NETWORK = "network"
    TOO_LARGE = "too_large"


@dataclass
class StreamFail:
    kind: StreamFailKind
    message: str


@dataclass
class ToolCallAccumulator:
    """Accumulate one tool_call by stream index.

    Provider SSE often sends ``id``, ``function.name``, and ``function.arguments``
    as incremental string fragments (same semantics as OpenAI-compatible deltas).
    Fragments are concatenated in arrival order — never overwritten.
    """

    index: int
    id_parts: list[str] = field(default_factory=list)
    name_parts: list[str] = field(default_factory=list)
    arguments_parts: list[str] = field(default_factory=list)

    def append_id(self, fragment: str) -> None:
        self.id_parts.append(fragment)

    def append_name(self, fragment: str) -> None:
        self.name_parts.append(fragment)

    def append_arguments(self, fragment: str) -> None:
        self.arguments_parts.append(fragment)

    @property
    def id(self) -> str | None:
        if not self.id_parts:
            return None
        return "".join(self.id_parts)

    @property
    def name(self) -> str | None:
        if not self.name_parts:
            return None
        return "".join(self.name_parts)

    @property
    def raw_arguments(self) -> str:
        return "".join(self.arguments_parts)


@dataclass
class AggregatedStreamResult:
    text: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    first_chunk_monotonic: float | None = None
    complete_monotonic: float | None = None
    saw_done_marker: bool = False
    fail: StreamFail | None = None

    @property
    def is_complete(self) -> bool:
        if self.fail is not None:
            return False
        # Explicit termination: [DONE] and/or a finish_reason on a choice.
        return self.saw_done_marker or self.finish_reason is not None


class SSELineBuffer:
    """Accumulate network byte chunks into SSE logical lines (LF-terminated)."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        if not data:
            return []
        self._buf.extend(data)
        lines: list[bytes] = []
        while True:
            idx = self._buf.find(b"\n")
            if idx < 0:
                break
            raw = bytes(self._buf[:idx])
            del self._buf[: idx + 1]
            if raw.endswith(b"\r"):
                raw = raw[:-1]
            lines.append(raw)
        return lines

    def flush_remainder(self) -> bytes | None:
        if not self._buf:
            return None
        rem = bytes(self._buf)
        self._buf.clear()
        return rem


class OpenAIStreamAggregator:
    """Aggregate OpenAI-compatible chat.completion.chunk SSE into one result."""

    def __init__(self, *, max_tool_argument_bytes: int = DEFAULT_MAX_ARGUMENT_BYTES):
        self.max_tool_argument_bytes = max_tool_argument_bytes
        self._line_buf = SSELineBuffer()
        self._event_data_lines: list[str] = []
        self._text_parts: list[str] = []
        self._tools: dict[int, ToolCallAccumulator] = {}
        self._finish_reason: str | None = None
        self._usage: dict[str, Any] | None = None
        self._first_chunk_monotonic: float | None = None
        self._saw_done_marker = False
        self._fail: StreamFail | None = None
        self._started = False
        self.provider_fields = {}

    def _mark_first_chunk(self) -> None:
        if self._first_chunk_monotonic is None:
            self._first_chunk_monotonic = time.monotonic()

    def feed_bytes(self, data: bytes) -> list[dict[str, Any]]:
        """Feed raw network bytes; return observation dicts for this batch."""
        if self._fail is not None:
            return []
        observations: list[dict[str, Any]] = []
        try:
            for line in self._line_buf.feed(data):
                observations.extend(self._handle_sse_line(line))
        except UnicodeDecodeError as exc:
            self._fail = StreamFail(
                kind=StreamFailKind.PARSE,
                message=f"invalid UTF-8 in SSE stream: {exc}",
            )
        return observations

    def finish(self, *, network_error: str | None = None) -> AggregatedStreamResult:
        """Finalize after stream ends. EOF without termination → incomplete."""
        if network_error and self._fail is None:
            self._fail = StreamFail(kind=StreamFailKind.NETWORK, message=network_error)

        # Trailing unterminated line (rare); ignore empty remainder.
        rem = self._line_buf.flush_remainder()
        if rem and self._fail is None:
            try:
                self._handle_sse_line(rem)
            except UnicodeDecodeError as exc:
                self._fail = StreamFail(
                    kind=StreamFailKind.PARSE,
                    message=f"invalid UTF-8 in SSE stream: {exc}",
                )

        # Flush pending event without trailing blank line.
        if self._event_data_lines and self._fail is None:
            self._dispatch_event_data("\n".join(self._event_data_lines))
            self._event_data_lines.clear()

        complete_mono = time.monotonic()
        if self._fail is not None:
            return AggregatedStreamResult(
                text="".join(self._text_parts) or None,
                finish_reason=self._finish_reason,
                usage=self._usage,
                first_chunk_monotonic=self._first_chunk_monotonic,
                complete_monotonic=complete_mono,
                saw_done_marker=self._saw_done_marker,
                fail=self._fail,
            )

        if not (self._saw_done_marker or self._finish_reason is not None):
            return AggregatedStreamResult(
                text="".join(self._text_parts) or None,
                finish_reason=self._finish_reason,
                usage=self._usage,
                first_chunk_monotonic=self._first_chunk_monotonic,
                complete_monotonic=complete_mono,
                saw_done_marker=self._saw_done_marker,
                fail=StreamFail(
                    kind=StreamFailKind.INCOMPLETE,
                    message="stream ended without termination marker ([DONE] or finish_reason)",
                ),
            )

        tool_calls: list[dict[str, Any]] = []
        for index in sorted(self._tools):
            acc = self._tools[index]
            if not acc.id:
                return AggregatedStreamResult(
                    fail=StreamFail(
                        kind=StreamFailKind.PARSE,
                        message=f"tool call index={index} missing id after stream completed",
                    ),
                    first_chunk_monotonic=self._first_chunk_monotonic,
                    complete_monotonic=complete_mono,
                    saw_done_marker=self._saw_done_marker,
                    finish_reason=self._finish_reason,
                    usage=self._usage,
                )
            if not acc.name:
                return AggregatedStreamResult(
                    fail=StreamFail(
                        kind=StreamFailKind.PARSE,
                        message=f"tool call id={acc.id} missing function.name",
                    ),
                    first_chunk_monotonic=self._first_chunk_monotonic,
                    complete_monotonic=complete_mono,
                    saw_done_marker=self._saw_done_marker,
                    finish_reason=self._finish_reason,
                    usage=self._usage,
                )
            raw = acc.raw_arguments
            if len(raw.encode("utf-8")) > self.max_tool_argument_bytes:
                return AggregatedStreamResult(
                    fail=StreamFail(
                        kind=StreamFailKind.TOO_LARGE,
                        message=(
                            f"tool call id={acc.id} arguments exceed max size "
                            f"({len(raw.encode('utf-8'))} > {self.max_tool_argument_bytes})"
                        ),
                    ),
                    first_chunk_monotonic=self._first_chunk_monotonic,
                    complete_monotonic=complete_mono,
                    saw_done_marker=self._saw_done_marker,
                    finish_reason=self._finish_reason,
                    usage=self._usage,
                )
            parsed = parse_tool_arguments_json(
                call_id=acc.id,
                name=acc.name,
                raw_arguments=raw,
                max_argument_bytes=self.max_tool_argument_bytes,
            )
            tool_calls.append(
                {
                    "id": acc.id,
                    "name": acc.name,
                    "arguments": parsed.arguments,
                    "raw_arguments": parsed.raw,
                    "parse_error": parsed.error,
                }
            )

        return AggregatedStreamResult(
            text="".join(self._text_parts) or None,
            tool_calls=tool_calls,
            finish_reason=self._finish_reason or "stop",
            usage=self._usage,
            first_chunk_monotonic=self._first_chunk_monotonic,
            complete_monotonic=complete_mono,
            saw_done_marker=self._saw_done_marker,
            fail=None,
        )

    def _handle_sse_line(self, line: bytes) -> list[dict[str, Any]]:
        if self._fail is not None:
            return []
        text = line.decode("utf-8")
        # Comment / keep-alive
        if text.startswith(":") or text.startswith("event:"):
            return []
        if text == "":
            if not self._event_data_lines:
                return []
            payload = "\n".join(self._event_data_lines)
            self._event_data_lines.clear()
            return self._dispatch_event_data(payload)
        if text.startswith("data:"):
            self._event_data_lines.append(text[5:].lstrip())
            return []
        # Ignore other SSE fields (id:, retry:) for OpenAI chat streams.
        return []

    def _dispatch_event_data(self, payload: str) -> list[dict[str, Any]]:
        observations: list[dict[str, Any]] = []
        stripped = payload.strip()
        if not stripped:
            return observations
        if stripped == "[DONE]":
            self._saw_done_marker = True
            self._mark_first_chunk()
            return observations

        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError as exc:
            self._fail = StreamFail(
                kind=StreamFailKind.PARSE,
                message=f"invalid JSON in SSE data: {exc.msg}",
            )
            return observations

        if not isinstance(obj, dict):
            self._fail = StreamFail(
                kind=StreamFailKind.PARSE,
                message="SSE data JSON must be an object",
            )
            return observations

        for key in ('id', 'model', 'system_fingerprint', 'request_id'):
            if key in obj: self.provider_fields[key] = obj[key]
        usage = obj.get("usage")
        if "usage" in obj:
            self.provider_fields['usage'] = usage
        if isinstance(usage, dict) and usage:
            self._usage = usage

        choices = obj.get("choices") or []
        if not isinstance(choices, list):
            self._fail = StreamFail(
                kind=StreamFailKind.PARSE,
                message="SSE choices must be a list",
            )
            return observations

        for choice in choices:
            if not isinstance(choice, dict):
                continue
            fr = choice.get("finish_reason")
            if fr is not None:
                self._finish_reason = fr
                self.provider_fields['choices'] = [{'finish_reason': fr}]
            delta = choice.get("delta") or {}
            if not isinstance(delta, dict):
                continue

            content = delta.get("content")
            if isinstance(content, str) and content:
                self._mark_first_chunk()
                self._started = True
                self._text_parts.append(content)
                observations.append({"event": "message_delta", "text": content})

            tool_calls = delta.get("tool_calls") or []
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    obs = self._ingest_tool_delta(tc)
                    if obs is not None:
                        observations.append(obs)
                    if self._fail is not None:
                        return observations

        return observations

    def _ingest_tool_delta(self, tc: dict[str, Any]) -> dict[str, Any] | None:
        index = tc.get("index", 0)
        if not isinstance(index, int):
            self._fail = StreamFail(
                kind=StreamFailKind.PARSE,
                message=f"tool_call index must be int, got {type(index).__name__}",
            )
            return None

        acc = self._tools.get(index)
        if acc is None:
            acc = ToolCallAccumulator(index=index)
            self._tools[index] = acc

        call_id = tc.get("id")
        if isinstance(call_id, str) and call_id:
            acc.append_id(call_id)

        func = tc.get("function") or {}
        if isinstance(func, dict):
            name = func.get("name")
            if isinstance(name, str) and name:
                acc.append_name(name)
            args_frag = func.get("arguments")
            if isinstance(args_frag, str) and args_frag:
                # Bound check while aggregating to fail early on huge args.
                prospective = acc.raw_arguments + args_frag
                if len(prospective.encode("utf-8")) > self.max_tool_argument_bytes:
                    self._fail = StreamFail(
                        kind=StreamFailKind.TOO_LARGE,
                        message=(
                            f"tool arguments exceed max size while streaming "
                            f"(>{self.max_tool_argument_bytes} bytes)"
                        ),
                    )
                    return None
                acc.append_arguments(args_frag)

        self._mark_first_chunk()
        self._started = True
        return {
            "event": "tool_call_delta",
            "index": index,
            "id": acc.id,
            "name": acc.name,
            "arguments_fragment": func.get("arguments") if isinstance(func, dict) else None,
        }
