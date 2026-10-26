"""End to end on a tiny local repo: cache, snapshot, retrievers, labels, metrics."""
import importlib.util
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from src.retrieval import dataset, repo_cache
from src.retrieval.chunking import ChunkStore, ensure_blobs, load_snapshot, tree_entries
from src.retrieval.labels import load_label_views
from src.retrieval.metrics import score_instance
from tests.test_retrieval_repo_cache import make_repo

ROOT = Path(__file__).resolve().parents[1]

FILES_V1 = {
    "pkg/__init__.py": "",
    "pkg/query.py": (
        "class QuerySet:\n"
        "    def filter(self, *args):\n"
        "        return self._filter_or_exclude(False, args)\n"
        "\n"
        "    def exclude(self, *args):\n"
        "        return self._filter_or_exclude(True, args)\n"
    ),
    "pkg/http.py": "class HTTPResponse:\n    def header(self, name):\n        return name\n",
    "pkg/tests/test_query.py": "def test_filter():\n    assert QuerySet().filter()\n",
    "README.rst": "QuerySet filter docs\n",
}

GOLD_DIFF = (
    "diff --git a/pkg/query.py b/pkg/query.py\n--- a/pkg/query.py\n+++ b/pkg/query.py\n"
    "@@ -1,3 +1,3 @@\n class QuerySet:\n     def filter(self, *args):\n"
    "-        return self._filter_or_exclude(False, args)\n+        return self._filter_or_exclude(False, *args)\n"
    "diff --git a/pkg/tests/test_query.py b/pkg/tests/test_query.py\n"
    "--- a/pkg/tests/test_query.py\n+++ b/pkg/tests/test_query.py\n@@ -1 +1 @@\n-def test_filter():\n+def test_filter2():\n"
)


def _load_cr_eval():
    spec = importlib.util.spec_from_file_location("cr_eval_pipeline", ROOT / "scripts" / "cr_eval.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve string annotations through sys.modules
    spec.loader.exec_module(mod)
    return mod


def test_pipeline(tmp_path, monkeypatch):
    work, shas = make_repo(tmp_path, [FILES_V1, {"pkg/http.py": "class HTTPResponse:\n    pass\n"}])
    cache = tmp_path / "cache"
    monkeypatch.setenv("CR_CACHE_DIR", str(cache))
    git_dir, _ = repo_cache.ensure_clone("o/r", url=str(work))

    table = pa.table({
        "repo": ["o/r", "o/r"],
        "instance_id": ["o__r-1", "o__r-2"],
        "base_commit": [shas[0], shas[1]],
        "patch": [GOLD_DIFF, GOLD_DIFF],
        "problem_statement": ["QuerySet.filter() drops args", "HTTPResponse header lost"],
        "hints_text": ["", ""],
    })
    parquet = tmp_path / "v.parquet"
    pq.write_table(table, parquet)
    queries = dataset.load_query_views(parquet)
    assert [q.instance_id for q in queries] == ["o__r-1", "o__r-2"]

    store = ChunkStore("o/r")
    snaps = [load_snapshot(store, git_dir, q.base_commit) for q in queries]
    assert snaps[0].paths == ["pkg/__init__.py", "pkg/http.py", "pkg/query.py"]
    # README and test file are outside the corpus; unchanged blobs are reused across commits
    entries = tree_entries(store, git_dir, shas[1])
    built, reused = ensure_blobs(store, git_dir, [e.blob_sha for e in entries if e.path.endswith(".py")])
    assert built == 1 and reused == 3  # test file built now, the rest cached
    store.close()

    cr_eval = _load_cr_eval()
    rec = cr_eval.retrieve_instance(queries[0], snaps[0])
    r = rec["rankings"]
    assert r["bm25_chunk"]["files"][0] == "pkg/query.py"
    assert r["bm25_chunk"]["chunks"][0] == "pkg/query.py::QuerySet.filter@2"
    assert r["bm25_file"]["files"][0] == "pkg/query.py" and r["bm25_file"]["chunks"] is None
    assert sorted(r["random"]["files"]) == snaps[0].paths
    assert r["path_mention"]["mentioned"] == []

    lvs = load_label_views(queries, parquet=parquet)
    lv = lvs["o__r-1"]
    assert lv.gold_files == ("pkg/query.py",)
    assert lv.excluded_counts() == {"test": 1}
    assert lv.gold_chunks == ("pkg/query.py::QuerySet.filter@2",)
    m = score_instance(lv, r["bm25_chunk"]["files"], r["bm25_chunk"]["chunks"])
    assert m["hit@1"] == 1.0 and m["mrr"] == 1.0 and m["fn_hit@5"] == 1.0

    rows, summary = cr_eval.score_rankings([rec], {"o__r-1": lv})
    assert summary["retrievers"]["bm25_chunk"]["all"]["mrr"] == 1.0
    assert "fn_hit@5" not in summary["retrievers"]["bm25_file"]["all"]


def test_path_mention_moves_named_file_first(tmp_path, monkeypatch):
    work, shas = make_repo(tmp_path, [FILES_V1])
    monkeypatch.setenv("CR_CACHE_DIR", str(tmp_path / "cache"))
    git_dir, _ = repo_cache.ensure_clone("o/r", url=str(work))
    store = ChunkStore("o/r")
    snap = load_snapshot(store, git_dir, shas[0])
    store.close()
    q = dataset.QueryView("o__r-9", "o/r", shas[0], "QuerySet.filter breaks, see pkg/http.py")
    cr_eval = _load_cr_eval()
    r = cr_eval.retrieve_instance(q, snap)["rankings"]
    assert r["path_mention"]["mentioned"] == ["pkg/http.py"]
    assert r["path_mention"]["files"][0] == "pkg/http.py"
    assert r["path_mention"]["chunks"][0].startswith("pkg/http.py::")
    assert r["bm25_chunk"]["files"][0] == "pkg/query.py"
