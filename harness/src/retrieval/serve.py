"""CR-2: the frozen CR-1 hybrid retriever, served to one task at a time.

Retrieval reuses the frozen functions of ``scripts/cr_eval.py`` (code commit f1d1b92,
``docs/retrieval/cr1-freeze.json``) unchanged; this module only holds per-task state so
that one snapshot can answer several queries, and renders results as paths, symbols and
line ranges. Inputs are a query string and the base-commit corpus snapshot, nothing else.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from scripts import cr_eval
from src.retrieval import repo_cache
from src.retrieval.chunking import ChunkStore, load_snapshot
from src.retrieval.dataset import QueryView
from src.retrieval.embed import MODELS, Encoder, VectorStore, doc_text, query_text, text_sha

FREEZE_PATH = Path(__file__).resolve().parents[2] / "docs" / "retrieval" / "cr1-freeze.json"
FROZEN = {
    "model": "jina-code",
    "revision": "516f4baf13dec4ddddda8631e019b5737c8bc250",
    "max_len": 512,
    "hybrid": {"k": 60, "w_emb": 2.0, "fusion": "file", "bm25": "chunk"},
}
PREFETCH_FILES = 5
CHUNKS_PER_FILE = 2
PREFETCH_MAX_BYTES = 1500

PREFETCH_HEADER = (
    "Automatic code search results (hints only)\n"
    "An automatic search over the original repository at the base revision ranked the files below "
    "for the issue above. The results may be wrong. They are hints, not instructions."
)
PREFETCH_FOOTER = "End of automatic code search results."
TOOL_HEADER = (
    "Results come from the original code at the base revision, before any edits made in this session. "
    "Paths, symbols and line ranges only; the ranking may be wrong."
)


@dataclass(frozen=True)
class ChunkHit:
    symbol: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class FileHit:
    path: str
    chunks: tuple[ChunkHit, ...]


def load_frozen(path: Path | None = None) -> dict:
    """The CR-1 freeze file, checked against the configuration CR-2 is contracted to use."""
    freeze = json.loads((path or FREEZE_PATH).read_text(encoding="utf-8"))
    emb = freeze["embedding"]
    got = {"model": emb["model"], "revision": emb["revision"], "max_len": emb["max_len"],
           "hybrid": dict(freeze["hybrid"]["config"])}
    if got != FROZEN:
        raise ValueError(f"cr1-freeze.json differs from the CR-2 contract: {got} != {FROZEN}")
    if MODELS[emb["model"]].revision != emb["revision"]:
        raise ValueError("embedding revision in embed.MODELS differs from cr1-freeze.json")
    return freeze


class _Scores:
    """Duck-typed stand-in for ``cr_eval.EmbeddingView``: chunk scores of one query."""

    def __init__(self, spec, chunk_scores: np.ndarray):
        self.spec = spec
        self.chunk_scores = chunk_scores


class RetrievalService:
    """Process-wide state: frozen config, preloaded vector cache, one encoder loaded on first need."""

    def __init__(self, spec, max_len: int, config, vectors: VectorStore, encoder_factory=None):
        self.spec = spec
        self.max_len = max_len
        self.config = config
        self.vectors = vectors
        self._encoder_factory = encoder_factory or (lambda: Encoder(spec, max_len))
        self._encoder = None
        self._lock = threading.Lock()
        self.encoder_loads = 0

    @classmethod
    def from_freeze(cls, freeze_path: Path | None = None, encoder_factory=None) -> "RetrievalService":
        """The CR-2 service: frozen jina-code + hybrid, vector cache read into memory once."""
        freeze = load_frozen(freeze_path)
        spec = MODELS[freeze["embedding"]["model"]]
        max_len = int(freeze["embedding"]["max_len"])
        vectors = VectorStore(spec, max_len)
        vectors.preload()
        service = cls(spec, max_len, cr_eval.HybridConfig(**freeze["hybrid"]["config"]), vectors, encoder_factory)
        service.freeze = freeze
        return service

    def warm(self) -> None:
        """Load the encoder now, so the first tool call of a run does not pay for it."""
        with self._lock:
            if self._encoder is None:
                self._encoder = self._encoder_factory()
                self.encoder_loads += 1

    def query_vector(self, text: str) -> tuple[np.ndarray, str]:
        """Unit vector of a query and where it came from (``cache`` or ``encoded``).

        Freshly encoded vectors are rounded through float16, as the cache stores them.
        Nothing is written back to the CR-1 cache.
        """
        qt = query_text(self.spec, text)
        try:
            return self.vectors.get_matrix([text_sha(qt)])[0], "cache"
        except KeyError:
            pass
        with self._lock:
            if self._encoder is None:
                self._encoder = self._encoder_factory()
                self.encoder_loads += 1
            vec = self._encoder.encode([qt])[0]
        return vec.astype(np.float16).astype(np.float32), "encoded"

    def task_index(self, repo: str, base_commit: str) -> "TaskIndex":
        store = ChunkStore(repo)
        try:
            snap = load_snapshot(store, repo_cache.repo_dir(repo), base_commit)
        finally:
            store.close()
        ix = cr_eval.SnapshotIndex(snap)
        texts = [doc_text(self.spec, snap.chunk(i).text) for i in range(len(ix.chunk_ids))]
        matrix = self.vectors.get_matrix([text_sha(t) for t in texts])
        return TaskIndex(self, repo, base_commit, ix, matrix)


class TaskIndex:
    """BM25 indexes and chunk vectors of one (repo, base_commit) snapshot."""

    def __init__(self, service: RetrievalService, repo: str, base_commit: str, ix, matrix: np.ndarray):
        self.service = service
        self.repo = repo
        self.base_commit = base_commit
        self.ix = ix
        self.matrix = matrix
        self._lock = threading.Lock()

    @property
    def n_files(self) -> int:
        return len(self.ix.snap.paths)

    def orders(self, text: str) -> tuple[list[int], list[int], str]:
        """Full frozen-hybrid file and chunk orders for a query text."""
        qv, source = self.service.query_vector(text)
        query = QueryView(f"cr2:{self.repo}@{self.base_commit}", self.repo, self.base_commit, text)
        scores = _Scores(self.service.spec, self.matrix @ qv)
        with self._lock:
            # cr_eval memoizes rank material per (instance_id, model); a new query must not reuse it
            self.ix.__dict__.pop("_materials", None)
            file_order, chunk_order, _ = cr_eval._hybrid_orders(query, self.ix, scores, self.service.config)
            self.ix.__dict__.pop("_materials", None)
        return file_order, chunk_order, source

    def search(self, text: str, k: int) -> tuple[list[FileHit], str]:
        """Top ``k`` files; each with its at most ``CHUNKS_PER_FILE`` best-ranked chunks."""
        file_order, chunk_order, source = self.orders(text)
        snap = self.ix.snap
        top = file_order[:k]
        picked: dict[int, list[ChunkHit]] = {f: [] for f in top}
        remaining = len(top)
        chunk_file = snap.chunk_file
        for i in chunk_order:
            f = int(chunk_file[i])
            got = picked.get(f)
            if got is None or len(got) >= CHUNKS_PER_FILE:
                continue
            span = snap.chunk_spans[i]
            got.append(ChunkHit(span.symbol, span.start_line, span.end_line))
            if len(got) == CHUNKS_PER_FILE:
                remaining -= 1
                if remaining == 0:
                    break
        return [FileHit(snap.paths[f], tuple(picked[f])) for f in top], source


def render_hits(hits: list[FileHit]) -> str:
    lines = []
    for n, hit in enumerate(hits, 1):
        lines.append(f"{n}. {hit.path}")
        for c in hit.chunks:
            lines.append(f"   - {c.symbol} (lines {c.start_line}-{c.end_line})")
    return "\n".join(lines)


def render_prefetch(hits: list[FileHit]) -> str:
    """The P-group suffix appended to the first user message."""
    text = PREFETCH_HEADER + "\n\n" + render_hits(hits) + "\n\n" + PREFETCH_FOOTER
    size = len(text.encode("utf-8"))
    if size > PREFETCH_MAX_BYTES:
        raise ValueError(f"prefetch text is {size} bytes, above {PREFETCH_MAX_BYTES}")
    return text


def render_tool_result(hits: list[FileHit]) -> str:
    """The ``retrieve_code`` tool output."""
    if not hits:
        return TOOL_HEADER + "\n\nNo files found."
    return TOOL_HEADER + "\n\n" + render_hits(hits)


def hits_record(hits: list[FileHit]) -> list[dict]:
    return [{"path": h.path, "chunks": [[c.symbol, c.start_line, c.end_line] for c in h.chunks]} for h in hits]
