"""Opt-in per-request telemetry (tracing.request_telemetry).

request_context goes on context.prepared: which raw messages the model view replaced, and the admission estimate.
settled_telemetry goes on model.settled: that estimate next to the provider's prompt_tokens, cache hits and
reasoning tokens. Without the option neither event changes.
"""
from __future__ import annotations

import json
from collections import Counter


def _key(message) -> str:
    return json.dumps(message, sort_keys=True, ensure_ascii=False, default=str)


def _tool_call_id(message: dict) -> str | None:
    if message.get("tool_call_id"):
        return message["tool_call_id"]
    calls = message.get("tool_calls") or []
    return calls[0].get("id") if len(calls) == 1 and isinstance(calls[0], dict) else None


def _masked(stats: dict) -> bool:
    if stats.get("actually_injected") in ("masking", "mask-then-summary"):
        return True
    if (stats.get("masking") or {}).get("did_compact"):
        return True
    return bool(stats.get("did_compact")) and stats.get("strategy") == "legacy-hook"


def request_context(raw: list[dict], view: list[dict], stats: dict) -> dict:
    """A raw message is replaced when no identical message is left in the view (matched as a multiset)."""
    summary_ranges = [(r["source_start"], r["source_end"]) for r in stats.get("summary_records") or []]
    left = Counter(_key(message) for message in view)
    replaced = []
    for index, message in enumerate(raw):
        key = _key(message)
        if left[key]:
            left[key] -= 1
            continue
        kind = "summarized" if any(start <= index < end for start, end in summary_ranges) else "masked"
        replaced.append({"index": index, "role": message.get("role"), "tool_call_id": _tool_call_id(message),
                         "kind": kind})
    return {
        "step": stats.get("step"),
        "admission_input_tokens": stats.get("admission_input_tokens"),
        "admission_method": stats.get("admission_method"),
        "masked": _masked(stats),
        "summarized": bool(summary_ranges),
        "message_count_raw": len(raw),
        "message_count_view": len(view),
        "replaced": replaced,
    }


def _count(value):
    return value if type(value) is int and value >= 0 else None


def settled_telemetry(context: dict, usage: dict | None) -> dict:
    usage = usage if isinstance(usage, dict) else {}
    admission, prompt = _count(context.get("admission_input_tokens")), _count(usage.get("prompt_tokens"))
    details = usage.get("completion_tokens_details")
    return {
        "step": context.get("step"),
        "admission_input_tokens": admission,
        "admission_method": context.get("admission_method"),
        "prompt_tokens": prompt,
        "admission_minus_prompt": admission - prompt if admission is not None and prompt is not None else None,
        "admission_over_prompt": round(admission / prompt, 4) if admission is not None and prompt else None,
        "prompt_cache_hit_tokens": _count(usage.get("prompt_cache_hit_tokens")),
        "prompt_cache_miss_tokens": _count(usage.get("prompt_cache_miss_tokens")),
        "completion_tokens": _count(usage.get("completion_tokens")),
        "reasoning_tokens": _count(details.get("reasoning_tokens")) if isinstance(details, dict) else None,
        "masked": bool(context.get("masked")),
        "summarized": bool(context.get("summarized")),
        "replaced_count": len(context.get("replaced") or []),
    }
