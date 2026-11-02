"""Vector cache and text keys (no torch needed)."""
import re
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from src.retrieval import embed
from src.retrieval.embed import MODELS, ModelSpec, VectorStore, doc_text, encode_missing, query_text, text_sha

SPEC = ModelSpec("fake", "org/fake-model", "a" * 40, "Q: ", "D: ", False, "mit")


def _unit(rng, n, d=8):
    v = rng.standard_normal((n, d)).astype(np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def test_prefixes_change_the_key():
    assert doc_text(SPEC, "x") == "D: x" and query_text(SPEC, "x") == "Q: x"
    assert text_sha(doc_text(SPEC, "x")) != text_sha("x")
    assert len({text_sha(doc_text(s, "x")) for s in (SPEC, replace(SPEC, doc_prefix=""))}) == 2


def test_vector_store_round_trip(tmp_path):
    rng = np.random.default_rng(0)
    vs = VectorStore(SPEC, 512, tmp_path)
    shas = [text_sha(t) for t in ("a", "b", "c")]
    vecs = _unit(rng, 3)
    assert vs.missing(shas) == shas
    vs.put_many([(s, 10, i == 1, v) for i, (s, v) in enumerate(zip(shas, vecs))])
    assert vs.missing(shas + [text_sha("d")]) == [text_sha("d")]
    got = vs.get_matrix([shas[2], shas[0], shas[2]])
    assert got.dtype == np.float32 and got.shape == (3, 8)
    assert np.allclose(got[0], vecs[2], atol=1e-3) and np.allclose(got[1], vecs[0], atol=1e-3)
    assert vs.truncation_stats(shas) == (1, 3)
    with pytest.raises(KeyError):
        vs.get_matrix([text_sha("d")])
    assert vs.preload() == 3
    assert np.array_equal(vs.get_matrix([shas[2], shas[0], shas[2]]), got)
    with pytest.raises(KeyError):
        vs.get_matrix([text_sha("d")])
    vs.close()


def test_vector_store_key_includes_model_revision_and_length(tmp_path):
    paths = {
        VectorStore(SPEC, 512, tmp_path).path,
        VectorStore(SPEC, 256, tmp_path).path,
        VectorStore(replace(SPEC, revision="b" * 40), 512, tmp_path).path,
        VectorStore(replace(SPEC, name="org/other"), 512, tmp_path).path,
    }
    assert len(paths) == 4


def test_vector_store_meta_mismatch_raises(tmp_path):
    vs = VectorStore(SPEC, 512, tmp_path)
    vs.db.execute("UPDATE meta SET v='999' WHERE k='max_len'")
    vs.db.commit()
    vs.close()
    with pytest.raises(ValueError):
        VectorStore(SPEC, 512, tmp_path)


class FakeEncoder:
    max_len = 4

    def __init__(self):
        self.calls = []

    def token_lengths(self, texts):
        return np.array([len(t.split()) for t in texts])

    def encode(self, texts):
        self.calls.append(list(texts))
        rng = np.random.default_rng(len(texts))
        return _unit(rng, len(texts))


def test_encode_missing_longest_first_and_skips_cached(tmp_path):
    vs = VectorStore(SPEC, 4, tmp_path)
    enc = FakeEncoder()
    texts = ["a", "a b c d e f", "a b", "a"]
    out = encode_missing(enc, vs, texts, group=2)
    assert out["encoded"] == 3 and out["truncated"] == 1 and out["tokens"] == 1 + 4 + 2
    assert enc.calls == [["a b c d e f", "a b"], ["a"]]
    assert encode_missing(enc, vs, texts)["encoded"] == 0
    vs.close()


def test_models_pin_revisions_and_remote_code():
    for spec in MODELS.values():
        assert re.fullmatch(r"[0-9a-f]{40}", spec.revision), spec.key
        if spec.code_revision:
            assert re.fullmatch(r"[0-9a-f]{40}", spec.code_revision), spec.key


def test_torch_imported_lazily():
    src = Path(embed.__file__).read_text(encoding="utf-8")
    assert not re.search(r"^(import torch|from torch|import sentence_transformers|from sentence_transformers)", src, re.M)
