"""Rank combination: deterministic ordering, chunk-to-file aggregation, and
the two reference rankers (random, path mention)."""
from __future__ import annotations

import hashlib
import random
import re

import numpy as np

RANDOM_SEED = 20261018


def order_by_score(scores, keys: list) -> list[int]:
    """Indices sorted by score desc, ties broken by ``keys`` ascending."""
    scores = np.asarray(scores, np.float64)
    return sorted(range(len(keys)), key=lambda i: (-scores[i], keys[i]))


def max_by_file(chunk_scores, chunk_file, n_files: int) -> np.ndarray:
    """File score = max over its chunks. Files without chunks get -inf."""
    out = np.full(n_files, -np.inf)
    np.maximum.at(out, np.asarray(chunk_file, np.int64), np.asarray(chunk_scores, np.float64))
    return out


def ranks_from_order(order: list[int], n: int) -> np.ndarray:
    """1-based rank of every item given a full ordering of ``range(n)``."""
    ranks = np.empty(n, np.int64)
    ranks[np.asarray(order, np.int64)] = np.arange(1, n + 1)
    return ranks


def rrf(rank_arrays, weights, k: float) -> np.ndarray:
    """score = sum_i w_i / (k + rank_i), over full 1-based rank arrays."""
    out = np.zeros(len(rank_arrays[0]), np.float64)
    for ranks, w in zip(rank_arrays, weights):
        out += w / (k + np.asarray(ranks, np.float64))
    return out


def random_order(n: int, instance_id: str, salt: str = "") -> list[int]:
    seed = int(hashlib.sha256(f"{RANDOM_SEED}:{instance_id}:{salt}".encode()).hexdigest()[:16], 16)
    idx = list(range(n))
    random.Random(seed).shuffle(idx)
    return idx


_PATH_TOKEN = re.compile(r"[\w./\-]+\.py\b")
_DOTTED = re.compile(r"(?<![\w.])[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")
_LAYOUT_ROOTS = {"src", "lib"}


def module_names(path: str) -> list[str]:
    """Dotted module names a corpus path can be imported as."""
    if not path.endswith(".py"):
        return []
    parts = path[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    names = []
    if len(parts) >= 2:
        names.append(".".join(parts))
    if len(parts) >= 3 and parts[0] in _LAYOUT_ROOTS:
        names.append(".".join(parts[1:]))
    return names


def mentioned_files(text: str, paths: list[str]) -> set[int]:
    """Indices of corpus files named in ``text``: a path token equal to or
    ending in ``/<path>``, or the longest prefix (>= 2 parts) of a dotted name
    that maps to a module file."""
    norm = text.replace("\\", "/")
    by_path = {p: i for i, p in enumerate(paths)}
    by_module: dict[str, int] = {}
    for i, p in enumerate(paths):
        for name in module_names(p):
            by_module.setdefault(name, i)
    hits: set[int] = set()
    for tok in _PATH_TOKEN.findall(norm):
        tok = tok.lstrip("./")
        best = None
        segs = tok.split("/")
        for k in range(len(segs)):
            cand = "/".join(segs[k:])
            if cand in by_path:
                best = by_path[cand]
                break
        if best is not None:
            hits.add(best)
    for name in _DOTTED.findall(text):
        segs = name.split(".")
        for k in range(len(segs), 1, -1):
            idx = by_module.get(".".join(segs[:k]))
            if idx is not None:
                hits.add(idx)
                break
    return hits


def mention_first(order: list[int], mentioned: set[int]) -> list[int]:
    """Stable partition: mentioned items first, each part keeps ``order``."""
    return [i for i in order if i in mentioned] + [i for i in order if i not in mentioned]
