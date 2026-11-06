"""Layer-1 micro-compact：在保留最近 N 条 tool 消息原文的同时压缩旧 tool 内容。

English: OpenAI-format micro-compact — keeps recent K tool results intact,
replaces old tool content (>100 chars) with a short placeholder.

Does NOT summarise via LLM (that's Layer 2 auto-compact, not implemented here).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from src.runtime.hooks import HookContext, LoopHooks


def estimate_tokens(messages: list[dict]) -> int:
    """Rough token estimate: total message chars / 4 (no tokenizer dependency)."""
    total_chars = sum(len(str(m.get("content", "") or "")) for m in messages)
    for msg in messages:
        for tc in msg.get("tool_calls", []) or []:
            total_chars += len(str(tc.get("function", {}).get("arguments", "")))
    return max(1, total_chars // 4)

# Tools whose historical results should always be preserved in full
DEFAULT_PRESERVE_TOOLS = {"read_file"}
MODIFICATION_TOOLS = {"write_file", "search_replace", "edit_file"}


@dataclass(frozen=True)
class ContextStats:
    """Observable stats for one micro-compact pass."""

    message_count_before: int
    message_count_after: int
    chars_before: int
    chars_after: int
    tool_messages_before: int
    tool_messages_after: int
    compacted_tool_count: int
    preserved_tool_count: int
    keep_recent: int
    estimated_tokens_before: int = 0
    estimated_tokens_after: int = 0

    @property
    def chars_saved(self) -> int:
        return max(0, self.chars_before - self.chars_after)

    @property
    def did_compact(self) -> bool:
        return self.compacted_tool_count > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_count_before": self.message_count_before,
            "message_count_after": self.message_count_after,
            "chars_before": self.chars_before,
            "chars_after": self.chars_after,
            "chars_saved": self.chars_saved,
            "tool_messages_before": self.tool_messages_before,
            "tool_messages_after": self.tool_messages_after,
            "compacted_tool_count": self.compacted_tool_count,
            "preserved_tool_count": self.preserved_tool_count,
            "keep_recent": self.keep_recent,
            "did_compact": self.did_compact,
            "estimated_tokens_before": self.estimated_tokens_before,
            "estimated_tokens_after": self.estimated_tokens_after,
        }


def _message_chars(messages: list[dict]) -> int:
    return sum(len(str(m.get("content", "") or "")) for m in messages)


def _tool_name_for_call_id(messages: list[dict], call_id: str) -> str:
    """Walk backwards from the tool message to find the assistant message
    that issued this tool_call_id, and return the function name."""
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls", []):
                if tc.get("id") == call_id:
                    return tc.get("function", {}).get("name", "unknown")
    return "unknown"


def _tool_content_indicates_failure(content: str) -> bool:
    lowered = content.lower()
    return lowered.startswith("error:") or "failed" in lowered or "traceback" in lowered


# v1 matches the run_command trailer itself ("status=failed", "Error: nonzero_exit"),
# so a grep with no match counts as a failure. v2 needs failure content.
PROTECTION_RULES = ("v1", "v2")
_SUBSTANTIVE_FAILURE = re.compile(
    r"Traceback \(most recent call last\)"
    r"|\bAssertionError\b"
    r"|^E {3,}\S"
    r"|^=+ (?:FAILURES|ERRORS) =+$"
    r"|^(?:FAILED|ERROR)\b"
    r"|\b[1-9]\d* (?:failed|errors?)\b",
    re.MULTILINE,
)
# Over-counting only keeps an extra observation verbatim; missing an edit loses it.
_REDIRECT_TARGET = re.compile(r"(?<![-=<>])(?:&|\d+)?>>?(?![=>])\|?[ \t]*([^\s;|<>]*)")
_WRITING_COMMAND = re.compile(
    r"\btee\b|\bpatch\b|\btruncate\b|\bgit\s+(?:apply|am)\b"
    r"|\bsed\b[^;|&\n]*\s(?:-[a-zA-Z]*i|--in-place)"
    r"|\bperl\b[^;|&\n]*\s-[a-zA-Z]*i"
)


def _shows_substantive_failure(content: str) -> bool:
    return _SUBSTANTIVE_FAILURE.search(content) is not None


def _command_writes_files(command: str) -> bool:
    for target in _REDIRECT_TARGET.findall(command):
        if not target.startswith("&") and target not in ("/dev/null", "/dev/stdout", "/dev/stderr"):
            return True
    return _WRITING_COMMAND.search(command) is not None


def _command_text(messages: list[dict], call_id: str) -> str:
    args = _call_arguments(messages, call_id)
    return str(args.get("command") or " ".join(str(part) for part in (args.get("argv") or [])))


def _protected_tool_indices(
    messages: list[dict],
    tool_indices: list[int],
    *,
    keep_recent_modified: int = 2,
    rules: str = "v1",
) -> set[int]:
    """Return tool-message indices that should stay verbatim for safety.

    Non-tool messages are never compacted, so the original user task and any
    plan-bearing assistant text are already preserved. This helper protects
    the tool observations most likely to be needed after compaction: the latest
    failure and recent file modification results.
    """
    if rules not in PROTECTION_RULES:
        raise ValueError(f"unknown protection rules: {rules}")
    failed = _tool_content_indicates_failure if rules == "v1" else _shows_substantive_failure
    protected: set[int] = set()

    for idx in reversed(tool_indices):
        content = str(messages[idx].get("content", "") or "")
        if failed(content):
            protected.add(idx)
            break

    modified_kept = 0
    for idx in reversed(tool_indices):
        call_id = messages[idx].get("tool_call_id", "")
        tool_name = _tool_name_for_call_id(messages, call_id)
        modified = tool_name in MODIFICATION_TOOLS or (
            rules == "v2" and tool_name == "run_command" and _command_writes_files(_command_text(messages, call_id)))
        if not modified:
            continue
        protected.add(idx)
        modified_kept += 1
        if modified_kept >= keep_recent_modified:
            break

    return protected


def _call_arguments(messages: list[dict], call_id: str) -> dict:
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for call in msg.get("tool_calls") or []:
            if call.get("id") != call_id:
                continue
            raw = (call.get("function") or {}).get("arguments") or "{}"
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
    return {}


def _elide_tool_result(messages: list[dict], call_id: str, tool_name: str, content: str) -> str:
    args = _call_arguments(messages, call_id)
    lines = content.count("\n") + (1 if content else 0)
    if tool_name == "read_file":
        path = args.get("path", "")
        offset = args.get("offset")
        extra = f", offset={offset}" if offset else ""
        return f"[Elided result of read_file(path={path}{extra}) — {lines} lines; re-read if needed]"
    if tool_name == "run_command":
        command = args.get("command") or " ".join(str(part) for part in (args.get("argv") or []))
        command = " ".join(str(command).split())
        if len(command) > 80:
            command = command[:77] + "..."
        match = re.search(r"exit_code=(-?\d+|null)", content)
        exit_code = match.group(1) if match else "unknown"
        return f"[Elided result of run_command({command}) — exit {exit_code}, {lines} lines]"
    if tool_name == "search_code":
        pattern = str(args.get("pattern", ""))[:80]
        path = args.get("path", ".")
        return f"[Elided result of search_code(pattern={pattern}, path={path}) — {lines} lines; re-run if needed]"
    path = args.get("path")
    inside = f"path={path}" if path else ""
    return f"[Elided result of {tool_name}({inside}) — {lines} lines]"


def micro_compact_openai_messages_with_stats(
    messages: list[dict],
    *,
    keep_recent: int = 3,
    preserve_tools: set[str] | None = None,
    protection_rules: str = "v1",
) -> tuple[list[dict], ContextStats]:
    """Compress old tool messages in-place and return observable stats.

    Rules:
    - The most recent *keep_recent* tool messages are always kept verbatim.
    - Tool messages for tools in *preserve_tools* are always kept verbatim.
    - Other tool messages with content > 100 chars are replaced with an
      elided line that keeps the tool name, key arguments, and line count.
    - Non-tool messages are never touched.
    """
    if preserve_tools is None:
        preserve_tools = DEFAULT_PRESERVE_TOOLS

    chars_before = _message_chars(messages)
    tokens_before = estimate_tokens(messages)

    # Identify indices of all tool messages
    tool_indices = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    tool_count = len(tool_indices)
    compacted_tool_count = 0
    preserved_tool_count = 0

    if len(tool_indices) <= keep_recent:
        stats = ContextStats(
            message_count_before=len(messages),
            message_count_after=len(messages),
            chars_before=chars_before,
            chars_after=_message_chars(messages),
            tool_messages_before=tool_count,
            tool_messages_after=tool_count,
            compacted_tool_count=0,
            preserved_tool_count=tool_count,
            keep_recent=keep_recent,
            estimated_tokens_before=tokens_before,
            estimated_tokens_after=estimate_tokens(messages),
        )
        return messages, stats

    # The last `keep_recent` tool messages are safe
    safe_indices = set(tool_indices[-keep_recent:]) if keep_recent > 0 else set()
    protected_indices = _protected_tool_indices(messages, tool_indices, rules=protection_rules)

    for idx in tool_indices:
        if idx in safe_indices or idx in protected_indices:
            preserved_tool_count += 1
            continue

        msg = messages[idx]
        call_id = msg.get("tool_call_id", "")
        tool_name = _tool_name_for_call_id(messages, call_id)

        if tool_name in preserve_tools:
            preserved_tool_count += 1
            continue

        content = msg.get("content", "")
        if len(content) > 100:
            messages[idx] = {
                **msg,
                "content": _elide_tool_result(messages, call_id, tool_name, content),
            }
            compacted_tool_count += 1
        else:
            preserved_tool_count += 1

    stats = ContextStats(
        message_count_before=len(messages),
        message_count_after=len(messages),
        chars_before=chars_before,
        chars_after=_message_chars(messages),
        tool_messages_before=tool_count,
        tool_messages_after=sum(1 for m in messages if m.get("role") == "tool"),
        compacted_tool_count=compacted_tool_count,
        preserved_tool_count=preserved_tool_count,
        keep_recent=keep_recent,
        estimated_tokens_before=tokens_before,
        estimated_tokens_after=estimate_tokens(messages),
    )
    return messages, stats


def micro_compact_openai_messages(
    messages: list[dict],
    *,
    keep_recent: int = 3,
    preserve_tools: set[str] | None = None,
) -> list[dict]:
    """Compress old tool messages in-place (returns the same list, mutated)."""
    compacted, _stats = micro_compact_openai_messages_with_stats(
        messages,
        keep_recent=keep_recent,
        preserve_tools=preserve_tools,
    )
    return compacted


class MicroCompactHooks(LoopHooks):
    """Hook that applies micro-compact before each LLM call."""

    def __init__(
        self,
        keep_recent: int = 3,
        preserve_tools: set[str] | None = None,
        token_threshold: int | None = None,
        protection_rules: str = "v1",
    ):
        if protection_rules not in PROTECTION_RULES:
            raise ValueError(f"unknown protection rules: {protection_rules}")
        self.keep_recent = keep_recent
        self.preserve_tools = preserve_tools
        self.token_threshold = token_threshold
        self.protection_rules = protection_rules

    async def before_llm(self, messages: list[dict], ctx: HookContext) -> list[dict]:
        tokens_before = estimate_tokens(messages)
        if self.token_threshold is not None and tokens_before <= self.token_threshold:
            ctx.extra["context_stats"] = ContextStats(
                message_count_before=len(messages),
                message_count_after=len(messages),
                chars_before=_message_chars(messages),
                chars_after=_message_chars(messages),
                tool_messages_before=sum(1 for m in messages if m.get("role") == "tool"),
                tool_messages_after=sum(1 for m in messages if m.get("role") == "tool"),
                compacted_tool_count=0,
                preserved_tool_count=sum(1 for m in messages if m.get("role") == "tool"),
                keep_recent=self.keep_recent,
                estimated_tokens_before=tokens_before,
                estimated_tokens_after=tokens_before,
            ).to_dict()
            return messages

        compacted, stats = micro_compact_openai_messages_with_stats(
            messages,
            keep_recent=self.keep_recent,
            preserve_tools=self.preserve_tools,
            protection_rules=self.protection_rules,
        )
        ctx.extra["context_stats"] = stats.to_dict()
        return compacted
