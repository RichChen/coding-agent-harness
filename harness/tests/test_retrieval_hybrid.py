"""Embedding, hybrid and hybrid_mention retrievers on a tiny repo with a fake vector store."""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

from src.retrieval import dataset, repo_cache
from src.retrieval.chunking import ChunkStore, load_snapshot
from src.retrieval.embed import ModelSpec, VectorStore, doc_text, query_text, text_sha
from src.retrieval.fusion import order_by_score
from tests.test_retrieval_pipeline import FILES_V1
from tests.test_retrieval_repo_cache import make_repo

ROOT = Path(__file__).resolve().parents[1]
SPEC = ModelSpec("fake", "org/fake-model", "a" * 40, "Q: ", "", False, "mit")
ISSUE = "QuerySet.filter() drops args"


def _load_cr_eval():
    spec = importlib.util.spec_from_file_location("cr_eval_hybrid", ROOT / "scripts" / "cr_eval.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve string annotations through sys.modules
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    work, shas = make_repo(tmp_path, [FILES_V1])
    monkeypatch.setenv("CR_CACHE_DIR", str(tmp_path / "cache"))
    git_dir, _ = repo_cache.ensure_clone("o/r", url=str(work))
    store = ChunkStore("o/r")
    snap = load_snapshot(store, git_dir, shas[0])
    store.close()
    cr_eval = _load_cr_eval()
    ix = cr_eval.SnapshotIndex(snap)
    # the query vector equals the vector of HTTPResponse.header, so embedding ranks it first
    rng = np.random.default_rng(0)
    vs = VectorStore(SPEC, 512, tmp_path / "vec")
    texts = [doc_text(SPEC, snap.chunk(i).text) for i in range(len(ix.chunk_ids))]
    vecs = rng.standard_normal((len(texts), 16)).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    target = ix.chunk_ids.index("pkg/http.py::HTTPResponse.header@2")
    rows = [(text_sha(t), 5, False, v) for t, v in zip(texts, vecs)]
    rows.append((text_sha(query_text(SPEC, ISSUE)), 5, False, vecs[target]))
    vs.put_many(rows)
    q = dataset.QueryView("o__r-1", "o/r", shas[0], ISSUE)
    yield cr_eval, snap, ix, vs, q
    vs.close()


def test_fast_order_matches_order_by_score():
    cr_eval = _load_cr_eval()
    rng = np.random.default_rng(1)
    keys = [(f"f{rng.integers(5)}.py", int(rng.integers(50))) for _ in range(200)]
    scores = rng.integers(0, 4, 200).astype(float)  # many ties
    assert cr_eval.fast_order(scores, cr_eval._key_rank(keys)) == order_by_score(scores, keys)


def test_embedding_retriever(setup):
    cr_eval, snap, ix, vs, q = setup
    ev = cr_eval.EmbeddingView(SPEC, vs, q, ix)
    r = cr_eval.retrieve_embedding(q, ix, ev)
    assert r["chunks"][0] == "pkg/http.py::HTTPResponse.header@2"
    assert r["files"][0] == "pkg/http.py"
    assert sorted(r["files"]) == snap.paths and sorted(r["chunks"]) == sorted(ix.chunk_ids)


def test_hybrid_extremes_follow_each_side(setup):
    cr_eval, snap, ix, vs, q = setup
    ev = cr_eval.EmbeddingView(SPEC, vs, q, ix)
    bm25 = cr_eval.retrieve_bm25_chunk(q, ix)
    emb = cr_eval.retrieve_embedding(q, ix, ev)
    for fusion in ("chunk", "file"):
        lo = cr_eval.retrieve_hybrid(q, ix, ev, cr_eval.HybridConfig(60, 1e-6, fusion, "chunk"))
        hi = cr_eval.retrieve_hybrid(q, ix, ev, cr_eval.HybridConfig(60, 1e6, fusion, "chunk"))
        assert lo["files"] == bm25["files"] and lo["chunks"] == bm25["chunks"]
        assert hi["files"] == emb["files"] and hi["chunks"] == emb["chunks"]


def test_every_grid_config_returns_full_permutations(setup):
    cr_eval, snap, ix, vs, q = setup
    ev = cr_eval.EmbeddingView(SPEC, vs, q, ix)
    assert len(cr_eval.GRID) == 24 and len({c.name for c in cr_eval.GRID}) == 24
    for cfg in cr_eval.GRID:
        r = cr_eval.retrieve_hybrid(q, ix, ev, cfg)
        assert sorted(r["files"]) == snap.paths and sorted(r["chunks"]) == sorted(ix.chunk_ids)


def test_hybrid_mention_moves_named_file_first(setup):
    cr_eval, snap, ix, vs, q = setup
    q2 = dataset.QueryView(q.instance_id, q.repo, q.base_commit, ISSUE + " (see pkg/__init__.py)")
    vs.put_many([(text_sha(query_text(SPEC, q2.problem_statement)), 5, False,
                  vs.get_matrix([text_sha(query_text(SPEC, ISSUE))])[0])])
    ev = cr_eval.EmbeddingView(SPEC, vs, q2, ix)
    cfg = cr_eval.HybridConfig(60, 1.0, "chunk", "chunk")
    base = cr_eval.retrieve_hybrid(q2, ix, ev, cfg)
    r = cr_eval.retrieve_hybrid_mention(q2, ix, ev, cfg)
    assert r["mentioned"] == ["pkg/__init__.py"]
    assert r["files"][0] == "pkg/__init__.py"
    assert r["files"][1:] == [f for f in base["files"] if f != "pkg/__init__.py"]


def test_select_hybrid_rule():
    cr_eval = _load_cr_eval()
    H = cr_eval.HybridConfig
    mrr = {H(10, 2.0, "file", "file"): 0.600, H(60, 1.0, "file", "chunk"): 0.592,
           H(60, 1.0, "chunk", "chunk"): 0.585, H(60, 0.5, "chunk", "chunk"): 0.595}
    assert cr_eval.select_hybrid(mrr) == H(60, 1.0, "file", "chunk")  # k=60, w=1 within 0.01; chunk fusion is 0.015 off
    mrr[H(60, 1.0, "chunk", "chunk")] = 0.591
    assert cr_eval.select_hybrid(mrr) == H(60, 1.0, "chunk", "chunk")
    assert cr_eval.select_hybrid({H(10, 2.0, "file", "file"): 0.7, H(60, 1.0, "chunk", "chunk"): 0.68}) == H(10, 2.0, "file", "file")


def test_frozen_configs(setup, monkeypatch):
    cr_eval, snap, ix, vs, q = setup
    monkeypatch.setitem(cr_eval.MODELS, "fake", SPEC)
    freeze = {"embedding": {"model": "fake", "max_len": 512},
              "hybrid": {"config": {"k": 60, "w_emb": 1.0, "fusion": "chunk", "bm25": "chunk"}}}
    fn, models = cr_eval.frozen_per_instance(freeze)
    assert models == [("fake", 512)]
    out = fn(q, ix, {"fake": vs})
    assert list(out) == ["random", "path_mention", "bm25", "embedding", "hybrid", "hybrid_mention"]
    assert out["bm25"] == cr_eval.retrieve_bm25_chunk(q, ix)
