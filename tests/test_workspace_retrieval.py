"""Workspace retrieval semantics over shared C0/C1/C2 and controlled blobs."""
from dataclasses import replace
from pathlib import Path
import runpy
import threading

import numpy as np
import pytest

from src.retrieval.workspace import CorpusRules, IndexConfig, WorkspaceIndex
from src.retrieval.workspace_encoder import EncoderConfig, WorkspaceEncoder
from src.workspace.contracts import corpus_id, corpus_rules_id

FIXTURE = runpy.run_path(str(Path(__file__).parent / "fixtures/workspace_contracts/fixture.py"))
BASE = FIXTURE["BASE_FILES"]
capture = FIXTURE["build_capture"]


class Backend:
    def __init__(self):
        self.calls = []
        self.fail = False
    def encode(self, texts):
        self.calls.append(list(texts))
        if self.fail:
            raise RuntimeError("backend unavailable")
        return np.array([[text.count("add") + 1, text.count("return") + 1, len(text) + 1]
                         for text in texts], np.float32)


def hybrid(reader, *, cache_root=None, backend=None, **kwargs):
    backend = backend or Backend()
    encoder = WorkspaceEncoder(EncoderConfig("fixture", "a" * 40, "fixture"), lambda: backend,
                                cache_root=cache_root)
    return WorkspaceIndex(reader, config=IndexConfig(mode="hybrid", **kwargs), encoder=encoder), backend, encoder


def test_shared_sequence_updates_source_test_locations_and_current_identities():
    captures, reader = FIXTURE["build_sequence"]()
    index = WorkspaceIndex(reader, config=IndexConfig(mode="bm25"))
    receipts = []
    for name in ("C0", "C1", "C2"):
        current = captures[name]
        receipt = index.update(current)
        response = index.search("add", 2)
        assert receipt.status == response.status == "ready"
        assert response.capture_id == current.capture_id and response.candidate_id == current.candidate_id
        assert response.corpus_id == receipt.corpus_id and response.index_id == receipt.index_id
        assert {hit.path: hit.material_kind for hit in response.hits} == {"pkg/calc.py": "source", "tests/test_calc.py": "test"}
        assert {hit.path: hit.file_sha256 for hit in response.hits} == {
            entry.path: entry.sha256 for entry in current.entries if entry.path.endswith(".py")}
        assert any(chunk.symbol == "add" and chunk.start_line == 1 and chunk.end_line == 2
                   for hit in response.hits for chunk in hit.chunks)
        receipts.append(receipt)
    assert len({receipt.corpus_id for receipt in receipts}) == 3
    assert len({receipt.index_id for receipt in receipts}) == 3
    assert receipts[-1].rebuilt == 1 and receipts[-1].reused == 1


def test_no_change_and_non_python_change_reuse_index_but_associate_latest_capture(tmp_path):
    current, reader = capture()
    index, backend, encoder = hybrid(reader, cache_root=tmp_path)
    first = index.update(current)
    same, _ = capture(number=1, blobs=reader)
    second = index.update(same)
    assert (second.corpus_id, second.index_id) == (first.corpus_id, first.index_id)
    assert second.capture_id != first.capture_id and second.candidate_id == first.candidate_id
    assert second.encoded == second.rebuilt == 0 and second.reused == 2
    files = dict(BASE, **{"data/rules.json": b'{"operation":"subtract"}\n'})
    other, _ = capture(files, number=2, blobs=reader, violations=({"reason": "unsupported_change"},))
    third = index.update(other)
    assert third.index_id == first.index_id and third.corpus_id == first.corpus_id
    assert third.candidate_id != first.candidate_id
    assert index.search("add").capture_id == other.capture_id
    assert len(backend.calls) == 2  # document batch and first query only
    encoder.close()


def test_add_modify_delete_rename_line_movement_and_text_cache(tmp_path):
    current, reader = capture()
    index, backend, encoder = hybrid(reader, cache_root=tmp_path)
    first = index.update(current)
    files = dict(BASE, **{"pkg/extra.py": b"def multiply(a, b):\n    return a * b\n"})
    added, _ = capture(files, number=1, blobs=reader)
    add = index.update(added)
    assert add.files == 3 and add.rebuilt == 1 and add.reused == 2 and add.encoded == 2
    files["pkg/calc.py"] = b"\n\ndef add(a, b):\n    return a + b\n"
    modified, _ = capture(files, number=2, blobs=reader)
    changed = index.update(modified)
    assert changed.rebuilt == 1 and changed.reused == 2
    assert changed.encoded == 2  # added blank lines also change actual module input
    result = index.search("add", 3)
    add_locations = next(hit.chunks for hit in result.hits if hit.path == "pkg/calc.py")
    assert any(chunk.symbol == "add" and chunk.start_line == 3 for chunk in add_locations)
    files["pkg/calc.py"] = b"\n\n\ndef add(a, b):\n    return a + b\n"
    shifted, _ = capture(files, number=5, blobs=reader)
    movement = index.update(shifted)
    assert movement.encoded == 1  # function text is unchanged; its new location is not an encoding key
    assert any(chunk.symbol == "add" and chunk.start_line == 4
               for hit in index.search("add", 3).hits if hit.path == "pkg/calc.py" for chunk in hit.chunks)
    files["pkg/renamed.py"] = files.pop("pkg/extra.py")
    renamed, _ = capture(files, number=3, blobs=reader)
    rename = index.update(renamed)
    assert rename.deleted == rename.rebuilt == 1 and rename.encoded == 2  # path is in actual input
    assert "pkg/extra.py" not in {hit.path for hit in index.search("multiply", 10).hits}
    del files["pkg/renamed.py"]
    removed, _ = capture(files, number=4, blobs=reader)
    removal = index.update(removed)
    assert removal.deleted == 1 and removal.rebuilt == removal.encoded == 0
    assert {hit.path for hit in index.search("multiply", 10).hits} == {"pkg/calc.py", "tests/test_calc.py"}
    assert removal.index_id != first.index_id
    encoder.close()


def test_unchanged_bytes_can_reuse_verified_bodies_without_new_transfer():
    first, reader = capture()
    index = WorkspaceIndex(reader, config=IndexConfig(mode="bm25"))
    initial = index.update(first)
    later = replace(first, capture_id="00000000-0000-4000-8000-000000000099", corpus_blobs=())
    receipt = index.update(later)
    assert receipt.status == "ready" and receipt.index_id == initial.index_id
    fresh = WorkspaceIndex(reader, config=IndexConfig(mode="bm25"))
    assert fresh.update(later).reason == "missing_corpus_blob"


def test_python_encoding_exclusion_ast_fallback_and_material_kinds():
    files = dict(BASE, **{
        "latin.py": b"# coding: latin-1\r\ndef cafe():\r\n    return 'caf\xe9'\r\n",
        "broken.py": b"def syntax(\n",
        "bad.py": b"# coding: utf-8\n\xff",
        "unknown.py": b"# coding: made-up-codec\n",
        "conftest.py": b"def setup(): pass\n",
        "tests/helper.py": b"def helper(): pass\n",
        "tests/fixtures/model.py": b"class Model: pass\n",
        "testing/test_something.py": b"def test_behavior(): pass\n",
        ".venv/private.py": b"do not expose\n",
        ".grader_private/answer.py": b"do not expose\n",
    })
    current, reader = capture(files)
    index = WorkspaceIndex(reader, config=IndexConfig(mode="bm25"))
    receipt = index.update(current)
    assert receipt.status == "ready"
    assert {(notice.path, notice.reason) for notice in receipt.exclusions} == {
        ("bad.py", "decode_error"), ("unknown.py", "decode_error"),
        (".venv/private.py", "excluded_path"), (".grader_private/answer.py", "excluded_path")}
    assert [(notice.path, notice.reason) for notice in receipt.warnings] == [("broken.py", "ast_window_fallback")]
    hits = {hit.path: hit for hit in index.search("cafe", 100).hits}
    assert hits["latin.py"].material_kind == "source"
    assert any(chunk.symbol == "cafe" and chunk.start_line == 2 and chunk.end_line == 3
               for chunk in hits["latin.py"].chunks)
    assert hits["broken.py"].chunks[0].symbol.startswith("<window")
    assert hits["conftest.py"].material_kind == hits["tests/helper.py"].material_kind == "test_support"
    assert hits["tests/fixtures/model.py"].material_kind == "test_support"
    assert hits["testing/test_something.py"].material_kind == "test"
    selected = [(entry, hits[entry.path].material_kind) for entry in current.entries if entry.path in hits]
    assert receipt.corpus_id == corpus_id(selected, corpus_rules_id=corpus_rules_id(CorpusRules().to_dict()))


def test_file_limit_exact_boundary_and_explicit_rule_identity():
    files = {"fit.py": b"# ok\n", "too_big.py": b"# more\n", "empty.py": b""}
    current, reader = capture(files)
    restricted = WorkspaceIndex(reader, config=IndexConfig(mode="bm25"), corpus_rules=CorpusRules(5))
    receipt = restricted.update(current)
    assert receipt.status == "ready" and receipt.files == 2
    assert [(n.path, n.reason) for n in receipt.exclusions] == [("too_big.py", "file_too_large")]
    normal = WorkspaceIndex(reader, config=IndexConfig(mode="bm25"))
    assert normal.update(current).corpus_id != receipt.corpus_id


@pytest.mark.parametrize("failure", ["corrupt", "missing", "encoder"])
def test_failed_update_is_unavailable_and_never_publishes_old_hits(failure):
    original, reader = capture()
    index, backend, encoder = hybrid(reader)
    before = index.update(original)
    files = dict(BASE, **{"pkg/new.py": b"def fresh(): return 42\n"})
    current, _ = capture(files, number=1, blobs=reader)
    if failure == "missing":
        current = replace(current, corpus_blobs=tuple((p, r) for p, r in current.corpus_blobs if p != "pkg/new.py"))
    elif failure == "corrupt":
        ref = dict(current.corpus_blobs)["pkg/new.py"]
        reader.blobs[ref.key] = b"corrupt bytes"
    else:
        backend.fail = True
    receipt = index.update(current)
    response = index.search("add")
    assert receipt.status == response.status == "unavailable" and response.hits == ()
    assert response.capture_id == current.capture_id and response.candidate_id == current.candidate_id
    assert receipt.index_id is None and before.index_id is not None
    backend.fail = False
    assert index.update(original).status == "ready"
    assert index.search("add").candidate_id == original.candidate_id
    encoder.close()


def test_corrupt_blob_is_checked_even_when_previous_content_was_cached():
    current, reader = capture()
    index = WorkspaceIndex(reader, config=IndexConfig(mode="bm25"))
    assert index.update(current).status == "ready"
    reader.blobs[dict(current.corpus_blobs)["pkg/calc.py"].key] = b"bad"
    assert index.update(current).status == "unavailable"


def test_explicit_modes_and_no_query_encoder_fallback():
    current, reader = capture()
    with pytest.raises(ValueError, match="requires"):
        WorkspaceIndex(reader, config=IndexConfig(mode="hybrid"))
    index, backend, encoder = hybrid(reader)
    receipt = index.update(current)
    backend.fail = True
    result = index.search("uncached query")
    assert result.status == "unavailable" and result.hits == ()
    assert result.reason == "encoder_unavailable" and result.receipt.mode == "hybrid"
    bm25 = WorkspaceIndex(reader, config=IndexConfig(mode="bm25"))
    assert bm25.update(current).encoded == 0
    assert bm25.search("add").status == "ready"
    with pytest.raises(ValueError, match="unused"):
        WorkspaceIndex(reader, config=IndexConfig(mode="bm25"), encoder=encoder)
    assert receipt.mode == "hybrid"
    encoder.close()


def test_hybrid_rrf_semantics_and_bm25_are_explicitly_different():
    class Synonyms:
        def encode(self, texts):
            return np.array([[1, 0] if "motor" in text or text == "engine" else [0, 1]
                             for text in texts], np.float32)
    current, reader = capture({"lexical.py": b"def match(): return 'engine'\n",
                               "semantic.py": b"def synonym(): return 'motor'\n"})
    lexical = WorkspaceIndex(reader, config=IndexConfig(mode="bm25"))
    lexical.update(current)
    assert lexical.search("engine", 1).hits[0].path == "lexical.py"
    encoder = WorkspaceEncoder(EncoderConfig("synonyms", "b" * 40, "fixture"), Synonyms)
    mixed = WorkspaceIndex(reader, config=IndexConfig(mode="hybrid"), encoder=encoder)
    mixed.update(current)
    assert mixed.search("engine", 1).hits[0].path == "semantic.py"
    # File ranks: semantic lexical rank 2, embedding rank 1; weights are 1 and 2.
    assert mixed.search("engine", 1).hits[0].score == pytest.approx(1 / 62 + 2 / 61)


def test_bm25_rebuild_matches_fresh_index_after_additions_and_deletions():
    current, reader = capture({"a.py": b"rare common common\n", "b.py": b"common\n"})
    index = WorkspaceIndex(reader, config=IndexConfig(mode="bm25"))
    index.update(current)
    updated, _ = capture({"b.py": b"common\n", "c.py": b"rare rare common\n"}, number=1, blobs=reader)
    assert index.update(updated).deleted == 1
    fresh = WorkspaceIndex(reader, config=IndexConfig(mode="bm25"))
    fresh.update(updated)
    assert index.search("rare common").hits == fresh.search("rare common").hits
    assert all(hit.path != "a.py" for hit in index.search("rare").hits)


def test_index_identity_changes_with_encoder_and_ranking_config_only():
    current, reader = capture()
    indexes = []
    for revision, config in [("a", IndexConfig(mode="hybrid")),
                             ("b", IndexConfig(mode="hybrid")),
                             ("a", IndexConfig(mode="hybrid", embedding_weight="3")),
                             ("a", IndexConfig(mode="hybrid", bm25_b="0.5")),
                             ("a", IndexConfig(mode="hybrid", chunks_per_file=1))]:
        encoder = WorkspaceEncoder(EncoderConfig("fixture", revision * 40, "fixture"), Backend)
        index = WorkspaceIndex(reader, config=config, encoder=encoder)
        indexes.append(index.update(current))
    assert len({receipt.corpus_id for receipt in indexes}) == 1
    assert len({receipt.candidate_id for receipt in indexes}) == 1
    assert len({receipt.index_id for receipt in indexes}) == 5


def test_query_failure_does_not_publish_lexical_results_and_can_recover():
    current, reader = capture()
    index, backend, encoder = hybrid(reader)
    receipt = index.update(current)
    backend.fail = True
    assert index.search("new question").status == "unavailable"
    backend.fail = False
    recovered = index.search("new question")
    assert recovered.status == "ready" and recovered.index_id == receipt.index_id
    encoder.close()


def test_empty_corpus_and_deterministic_ties_are_valid():
    current, reader = capture({"data.json": b"{}"})
    index = WorkspaceIndex(reader, config=IndexConfig(mode="bm25"))
    assert index.search("add").reason == "not_initialized"
    receipt = index.update(current)
    assert receipt.status == "ready" and receipt.files == receipt.chunks == 0
    assert index.search("add").status == "ready" and index.search("add").hits == ()
    tied, _ = capture({"z.py": b"pass\n", "a.py": b"pass\n"}, number=1, blobs=reader)
    index.update(tied)
    assert [hit.path for hit in index.search("absent token", 9).hits] == ["a.py", "z.py"]


def test_atomic_publication_serializes_search_with_build():
    entered, release, search_started, search_finished = (threading.Event() for _ in range(4))
    class Pausing(Backend):
        def encode(self, texts):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test release timed out")
            return super().encode(texts)
    current, reader = capture()
    index, backend, encoder = hybrid(reader, backend=Pausing())
    receipts, results = [], []
    def search():
        search_started.set()
        results.append(index.search("add"))
        search_finished.set()
    updater = threading.Thread(target=lambda: receipts.append(index.update(current)))
    updater.start()
    assert entered.wait(5)
    searcher = threading.Thread(target=search)
    searcher.start()
    assert search_started.wait(5) and not search_finished.is_set()
    release.set()
    updater.join(5)
    searcher.join(5)
    assert not updater.is_alive() and not searcher.is_alive()
    assert receipts[0].status == results[0].status == "ready"
    assert results[0].index_id == receipts[0].index_id
    encoder.close()


@pytest.mark.parametrize("change", [dict(bm25_k1="0"), dict(bm25_b="2"), dict(rrf_k=0),
    dict(mode="automatic"), dict(embedding_weight="nan"), dict(chunks_per_file=True)])
def test_invalid_ranking_configuration_fails(change):
    values = dict(mode="bm25")
    values.update(change)
    with pytest.raises(ValueError):
        IndexConfig(**values)


@pytest.mark.parametrize("query,k", [("", 1), ("  ", 1), (None, 1), ("add", 0), ("add", True)])
def test_invalid_search_request(query, k):
    _, reader = capture()
    index = WorkspaceIndex(reader, config=IndexConfig(mode="bm25"))
    with pytest.raises(ValueError):
        index.search(query, k)
