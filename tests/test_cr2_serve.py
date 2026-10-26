"""CR-2 retrieval service and retrieve_code tool on a tiny repo with a fake vector store and encoder."""
import asyncio
import json
from pathlib import Path

import numpy as np
import pytest

from scripts import cr_eval
from src.retrieval import dataset, repo_cache, serve
from src.retrieval.chunking import ChunkStore, load_snapshot
from src.retrieval.embed import ModelSpec, VectorStore, doc_text, query_text, text_sha
from src.tools.base import ToolContext
from src.tools.retrieve import MAX_QUERY_CHARS, RetrieveCodeTool, TaskRetrieval
from tests.test_retrieval_pipeline import FILES_V1
from tests.test_retrieval_repo_cache import make_repo

SPEC = ModelSpec("fake", "org/fake-model", "a" * 40, "Q: ", "", False, "mit")
ISSUE = "QuerySet.filter() drops args"
TARGET = "pkg/http.py::HTTPResponse.header@2"


class FakeEncoder:
    def __init__(self, vector):
        self.vector = vector
        self.calls = []

    def encode(self, texts):
        self.calls.append(list(texts))
        return np.stack([self.vector for _ in texts])


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    work, shas = make_repo(tmp_path, [FILES_V1])
    monkeypatch.setenv("CR_CACHE_DIR", str(tmp_path / "cache"))
    git_dir, _ = repo_cache.ensure_clone("o/r", url=str(work))
    store = ChunkStore("o/r")
    snap = load_snapshot(store, git_dir, shas[0])
    store.close()
    ix = cr_eval.SnapshotIndex(snap)
    rng = np.random.default_rng(0)
    vs = VectorStore(SPEC, 512, tmp_path / "vec")
    texts = [doc_text(SPEC, snap.chunk(i).text) for i in range(len(ix.chunk_ids))]
    vecs = rng.standard_normal((len(texts), 16)).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    target = ix.chunk_ids.index(TARGET)
    rows = [(text_sha(t), 5, False, v) for t, v in zip(texts, vecs)]
    rows.append((text_sha(query_text(SPEC, ISSUE)), 5, False, vecs[target]))
    vs.put_many(rows)
    vs.preload()  # as RetrievalService.from_freeze does; tool calls read vectors from worker threads
    encoder = FakeEncoder(vecs[target])
    config = cr_eval.HybridConfig(60, 2.0, "file", "chunk")
    service = serve.RetrievalService(SPEC, 512, config, vs, encoder_factory=lambda: encoder)
    q = dataset.QueryView("o__r-1", "o/r", shas[0], ISSUE)
    yield service, encoder, snap, ix, vs, q, config
    vs.close()


def test_frozen_matches_cr1_freeze_file():
    freeze = serve.load_frozen()
    assert freeze["embedding"]["model"] == "jina-code"


def test_orders_equal_frozen_hybrid(setup):
    service, encoder, snap, ix, vs, q, config = setup
    ti = service.task_index("o/r", q.base_commit)
    file_order, chunk_order, source = ti.orders(ISSUE)
    assert source == "cache" and service.encoder_loads == 0
    ref = cr_eval.retrieve_hybrid(q, ix, cr_eval.EmbeddingView(SPEC, vs, q, ix), config)
    assert [snap.paths[i] for i in file_order] == ref["files"]
    assert [ix.chunk_ids[i] for i in chunk_order] == ref["chunks"]


def test_search_top_files_and_best_chunks(setup):
    service, encoder, snap, ix, vs, q, config = setup
    ti = service.task_index("o/r", q.base_commit)
    file_order, chunk_order, _ = ti.orders(ISSUE)
    hits, _ = ti.search(ISSUE, 2)
    assert [h.path for h in hits] == [snap.paths[i] for i in file_order[:2]]
    for hit in hits:
        assert 1 <= len(hit.chunks) <= serve.CHUNKS_PER_FILE
        ranked = [ix.chunk_ids[i] for i in chunk_order if ix.chunk_ids[i].startswith(hit.path + "::")]
        assert [c.symbol for c in hit.chunks] == [cid.split("::", 1)[1].rsplit("@", 1)[0] for cid in ranked[:len(hit.chunks)]]
    assert hits[0].path == "pkg/http.py" and hits[0].chunks[0].symbol == "HTTPResponse.header"


def test_free_query_encodes_once_without_cache_write(setup):
    service, encoder, snap, ix, vs, q, config = setup
    ti = service.task_index("o/r", q.base_commit)
    count = lambda: vs.db.execute("SELECT COUNT(*) FROM vec").fetchone()[0]  # noqa: E731
    before = count()
    hits, source = ti.search("header of an http response", 3)
    ti.search("another free query", 3)
    assert source == "encoded" and service.encoder_loads == 1
    assert encoder.calls == [[query_text(SPEC, "header of an http response")], [query_text(SPEC, "another free query")]]
    assert count() == before
    assert hits[0].path == "pkg/http.py"
    service.warm()
    assert service.encoder_loads == 1


def test_render_prefetch_shape_and_cap():
    hits = [serve.FileHit("a/b.py", (serve.ChunkHit("f", 1, 9), serve.ChunkHit("C.m", 20, 30))),
            serve.FileHit("c.py", (serve.ChunkHit("<module>", 1, 4),))]
    text = serve.render_prefetch(hits)
    assert text.startswith(serve.PREFETCH_HEADER + "\n\n1. a/b.py\n   - f (lines 1-9)\n   - C.m (lines 20-30)\n2. c.py")
    assert text.endswith("\n\n" + serve.PREFETCH_FOOTER)
    assert "def " not in text
    long = [serve.FileHit("x/" * 200 + "y.py", ())] * 5
    with pytest.raises(ValueError):
        serve.render_prefetch(long)
    assert serve.render_tool_result([]).endswith("No files found.")
    assert serve.render_tool_result(hits).startswith(serve.TOOL_HEADER + "\n\n1. a/b.py")
    assert serve.hits_record(hits)[0] == {"path": "a/b.py", "chunks": [["f", 1, 9], ["C.m", 20, 30]]}


def _run(tool, params, call_id="c1"):
    return asyncio.run(tool.execute(params, ToolContext(workspace_root=Path("."), call_id=call_id)))


def test_tool_logs_calls_and_returns_paths_only(setup, tmp_path):
    service, encoder, snap, ix, vs, q, config = setup
    log = tmp_path / "run" / "retrieval-calls.jsonl"
    tool = RetrieveCodeTool(TaskRetrieval(service.task_index("o/r", q.base_commit), log))
    result = _run(tool, {"query": ISSUE, "k": 2})
    assert result.success and result.output.startswith(serve.TOOL_HEADER)
    assert "1. pkg/http.py" in result.output and "def " not in result.output
    default = _run(tool, {"query": ISSUE}, call_id="c2")
    assert default.success
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [r["call_id"] for r in rows] == ["c1", "c2"]
    assert rows[0]["k"] == 2 and len(rows[0]["files"]) == 2 and rows[0]["query_vector"] == "cache"
    assert rows[1]["k"] == 5 and rows[0]["files"][0]["path"] == "pkg/http.py"


def test_tool_rejects_bad_arguments_and_logs_them(setup, tmp_path):
    service, encoder, snap, ix, vs, q, config = setup
    log = tmp_path / "calls.jsonl"
    tool = RetrieveCodeTool(TaskRetrieval(service.task_index("o/r", q.base_commit), log))
    for params in ({"query": " "}, {"query": "x" * (MAX_QUERY_CHARS + 1)}, {"query": "a", "k": 11}, {"query": "a", "k": True}):
        assert not _run(tool, params).success
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 4 and all(r["error"] for r in rows)
    assert not _run(RetrieveCodeTool(), {"query": "a"}).success


def test_tool_failure_returns_traceback(tmp_path):
    class Broken:
        def search(self, query, k):
            raise RuntimeError("index exploded")

    log = tmp_path / "calls.jsonl"
    result = _run(RetrieveCodeTool(TaskRetrieval(Broken(), log)), {"query": "a"})
    assert not result.success and "Traceback (most recent call last)" in result.output
    row = json.loads(log.read_text(encoding="utf-8"))
    assert "index exploded" in row["error"] and row["traceback"]


def test_tool_schema_limits():
    tool = RetrieveCodeTool()
    props = tool.parameters["properties"]
    assert tool.side_effect.value == "read_only"
    assert props["query"]["maxLength"] == 2000 and props["k"]["maximum"] == 10 and tool.parameters["required"] == ["query"]
    assert not hasattr(tool, "backend")
