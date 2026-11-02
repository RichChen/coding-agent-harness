"""Code-aware tokenizer shared by BM25 corpus and queries."""
from __future__ import annotations

import re
from collections import Counter

TOKENIZER_VERSION = "1"

_IDENT = re.compile(r"\w+", re.UNICODE)
_CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+|[^\W\d_A-Za-z]+", re.UNICODE)


def _sub_parts(ident: str) -> list[str]:
    parts = []
    for piece in ident.split("_"):
        if piece:
            parts.extend(_CAMEL.findall(piece))
    return parts


def tokenize(text: str) -> list[str]:
    """Split on non-alphanumerics; split identifiers on snake_case and camelCase
    while keeping the full identifier; lowercase; drop length-1 tokens."""
    out: list[str] = []
    for ident in _IDENT.findall(text):
        full = ident.lower()
        parts = [p.lower() for p in _sub_parts(ident)]
        if len(full) > 1:
            out.append(full)
        if len(parts) > 1 or (parts and parts[0] != full):
            out.extend(p for p in parts if len(p) > 1)
    return out


def count_tokens(text: str) -> Counter:
    return Counter(tokenize(text))


class Vocab:
    """token <-> int id, append-only."""

    def __init__(self, tokens: list[str] | None = None):
        self.id_of: dict[str, int] = {}
        self.tokens: list[str] = []
        for tok in tokens or ():
            self.add(tok)

    def add(self, tok: str) -> int:
        idx = self.id_of.get(tok)
        if idx is None:
            idx = len(self.tokens)
            self.id_of[tok] = idx
            self.tokens.append(tok)
        return idx

    def lookup(self, tok: str) -> int | None:
        return self.id_of.get(tok)

    def __len__(self) -> int:
        return len(self.tokens)
