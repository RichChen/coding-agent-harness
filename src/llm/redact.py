"""Strip credentials from provider error text before it is stored or logged."""

from __future__ import annotations

import re
from typing import Iterable

REDACTED = "[redacted]"

_PATTERNS = (
    # Authorization: Bearer <token> / Authorization=<token>, header or JSON-quoted
    (re.compile(r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?)(bearer\s+|basic\s+)?[^\s\"',;}]+"),
     lambda m: m.group(1) + (m.group(2) or "") + REDACTED),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), lambda m: m.group(1) + REDACTED),
    # api_key=..., "x-api-key": "...", access_token / secret
    (re.compile(r"(?i)\b((?:x-)?api[_-]?key|access[_-]?token|secret)([\"']?\s*[:=]\s*[\"']?)[^\s\"',;&}]+"),
     lambda m: m.group(1) + m.group(2) + REDACTED),
    # OpenAI-style keys (sk-..., sk-proj-...)
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), lambda m: REDACTED),
)


def redact_secrets(text: str, secrets: Iterable[str | None] = ()) -> str:
    for secret in secrets:
        if isinstance(secret, str) and secret:
            text = text.replace(secret, REDACTED)
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text
