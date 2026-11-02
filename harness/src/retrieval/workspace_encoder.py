"""Pinned, injectable encoders and a separate workspace-v1 vector cache.

No model imports or downloads on import/construction. Inputs to the backend are
the exact prefixed strings whose UTF-8 hashes key the cache. The production
adapter accepts only an approved local, non-remote-code snapshot; custom-code
models need a separate audited adapter rather than an implicit network fallback.
"""
from __future__ import annotations

import re
import sqlite3
import threading
from importlib.metadata import version
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import numpy as np

from src.workspace.contracts import blob_sha256, canonical_json, content_id

CACHE_NAMESPACE = "workspace-v1"


class WorkspaceEncodingError(RuntimeError):
    """Encoding/cache failure; callers must not silently fall back to BM25."""


@dataclass(frozen=True)
class EncoderConfig:
    model: str
    revision: str
    device: str
    precision: str = "float32"
    max_length: int = 512
    query_prefix: str = ""
    document_prefix: str = ""
    preprocessing: str = "prefix-utf8-no-normalization-v1"
    runtime_versions: tuple[tuple[str, str], ...] = ()

    def __post_init__(self):
        for name in ("model", "device"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or "\0" in value:
                raise ValueError(f"{name} must be explicit")
        if not isinstance(self.revision, str) or not re.fullmatch(r"[0-9a-f]{40}", self.revision):
            raise ValueError("encoder revision must be a pinned 40-character commit")
        if self.precision not in ("float32", "float16"):
            raise ValueError("precision must be float32 or float16")
        if type(self.max_length) is not int or self.max_length < 1:
            raise ValueError("max_length must be a positive integer")
        if self.preprocessing != "prefix-utf8-no-normalization-v1":
            raise ValueError("unsupported preprocessing")
        for prefix in (self.query_prefix, self.document_prefix):
            if not isinstance(prefix, str):
                raise ValueError("prefix must be text")
            prefix.encode("utf-8")
        if (not isinstance(self.runtime_versions, tuple)
                or any(not isinstance(pair, tuple) or len(pair) != 2
                       or any(not isinstance(value, str) or not value for value in pair)
                       for pair in self.runtime_versions)
                or len(dict(self.runtime_versions)) != len(self.runtime_versions)):
            raise ValueError("runtime_versions must contain unique immutable name/version pairs")
        object.__setattr__(self, "runtime_versions", tuple(sorted(self.runtime_versions)))

    def to_dict(self) -> dict:
        return dict(model=self.model, revision=self.revision, device=self.device,
                    precision=self.precision, max_length=self.max_length,
                    query_prefix=self.query_prefix, document_prefix=self.document_prefix,
                    preprocessing=self.preprocessing, adapter="workspace-encoder-v1",
                    output="unit-float32", cache_precision="little-endian-float32", runtime_versions=self.runtime_versions)


class EncoderBackend(Protocol):
    def encode(self, texts: list[str]) -> np.ndarray: ...


@dataclass(frozen=True)
class EncodedBatch:
    vectors: np.ndarray
    encoded: int
    reused: int
    text_hashes: tuple[str, ...]


def _vectors(value, count: int, dimension: int | None = None) -> np.ndarray:
    try:
        array = np.array(value, dtype=np.float32, copy=True)
    except (ValueError, TypeError, OverflowError) as exc:
        raise WorkspaceEncodingError("invalid encoder output") from exc
    if (array.ndim != 2 or array.shape[0] != count or array.shape[1] < 1
            or (dimension is not None and array.shape[1] != dimension)
            or not np.isfinite(array).all()):
        raise WorkspaceEncodingError("encoder shape/dimension/nonfinite output")
    norms = np.linalg.norm(array.astype(np.float64), axis=1)
    if (norms <= 1e-12).any():
        raise WorkspaceEncodingError("zero-norm encoder output")
    array /= norms[:, None]
    return array


class WorkspaceEncoder:
    """Lazy backend + verified memory/SQLite cache, never the frozen CR cache.

    ``encoded``/``reused`` count distinct actual input strings within a call.
    A failed batch commits no newly encoded vectors. A cache mismatch is an
    error, not permission to relabel corrupt data or silently re-encode it.
    Instances serialize calls; close only after index users have stopped.
    """

    def __init__(self, config: EncoderConfig, backend_factory: Callable[[], EncoderBackend],
                 *, cache_root: Path | None = None):
        if not isinstance(config, EncoderConfig) or not callable(backend_factory):
            raise ValueError("config and backend_factory are required")
        self._config = config
        self._factory = backend_factory
        self._backend = None
        self._memory: dict[str, np.ndarray] = {}
        self._dimension: int | None = None
        self._lock = threading.Lock()
        self._closed = False
        self.cache_id = content_id("workspace-encoder-v1", config.to_dict())
        self.cache_path = None
        self._db = None
        if cache_root is not None:
            self.cache_path = Path(cache_root) / CACHE_NAMESPACE / "vectors" / (self.cache_id.split(":")[1] + ".sqlite")
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(self.cache_path, check_same_thread=False)
            try:
                self._db.executescript("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value BLOB);"
                                       "CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, dim INTEGER, value BLOB, sha TEXT);")
                identity = canonical_json(config.to_dict())
                stored = self._db.execute("SELECT value FROM meta WHERE key='config'").fetchone()
                if stored is not None and stored[0] != identity:
                    raise WorkspaceEncodingError("vector cache config mismatch")
                self._db.execute("INSERT OR IGNORE INTO meta VALUES ('config', ?)", (identity,))
                dim = self._db.execute("SELECT value FROM meta WHERE key='dimension'").fetchone()
                if dim is not None:
                    self._dimension = int(dim[0])
                    if self._dimension < 1:
                        raise WorkspaceEncodingError("invalid cache dimension")
                self._db.commit()
            except Exception:
                self._db.close()
                raise

    @property
    def config(self) -> EncoderConfig:
        return self._config

    def _get(self, key: str) -> np.ndarray | None:
        if key in self._memory:
            return self._memory[key]
        if self._db is None:
            return None
        row = self._db.execute("SELECT dim, value, sha FROM vectors WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        dim, raw, checksum = row
        if (dim != self._dimension or len(raw) != dim * 4 or blob_sha256(raw) != checksum):
            raise WorkspaceEncodingError("vector cache integrity mismatch")
        vector = np.frombuffer(raw, "<f4").astype(np.float32)
        # Cached values are already normalized. Preserve their exact bytes.
        if not np.isfinite(vector).all() or not np.isclose(np.linalg.norm(vector), 1, atol=1e-5):
            raise WorkspaceEncodingError("invalid cached unit vector")
        vector.setflags(write=False)
        self._memory[key] = vector
        return vector

    def encode(self, texts: list[str], *, role: str) -> EncodedBatch:
        if role not in ("document", "query"):
            raise ValueError("role must be document or query")
        if not isinstance(texts, list) or any(not isinstance(text, str) for text in texts):
            raise ValueError("texts must be a list of strings")
        prefix = self.config.document_prefix if role == "document" else self.config.query_prefix
        actual = [prefix + text for text in texts]
        hashes = tuple(blob_sha256(text.encode("utf-8")) for text in actual)
        with self._lock:
            if self._closed:
                raise WorkspaceEncodingError("encoder is closed")
            try:
                unique = dict(zip(hashes, actual))
                found = {key: self._get(key) for key in unique}
                missing = [key for key, value in found.items() if value is None]
                if missing:
                    if self._backend is None:
                        self._backend = self._factory()
                    matrix = _vectors(self._backend.encode([unique[key] for key in missing]),
                                      len(missing), self._dimension)
                    dimension = matrix.shape[1]
                    if self._db is not None:
                        with self._db:
                            self._db.execute("INSERT OR REPLACE INTO meta VALUES ('dimension', ?)", (dimension,))
                            for key, vector in zip(missing, matrix):
                                raw = vector.astype("<f4").tobytes()
                                self._db.execute("INSERT OR REPLACE INTO vectors VALUES (?, ?, ?, ?)",
                                                 (key, dimension, raw, blob_sha256(raw)))
                    self._dimension = dimension
                    for key, vector in zip(missing, matrix):
                        vector.setflags(write=False)
                        self._memory[key] = found[key] = vector
                output = (np.stack([found[key] for key in hashes]) if hashes
                          else np.empty((0, self._dimension or 0), np.float32))
                output.setflags(write=False)
                return EncodedBatch(output, len(missing), len(unique) - len(missing), hashes)
            except WorkspaceEncodingError:
                raise
            except Exception as exc:
                raise WorkspaceEncodingError("encoder/cache unavailable") from exc

    def close(self):
        with self._lock:
            if not self._closed and self._db is not None:
                self._db.close()
            self._closed = True


class LocalSentenceTransformerBackend:
    """Offline-only snapshot loader. No trust_remote_code or implicit CUDA.

    The approved local HF snapshot directory must be named by the pinned
    revision. Its provenance/integrity belongs to model preparation. The
    config and cache additionally pin installed inference library versions.
    This adapter deliberately supports only the existing Qwen model spec.
    """

    def __init__(self, config: EncoderConfig, snapshot_path: Path):
        from src.retrieval.embed import MODELS

        spec = MODELS["qwen3-0.6b"]
        path = Path(snapshot_path).absolute()
        if (config.model != spec.name or config.revision != spec.revision
                or config.query_prefix != spec.query_prefix or config.document_prefix != spec.doc_prefix):
            raise WorkspaceEncodingError("local adapter requires the pinned Qwen model and prefixes")
        if path.name != config.revision or not path.is_dir():
            raise WorkspaceEncodingError("approved pinned local snapshot is absent")
        if config.runtime_versions != _runtime_versions():
            raise WorkspaceEncodingError("inference library versions differ from encoder identity")
        import torch
        from sentence_transformers import SentenceTransformer

        dtype = torch.float32 if config.precision == "float32" else torch.float16
        self.model = SentenceTransformer(str(path), device=config.device, trust_remote_code=False,
                                         local_files_only=True, model_kwargs={"torch_dtype": dtype})
        self.model.to(dtype=dtype)
        if {parameter.dtype for parameter in self.model.parameters()} != {dtype}:
            raise WorkspaceEncodingError("model inference precision mismatch")
        self.model.max_seq_length = config.max_length

    def encode(self, texts: list[str]) -> np.ndarray:
        return self.model.encode(texts, batch_size=8, convert_to_numpy=True,
                                 normalize_embeddings=False, show_progress_bar=False)


def _runtime_versions():
    return tuple(sorted((name, version(name)) for name in
                        ("numpy", "torch", "sentence-transformers", "transformers", "tokenizers")))


def local_qwen_encoder(snapshot_path: Path, *, device: str, cache_root: Path,
                       precision: str = "float32", max_length: int = 512) -> WorkspaceEncoder:
    """Prepare the lazy local adapter, pinning installed inference versions too."""
    from src.retrieval.embed import MODELS

    spec = MODELS["qwen3-0.6b"]
    config = EncoderConfig(spec.name, spec.revision, device, precision, max_length,
                           spec.query_prefix, spec.doc_prefix, runtime_versions=_runtime_versions())
    return WorkspaceEncoder(config, lambda: LocalSentenceTransformerBackend(config, snapshot_path),
                            cache_root=cache_root)


def _smoke():
    """Explicitly invoked future smoke; never called by fixture tests/imports."""
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Offline workspace encoder smoke; loads a real model")
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--cache-root", required=True, type=Path)
    parser.add_argument("--device", required=True)
    parser.add_argument("--precision", choices=("float16", "float32"), default="float32")
    args = parser.parse_args()
    encoder = local_qwen_encoder(args.snapshot, device=args.device, precision=args.precision,
                                  cache_root=args.cache_root)
    try:
        docs = ["# path: pkg/calc.py  symbol: add\ndef add(a, b): return a + b",
                "# path: tests/test_calc.py  symbol: test_add\ndef test_add(): assert add(1, 2) == 3"]
        first = encoder.encode(docs, role="document")
        second = encoder.encode(docs, role="document")
        query = encoder.encode(["addition calculation"], role="query")
        assert second.encoded == 0 and second.reused == 2
        assert np.array_equal(first.vectors, second.vectors)
        assert query.vectors.shape[1] == first.vectors.shape[1]
        print(json.dumps(dict(config=encoder.config.to_dict(), cache=str(encoder.cache_path),
                              shape=first.vectors.shape, encoded=first.encoded, reused=second.reused,
                              document_text_hashes=first.text_hashes, query_text_hashes=query.text_hashes,
                              scores=(first.vectors @ query.vectors[0]).tolist()), ensure_ascii=False))
    finally:
        encoder.close()


if __name__ == "__main__":
    _smoke()
