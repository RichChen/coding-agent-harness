"""Cache identity/integrity checks without torch, downloads or a solver."""
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
import sqlite3
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from src.retrieval.workspace_encoder import (
    EncoderConfig, LocalSentenceTransformerBackend, WorkspaceEncoder, WorkspaceEncodingError,
)
from src.retrieval import workspace_encoder
from src.retrieval.embed import MODELS


class Backend:
    def __init__(self):
        self.calls = []

    def encode(self, texts):
        self.calls.append(texts)
        return np.array([[len(text) + 1, text.count("a") + 1, 1] for text in texts], np.float32)


def config(**kwargs):
    return EncoderConfig("fixture/model", "a" * 40, "fixture", **kwargs)


def test_exact_prefixed_inputs_and_duplicate_accounting(tmp_path):
    backend = Backend()
    encoder = WorkspaceEncoder(config(document_prefix="D:", query_prefix="Q:"), lambda: backend,
                                cache_root=tmp_path)
    batch = encoder.encode(["alpha", "beta", "alpha"], role="document")
    assert backend.calls == [["D:alpha", "D:beta"]]
    assert batch.encoded == 2 and batch.reused == 0
    assert batch.text_hashes == tuple(sha256(text.encode()).hexdigest() for text in ["D:alpha", "D:beta", "D:alpha"])
    assert np.array_equal(batch.vectors[0], batch.vectors[2])
    assert np.allclose(np.linalg.norm(batch.vectors, axis=1), 1)
    assert not batch.vectors.flags.writeable
    second = encoder.encode(["alpha", "gamma"], role="document")
    assert second.encoded == 1 and second.reused == 1
    query = encoder.encode(["alpha"], role="query")
    assert query.encoded == 1 and backend.calls[-1] == ["Q:alpha"]
    assert "workspace-v1" in encoder.cache_path.parts
    encoder.close()


def test_sqlite_reopen_never_loads_backend_for_cached_input(tmp_path):
    first = WorkspaceEncoder(config(), Backend, cache_root=tmp_path)
    expected = first.encode(["alpha"], role="document").vectors.copy()
    first.close()

    def forbidden():
        raise AssertionError("cache hit loaded model")

    reopened = WorkspaceEncoder(config(), forbidden, cache_root=tmp_path)
    actual = reopened.encode(["alpha"], role="document")
    assert actual.reused == 1 and actual.encoded == 0
    assert np.array_equal(expected, actual.vectors)
    reopened.close()


@pytest.mark.parametrize("change", [dict(model="fixture/other"), dict(revision="b" * 40),
    dict(max_length=256), dict(precision="float16"), dict(query_prefix="Q:"),
    dict(document_prefix="D:"), dict(device="other"), dict(runtime_versions=(("backend", "2"),))])
def test_all_encoder_inputs_isolate_cache(tmp_path, change):
    backend = Backend()
    original = WorkspaceEncoder(config(), lambda: backend, cache_root=tmp_path)
    original.encode(["same"], role="document")
    other = WorkspaceEncoder(replace(config(), **change), lambda: backend, cache_root=tmp_path)
    assert other.cache_path != original.cache_path
    assert other.encode(["same"], role="document").encoded == 1
    original.close()
    other.close()


@pytest.mark.parametrize("kwargs", [dict(revision="main"), dict(max_length=0), dict(max_length=True),
    dict(precision="auto"), dict(device=""), dict(preprocessing="unknown"),
    dict(runtime_versions=(("x", "1"), ("x", "2")))])
def test_invalid_identity_is_rejected(kwargs):
    values = dict(model="fixture", revision="a" * 40, device="fixture")
    values.update(kwargs)
    with pytest.raises(ValueError):
        EncoderConfig(**values)


@pytest.mark.parametrize("bad", [np.array([[0, 0]]), np.array([[np.nan, 1]]),
    np.array([[np.inf, 1]]), np.array([1, 2]), np.ones((2, 3)), np.ones((1, 0))])
def test_bad_encoder_batch_is_not_cached(tmp_path, bad):
    class Bad:
        def encode(self, texts):
            return bad
    encoder = WorkspaceEncoder(config(), Bad, cache_root=tmp_path)
    with pytest.raises(WorkspaceEncodingError):
        encoder.encode(["alpha"], role="document")
    assert encoder._db.execute("SELECT count(*) FROM vectors").fetchone()[0] == 0
    encoder.close()


def test_dimension_change_is_rejected_and_keeps_old_cache():
    class Changing:
        def __init__(self):
            self.dimension = 2
        def encode(self, texts):
            self.dimension += 1
            return np.ones((len(texts), self.dimension))
    encoder = WorkspaceEncoder(config(), Changing)
    expected = encoder.encode(["alpha"], role="document")
    with pytest.raises(WorkspaceEncodingError):
        encoder.encode(["new"], role="document")
    assert np.array_equal(expected.vectors, encoder.encode(["alpha"], role="document").vectors)


def test_corrupt_vector_fails_without_backend_fallback(tmp_path):
    first = WorkspaceEncoder(config(), Backend, cache_root=tmp_path)
    first.encode(["alpha"], role="document")
    path = first.cache_path
    first.close()
    with sqlite3.connect(path) as db:
        db.execute("UPDATE vectors SET value=?", (b"broken",))
    backend = Backend()
    second = WorkspaceEncoder(config(), lambda: backend, cache_root=tmp_path)
    with pytest.raises(WorkspaceEncodingError, match="integrity"):
        second.encode(["alpha"], role="document")
    assert not backend.calls
    second.close()


def test_corrupt_identity_is_not_overwritten(tmp_path):
    encoder = WorkspaceEncoder(config(), Backend, cache_root=tmp_path)
    path = encoder.cache_path
    encoder.close()
    with sqlite3.connect(path) as db:
        db.execute("UPDATE meta SET value=? WHERE key='config'", (b"wrong",))
    with pytest.raises(WorkspaceEncodingError, match="config mismatch"):
        WorkspaceEncoder(config(), Backend, cache_root=tmp_path)


def test_lazy_backend_failure_is_explicit_and_closed_encoder_stays_closed():
    def absent():
        raise RuntimeError("model unavailable")
    encoder = WorkspaceEncoder(config(), absent)
    assert encoder.encode([], role="document").vectors.shape == (0, 0)
    with pytest.raises(WorkspaceEncodingError, match="unavailable"):
        encoder.encode(["alpha"], role="document")
    with pytest.raises(FrozenInstanceError):
        encoder.config.precision = "float16"
    encoder.close()
    with pytest.raises(WorkspaceEncodingError, match="closed"):
        encoder.encode([], role="query")


def test_local_adapter_rejects_unpinned_model_before_importing_model_stack(tmp_path):
    with pytest.raises(WorkspaceEncodingError, match="pinned Qwen"):
        LocalSentenceTransformerBackend(config(), tmp_path)


def test_local_adapter_parameters_are_explicit_without_loading_real_model(tmp_path, monkeypatch):
    spec = MODELS["qwen3-0.6b"]
    path = tmp_path / spec.revision
    path.mkdir()
    calls = []
    class Model:
        def __init__(self, source, **kwargs):
            calls.append((source, kwargs))
            self.dtype = kwargs["model_kwargs"]["torch_dtype"]
        def to(self, *, dtype):
            self.dtype = dtype
        def parameters(self):
            return [SimpleNamespace(dtype=self.dtype)]
        def encode(self, texts, **kwargs):
            calls.append((texts, kwargs))
            return np.ones((len(texts), 3), np.float32)
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(float32="float32", float16="float16"))
    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=Model))
    monkeypatch.setattr(workspace_encoder, "_runtime_versions", lambda: (("backend", "pinned"),))
    cfg = EncoderConfig(spec.name, spec.revision, "cpu", max_length=123,
                         query_prefix=spec.query_prefix, runtime_versions=(("backend", "pinned"),))
    backend = LocalSentenceTransformerBackend(cfg, path)
    assert calls[0] == (str(path), dict(device="cpu", trust_remote_code=False, local_files_only=True,
                                       model_kwargs={"torch_dtype": "float32"}))
    assert backend.model.max_seq_length == 123
    assert backend.encode(["document"]).shape == (1, 3)
    assert calls[1][1] == dict(batch_size=8, convert_to_numpy=True, normalize_embeddings=False,
                              show_progress_bar=False)
    with pytest.raises(WorkspaceEncodingError, match="library versions"):
        LocalSentenceTransformerBackend(replace(cfg, runtime_versions=()), path)


def test_local_snapshot_is_required_and_never_falls_back_to_hub(tmp_path):
    spec = MODELS["qwen3-0.6b"]
    cfg = EncoderConfig(spec.name, spec.revision, "cpu", query_prefix=spec.query_prefix)
    with pytest.raises(WorkspaceEncodingError, match="snapshot is absent"):
        LocalSentenceTransformerBackend(cfg, tmp_path / spec.revision)
