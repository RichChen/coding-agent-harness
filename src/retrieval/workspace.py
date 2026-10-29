"""Query-boundary workspace indexing over injected capture/blob contracts only.

There is no Docker, host tree scanner, frozen CR cache, or agent lifecycle here.
Each update builds complete BM25 statistics and vectors off to the side. Calls
serialize around one publication; failed updates make search unavailable until
a successful update, rather than serving the previous capture as current.
"""
from __future__ import annotations

import io
import math
import threading
import tokenize as python_tokenize
from dataclasses import dataclass
from pathlib import PurePosixPath

import numpy as np

from src.retrieval.bm25 import from_token_lists, query_term_ids
from src.retrieval.chunking import CHUNKER_VERSION, WINDOW_LINES, WINDOW_STEP, chunk_header, chunk_source
from src.retrieval.fusion import max_by_file, order_by_score, ranks_from_order, rrf
from src.retrieval.tokenize import TOKENIZER_VERSION, tokenize
from src.retrieval.workspace_encoder import WorkspaceEncoder, WorkspaceEncodingError
from src.workspace.contracts import (
    BlobReader, ContractError, WorkspaceCapture, corpus_id, corpus_rules_id,
    index_id, read_verified_blob,
)

_TEST_DIRS = ("test", "tests", "testing")
_SUPPORT_DIRS = ("fixture", "fixtures", "roots", "testdata")
_EXCLUDED_DIRS = (".git", "__pycache__", ".pytest_cache", ".venv", "venv",
                  "build", "dist", ".ssh", ".aws", ".grader_private", ".harness_artifacts")


@dataclass(frozen=True)
class CorpusRules:
    max_file_bytes: int = 1048576

    def __post_init__(self):
        if type(self.max_file_bytes) is not int or not 1 <= self.max_file_bytes <= 1048576:
            raise ValueError("Python corpus file limit must be within 1..1MiB")

    def to_dict(self) -> dict:
        return dict(version="workspace-python-v1", suffix=".py", max_file_bytes=self.max_file_bytes,
                    decoding="pep263-strict-universal-newlines-v1", decode_error="exclude",
                    ast_error="window", test_directories=_TEST_DIRS,
                    test_names=("test_*.py", "*_test.py"), support_directories=_SUPPORT_DIRS,
                    conftest="test_support", other_python_in_test_directory="test_support",
                    excluded_directories=_EXCLUDED_DIRS, excluded_names=(".env*",),
                    path_order="utf8-bytes")


@dataclass(frozen=True)
class IndexConfig:
    mode: str  # no implicit BM25 fallback
    bm25_k1: str = "1.2"
    bm25_b: str = "0.75"
    rrf_k: int = 60
    lexical_weight: str = "1"
    embedding_weight: str = "2"
    chunks_per_file: int = 2

    def __post_init__(self):
        if self.mode not in ("bm25", "hybrid"):
            raise ValueError("explicit mode must be bm25 or hybrid")
        for name in ("bm25_k1", "bm25_b", "lexical_weight", "embedding_weight"):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise ValueError("fractional identity configuration must use decimal strings")
            try:
                number = float(value)
            except ValueError as exc:
                raise ValueError(f"invalid {name}") from exc
            if not math.isfinite(number):
                raise ValueError(f"nonfinite {name}")
        if (float(self.bm25_k1) <= 0 or not 0 <= float(self.bm25_b) <= 1
                or float(self.lexical_weight) <= 0 or float(self.embedding_weight) <= 0):
            raise ValueError("invalid BM25/RRF range")
        if type(self.rrf_k) is not int or self.rrf_k < 1:
            raise ValueError("rrf_k must be a positive integer")
        if type(self.chunks_per_file) is not int or self.chunks_per_file < 1:
            raise ValueError("chunks_per_file must be a positive integer")


@dataclass(frozen=True)
class CorpusNotice:
    path: str
    reason: str


@dataclass(frozen=True)
class IndexReceipt:
    status: str
    capture_id: str
    candidate_id: str
    corpus_id: str | None
    index_id: str | None
    mode: str
    files: int = 0
    chunks: int = 0
    rebuilt: int = 0  # selected files, including new names
    reused: int = 0  # selected files unchanged at the same path
    encoded: int = 0  # unique document inputs newly encoded
    vectors_reused: int = 0  # unique document inputs found in vector cache
    deleted: int = 0  # previously indexed paths absent from the new corpus
    exclusions: tuple[CorpusNotice, ...] = ()
    warnings: tuple[CorpusNotice, ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class ChunkLocation:
    symbol: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class RetrievalHit:
    path: str
    file_sha256: str
    material_kind: str
    chunks: tuple[ChunkLocation, ...]
    score: float


@dataclass(frozen=True)
class RetrievalResponse:
    receipt: IndexReceipt | None
    hits: tuple[RetrievalHit, ...] = ()
    status: str = "ready"
    reason: str | None = None

    @property
    def capture_id(self):
        return self.receipt.capture_id if self.receipt else None

    @property
    def candidate_id(self):
        return self.receipt.candidate_id if self.receipt else None

    @property
    def corpus_id(self):
        return self.receipt.corpus_id if self.receipt else None

    @property
    def index_id(self):
        return self.receipt.index_id if self.receipt else None


def material_kind(path: str) -> str:
    """Explicit path-only policy, recorded in corpus_rules_id (no file execution)."""
    parts = PurePosixPath(path).parts
    name = parts[-1]
    if name == "conftest.py" or any(part in _SUPPORT_DIRS for part in parts[:-1]):
        return "test_support"
    if name.startswith("test_") or name.endswith("_test.py"):
        return "test"
    if any(part in _TEST_DIRS for part in parts[:-1]):
        return "test_support"
    return "source"


def _excluded_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return (any(part in _EXCLUDED_DIRS for part in parts[:-1])
            or any(part.startswith(".env") for part in parts))


class _BuildError(Exception):
    pass


@dataclass(frozen=True)
class _File:
    path: str
    sha256: str
    kind: str
    spans: tuple
    bodies: tuple[str, ...]
    parse_ok: bool


@dataclass(frozen=True)
class _State:
    receipt: IndexReceipt
    files: tuple[_File, ...]
    chunk_files: np.ndarray
    locations: tuple[ChunkLocation, ...]
    keys: tuple
    bm25: object
    vocab: object
    matrix: np.ndarray | None


class WorkspaceIndex:
    def __init__(self, blob_reader: BlobReader, *, config: IndexConfig,
                 encoder: WorkspaceEncoder | None = None, corpus_rules: CorpusRules = CorpusRules()):
        if not isinstance(config, IndexConfig) or not isinstance(corpus_rules, CorpusRules):
            raise ValueError("typed index/corpus configuration required")
        if config.mode == "hybrid" and not isinstance(encoder, WorkspaceEncoder):
            raise ValueError("hybrid requires an explicit WorkspaceEncoder")
        if config.mode == "bm25" and encoder is not None:
            raise ValueError("bm25 mode does not accept an unused encoder")
        self._reader, self._config, self._encoder, self._rules = blob_reader, config, encoder, corpus_rules
        self._lock = threading.RLock()
        self._state: _State | None = None
        self._receipt: IndexReceipt | None = None
        self._last_files: tuple[_File, ...] = ()
        self._body_cache: dict[str, tuple] = {}
        self._corpus_rules_id = corpus_rules_id(corpus_rules.to_dict())

    @property
    def receipt(self) -> IndexReceipt | None:
        with self._lock:
            return self._receipt

    def update(self, capture: WorkspaceCapture) -> IndexReceipt:
        if not isinstance(capture, WorkspaceCapture):
            raise TypeError("WorkspaceCapture required")
        with self._lock:
            # Invalidate immediately; historical cache is for rebuilding only.
            self._state = None
            self._receipt = IndexReceipt("unavailable", capture.capture_id, capture.candidate_id,
                                         None, None, self._config.mode, reason="update_incomplete")
            try:
                state, body_cache = self._build(capture)
            except _BuildError as exc:
                reason = str(exc)
            except WorkspaceEncodingError:
                reason = "encoder_unavailable"
            except (ContractError, KeyError, OSError):
                reason = "blob_unavailable_or_invalid"
            except Exception:
                reason = "index_build_failed"
            else:
                self._body_cache.update(body_cache)
                self._last_files = state.files
                self._state, self._receipt = state, state.receipt
                return state.receipt
            self._receipt = IndexReceipt("unavailable", capture.capture_id, capture.candidate_id,
                                         None, None, self._config.mode, reason=reason)
            return self._receipt

    def _build(self, capture: WorkspaceCapture):
        refs = dict(capture.corpus_blobs)
        files, selected, exclusions, warnings, staged = [], [], [], [], {}
        previous = {file.path: file for file in self._last_files}
        rebuilt = reused = 0
        for entry in capture.entries:
            if not entry.path.endswith(".py"):
                continue
            if _excluded_path(entry.path):
                exclusions.append(CorpusNotice(entry.path, "excluded_path"))
                continue
            if entry.size > self._rules.max_file_bytes:
                exclusions.append(CorpusNotice(entry.path, "file_too_large"))
                continue
            ref = refs.get(entry.path)
            cached = staged.get(entry.sha256, self._body_cache.get(entry.sha256))
            if ref is None:
                if cached is None:
                    raise _BuildError("missing_corpus_blob")
            else:
                data = read_verified_blob(self._reader, ref)
                if cached is None:
                    try:
                        encoding, _ = python_tokenize.detect_encoding(io.BytesIO(data).readline)
                        source = data.decode(encoding, errors="strict").replace("\r\n", "\n").replace("\r", "\n")
                    except (SyntaxError, LookupError, UnicodeError):
                        exclusions.append(CorpusNotice(entry.path, "decode_error"))
                        continue
                    spans, bodies, parse_ok = chunk_source(source)
                    cached = (tuple(spans), tuple(bodies), parse_ok)
                    staged[entry.sha256] = cached
            spans, bodies, parse_ok = cached
            kind = material_kind(entry.path)
            file = _File(entry.path, entry.sha256, kind, spans, bodies, parse_ok)
            files.append(file)
            selected.append((entry, kind))
            prior = previous.get(entry.path)
            if prior and prior.sha256 == entry.sha256 and prior.kind == kind:
                reused += 1
            else:
                rebuilt += 1
            if not parse_ok:
                warnings.append(CorpusNotice(entry.path, "ast_window_fallback"))

        cid = corpus_id(selected, corpus_rules_id=self._corpus_rules_id)
        iid = index_id(corpus_id=cid,
                       chunker=dict(version=CHUNKER_VERSION, window_lines=WINDOW_LINES,
                                    window_step=WINDOW_STEP, header="path-symbol-v1"),
                       tokenizer=dict(version=TOKENIZER_VERSION),
                       encoder=self._encoder.config.to_dict() if self._encoder else {},
                       bm25=dict(k1=self._config.bm25_k1, b=self._config.bm25_b, aggregation="chunk-max-file-v1"),
                       rrf=dict(k=self._config.rrf_k, lexical_weight=self._config.lexical_weight,
                                embedding_weight=self._config.embedding_weight, fusion="file-and-chunk-v1",
                                chunks_per_file=self._config.chunks_per_file, ties="path-symbol-lines-v1"),
                       mode=self._config.mode)
        texts, locations, chunk_files, keys = [], [], [], []
        for number, file in enumerate(files):
            for span, body in zip(file.spans, file.bodies):
                texts.append(chunk_header(file.path, span.symbol) + "\n" + body)
                locations.append(ChunkLocation(span.symbol, span.start_line, span.end_line))
                chunk_files.append(number)
                keys.append((file.path.encode("utf-8"), span.symbol, span.start_line, span.end_line))
        bm25, vocab = from_token_lists([tokenize(text) for text in texts],
                                       float(self._config.bm25_k1), float(self._config.bm25_b))
        matrix = None
        encoded = vectors_reused = 0
        if self._encoder and texts:
            batch = self._encoder.encode(texts, role="document")
            matrix, encoded, vectors_reused = batch.vectors, batch.encoded, batch.reused
        receipt = IndexReceipt("ready", capture.capture_id, capture.candidate_id, cid, iid,
                               self._config.mode, files=len(files), chunks=len(texts),
                               rebuilt=rebuilt, reused=reused, encoded=encoded, vectors_reused=vectors_reused,
                               deleted=len(set(previous) - {file.path for file in files}),
                               exclusions=tuple(exclusions), warnings=tuple(warnings))
        return _State(receipt, tuple(files), np.array(chunk_files, np.int64), tuple(locations),
                      tuple(keys), bm25, vocab, matrix), staged

    def search(self, query: str, k: int = 5) -> RetrievalResponse:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("nonempty text query required")
        query.encode("utf-8")
        if type(k) is not int or k < 1:
            raise ValueError("k must be a positive integer")
        with self._lock:
            state = self._state
            if state is None:
                return RetrievalResponse(self._receipt, status="unavailable",
                                         reason=self._receipt.reason if self._receipt else "not_initialized")
            if not state.files:
                return RetrievalResponse(state.receipt)
            lexical = state.bm25.score(query_term_ids(query, state.vocab))
            chunk_order = order_by_score(lexical, list(state.keys))
            file_keys = [file.path.encode("utf-8") for file in state.files]
            file_scores = max_by_file(lexical, state.chunk_files, len(state.files))
            file_order = order_by_score(file_scores, file_keys)
            if self._encoder:
                try:
                    vector = self._encoder.encode([query], role="query").vectors[0]
                    semantic = state.matrix @ vector
                    semantic_file = max_by_file(semantic, state.chunk_files, len(state.files))
                    weights = [float(self._config.lexical_weight), float(self._config.embedding_weight)]
                    file_scores = rrf([ranks_from_order(file_order, len(file_keys)),
                                       ranks_from_order(order_by_score(semantic_file, file_keys), len(file_keys))],
                                      weights, self._config.rrf_k)
                    chunk_scores = rrf([ranks_from_order(chunk_order, len(state.keys)),
                                        ranks_from_order(order_by_score(semantic, list(state.keys)), len(state.keys))],
                                       weights, self._config.rrf_k)
                    file_order = order_by_score(file_scores, file_keys)
                    chunk_order = order_by_score(chunk_scores, list(state.keys))
                except Exception:
                    return RetrievalResponse(state.receipt, status="unavailable", reason="encoder_unavailable")
            hits = []
            for number in file_order[:k]:
                file = state.files[number]
                picked = tuple(state.locations[i] for i in chunk_order if state.chunk_files[i] == number)
                hits.append(RetrievalHit(file.path, file.sha256, file.kind,
                                         picked[:self._config.chunks_per_file], float(file_scores[number])))
            return RetrievalResponse(state.receipt, tuple(hits))
