"""Semantic workspace-v1 tests using independent fixture/hash construction."""
import hashlib
import json
import runpy
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from src.workspace.contracts import (
    ArtifactRef, ContractError, FileEntry, WorkspaceCapture, blob_sha256,
    candidate_id, canonical_json, content_id, corpus_id, corpus_rules_id, index_id,
    read_verified_blob, rules_id, tree_id, validate_path, validation_key,
)

FIXTURE = runpy.run_path(str(Path(__file__).parent / "fixtures/workspace_contracts/fixture.py"))
BASE_FILES = FIXTURE["BASE_FILES"]
build_capture = FIXTURE["build_capture"]
build_sequence = FIXTURE["build_sequence"]
reference_id = FIXTURE["reference_id"]
reference_entries = FIXTURE["reference_entries"]


def corpus(capture):
    return corpus_id(((entry, "test" if entry.path.startswith("tests/") else "source")
                      for entry in capture.entries if entry.path.endswith(".py")),
                     corpus_rules_id=corpus_rules_id(FIXTURE["CORPUS_RULES"]))


def index(capture, **changes):
    return index_id(corpus_id=corpus(capture), **(FIXTURE["INDEX_CONFIG"] | changes))


def test_canonical_json_and_raw_hash_known_vectors():
    assert canonical_json({"z": [True, None, -2], "a": "汉é"}) == (
        '{"a":"汉é","z":[true,null,-2]}'.encode("utf-8"))
    assert blob_sha256(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert blob_sha256(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert tree_id(()) == "tree-v1:" + hashlib.sha256(b"tree-v1\0[]").hexdigest()
    assert content_id("example-v1", {"a": 1}) == (
        "example-v1:" + hashlib.sha256(b'example-v1\0{"a":1}').hexdigest())
    assert blob_sha256(b"a\r\n") != blob_sha256(b"a\n")
    assert content_id("one-v1", {}) != content_id("two-v1", {})
    assert canonical_json(["a", "b"]) != canonical_json(["b", "a"])


@pytest.mark.parametrize("value", [1.0, 0.0, float("nan"), float("inf"),
                                      {"nested": [0.25]}, {1: "key"}, {"set": {1}}])
def test_no_float_coercion_or_non_json_values(value):
    with pytest.raises(ContractError):
        canonical_json(value)


@pytest.mark.parametrize("path", ["", "/a", "//a", "a/", "a//b", ".", "..", "./a",
                                     "a/../b", "a/./b", "C:a", "c:/a", "a\\b", "a\0b", "\ud800"])
def test_invalid_paths(path):
    with pytest.raises(ContractError):
        validate_path(path)


def test_paths_preserve_unicode_and_case():
    assert validate_path("包/É.py") == "包/É.py"
    assert validate_path("pkg/e\u0301.py") != validate_path("pkg/é.py")
    first = FileEntry("pkg/e\u0301.py", "regular", 0o644, 0, blob_sha256(b""))
    second = replace(first, path="pkg/é.py")
    assert tree_id([first]) != tree_id([second])
    assert tree_id([first, second])


@pytest.mark.parametrize("changes", [{"kind": "symlink"}, {"mode": -1}, {"mode": True},
                                         {"mode": 0o100644}, {"mode": 1.0}, {"size": -1},
                                         {"size": False}, {"sha256": "A" * 64}, {"sha256": "x"}])
def test_entry_fields_rejected(changes):
    with pytest.raises(ContractError):
        FileEntry(**(dict(path="a.py", kind="regular", mode=0o644, size=0,
                          sha256=blob_sha256(b"")) | changes))


@pytest.mark.parametrize("paths", [("a", "a"), ("a", "A"), ("pkg/a", "PKG/b"),
                                      ("a", "a/b"), ("A", "a/b"), ("straße", "STRASSE")])
def test_manifest_path_collisions(paths):
    with pytest.raises(ContractError):
        tree_id(FileEntry(path, "regular", 0o644, 0, blob_sha256(b"")) for path in paths)


def test_sequence_independent_identity_vectors_and_blob_validation():
    captures, blobs = build_sequence()
    base = [FileEntry.from_dict(item) for item in reference_entries(BASE_FILES)]
    for name, capture in captures.items():
        capture.validate_against_base(base)
        capture.validate_blobs(blobs)
        expected_files = dict(BASE_FILES)
        expected_files["pkg/calc.py"] = blobs.read(dict(capture.corpus_blobs)["pkg/calc.py"])
        assert capture.manifest_id == reference_id("tree-v1", reference_entries(expected_files))
        payload = {key: capture.to_dict()[key] for key in
                   ("base_revision", "image_digest", "base_manifest_id", "manifest_id", "rules_id")}
        assert capture.candidate_id == reference_id("candidate-v1", payload)
        expected_corpus = {"corpus_rules_id": reference_id("corpus-rules-v1", FIXTURE["CORPUS_RULES"]),
                           "files": [{"path": e["path"], "sha256": e["sha256"],
                                      "kind": "test" if e["path"].startswith("tests/") else "source"}
                                     for e in reference_entries(expected_files) if e["path"].endswith(".py")]}
        assert corpus(capture) == reference_id("corpus-v1", expected_corpus)
        assert index(capture) == reference_id("index-v1", dict(corpus_id=corpus(capture),
                                                               **FIXTURE["INDEX_CONFIG"]))
    assert len({c.candidate_id for c in captures.values()}) == 3
    assert captures["C0"].changed == captures["C0"].deleted == ()
    assert captures["C0"].admissible  # Valid empty patch is a successful capture.


def test_non_python_changes_candidate_but_not_corpus_or_index():
    c0, _ = build_capture()
    files = dict(BASE_FILES, **{"data/rules.json": b'{"operation":"subtract"}\n'})
    changed, _ = build_capture(files, violations=[{"reason": "unsupported_change", "path": "data/rules.json"}])
    assert c0.candidate_id != changed.candidate_id
    assert corpus(c0) == corpus(changed)
    assert index(c0) == index(changed)
    assert not changed.admissible
    changed.validate_against_base(c0.entries)


def test_order_stability_and_audit_does_not_change_content_identity():
    capture, _ = build_capture()
    shuffled, _ = build_capture(dict(reversed(list(BASE_FILES.items()))))
    assert capture.to_json() == shuffled.to_json()
    altered = replace(capture, capture_id="12345678-1234-4234-8234-123456789abc",
                      captured_at="2026-10-31T01:00:00+08:00", origin={"container": "another"})
    assert altered.candidate_id == capture.candidate_id
    assert altered.to_json() != capture.to_json()
    assert rules_id({"b": [2, 1], "a": {"x": True}}) == rules_id({"a": {"x": True}, "b": [2, 1]})
    assert tree_id(reversed(capture.entries)) == capture.manifest_id
    selected = [(entry, "source") for entry in capture.entries if entry.path.endswith(".py")]
    rule = corpus_rules_id(FIXTURE["CORPUS_RULES"])
    assert corpus_id(selected, corpus_rules_id=rule) == corpus_id(reversed(selected), corpus_rules_id=rule)
    moved = replace(capture, corpus_blobs=tuple(
        (path, replace(ref, key="another/" + ref.sha256)) for path, ref in capture.corpus_blobs),
        barrier_receipt=replace(capture.barrier_receipt, key="another/barrier"))
    assert moved.candidate_id == capture.candidate_id
    assert corpus(moved) == corpus(capture)


def test_path_mode_base_image_and_policy_change_candidate():
    capture, _ = build_capture()
    payload = {key: getattr(capture, key) for key in
               ("base_revision", "image_digest", "base_manifest_id", "manifest_id", "rules_id")}
    for field, value in {"base_revision": "new-base", "image_digest": "sha256:" + "2" * 64,
                         "base_manifest_id": tree_id(()), "rules_id": rules_id({"version": 2})}.items():
        assert candidate_id(**(payload | {field: value})) != capture.candidate_id
    mode_capture, _ = build_capture(modes={"pkg/calc.py": 0o755},
                                     violations=[{"reason": "unsupported_mode_change"}])
    assert mode_capture.candidate_id != capture.candidate_id
    assert corpus(mode_capture) == corpus(capture)
    mode_capture.validate_against_base(capture.entries)
    with pytest.raises(ContractError, match="mode"):
        replace(mode_capture, admissible=True, violations=()).validate_against_base(capture.entries)
    files = dict(BASE_FILES)
    files["pkg/renamed.py"] = files.pop("pkg/calc.py")
    renamed, _ = build_capture(files)
    assert renamed.deleted == ("pkg/calc.py",)
    assert [path for path, _ in renamed.changed] == ["pkg/renamed.py"]
    assert renamed.candidate_id != capture.candidate_id
    assert corpus(renamed) != corpus(capture)
    renamed.validate_against_base(capture.entries)


def test_identity_config_changes_and_validation_bindings():
    capture, _ = build_capture()
    for name, value in {"chunker": {"version": 2}, "tokenizer": {"version": 2},
                        "encoder": {"revision": "2"}, "bm25": {"k1": "1.6"},
                        "rrf": {"k": 61}, "mode": "bm25"}.items():
        assert index(capture, **{name: value}) != index(capture)
    assert corpus_rules_id({"version": 2}) != corpus_rules_id(FIXTURE["CORPUS_RULES"])
    with pytest.raises(ContractError, match="floats"):
        index(capture, rrf={"weights": [0.5, 1]})
    with pytest.raises(ContractError):
        content_id("environment-v1", {"cpu": 0.5})
    payload = dict(candidate_id=capture.candidate_id, check_id=content_id("check-v1", {"selectors": ["a"]}),
                   environment_id=content_id("environment-v1", {"cpus": "0.5"}))
    assert validation_key(**payload) == reference_id("validation-v1", payload)
    for name, value in {"candidate_id": "candidate-v1:" + "a" * 64,
                        "check_id": content_id("check-v1", {"selectors": ["b"]}),
                        "environment_id": content_id("environment-v1", {"cpus": "1"})}.items():
        assert validation_key(**(payload | {name: value})) != validation_key(**payload)


def test_deep_immutability_and_detached_json_roundtrip():
    original, _ = build_capture()
    raw = original.to_dict()
    raw["origin"] = {"nested": {"array": [1, {"value": "before"}]}}
    raw["excluded"] = [{"rule": {"names": ["cache"]}}]
    raw["admissible"] = False
    raw["violations"] = [{"reason": "unsupported_change", "details": {"paths": ["x"]}}]
    capture = WorkspaceCapture.from_dict(raw)
    wire = capture.to_json()
    raw["origin"]["nested"]["array"][1]["value"] = "after"
    raw["entries"].clear()
    raw["excluded"][0]["rule"]["names"].append("other")
    raw["violations"][0]["details"]["paths"].append("y")
    assert capture.to_json() == wire
    with pytest.raises(TypeError):
        capture.origin["nested"]["array"][1]["value"] = "after"
    with pytest.raises(FrozenInstanceError):
        capture.entries[0].mode = 0o755
    detached = capture.to_dict()
    detached["origin"]["nested"]["array"].clear()
    assert capture.to_json() == wire
    restored = WorkspaceCapture.from_json(wire)
    assert restored == capture
    assert restored.to_json() == wire
    assert WorkspaceCapture.from_dict(json.loads(wire)) == capture


def test_deleted_changed_and_manifest_consistency():
    captures, _ = build_sequence()
    base, changed = captures["C0"], captures["C1"]
    with pytest.raises(ContractError, match="deleted"):
        replace(changed, deleted=("pkg/calc.py",))
    with pytest.raises(ContractError, match="duplicate"):
        replace(changed, changed=changed.changed * 2)
    with pytest.raises(ContractError, match="reference"):
        replace(changed, changed=(("missing.py", changed.changed[0][1]),))
    with pytest.raises(ContractError, match="reference"):
        replace(changed, changed=(("pkg/calc.py", replace(changed.changed[0][1], size=999)),))
    with pytest.raises(ContractError, match="complete"):
        replace(changed, changed=()).validate_against_base(base.entries)
    with pytest.raises(ContractError, match="complete"):
        replace(base, deleted=("missing.py",)).validate_against_base(base.entries)
    with pytest.raises(ContractError, match="base_manifest"):
        changed.validate_against_base(())
    with pytest.raises(ContractError, match="manifest_id"):
        replace(changed, manifest_id=tree_id(()))
    with pytest.raises(ContractError, match="candidate_id"):
        replace(changed, candidate_id=base.candidate_id)


def test_corrupt_blob_size_sha_and_barrier():
    captures, blobs = build_sequence()
    capture = captures["C2"]
    ref = dict(capture.changed)["pkg/calc.py"]
    original = blobs.blobs[ref.key]
    blobs.blobs[ref.key] = original + b"x"
    with pytest.raises(ContractError, match="size"):
        capture.validate_blobs(blobs)
    blobs.blobs[ref.key] = b"x" * len(original)
    with pytest.raises(ContractError, match="sha256"):
        read_verified_blob(blobs, ref)
    blobs.blobs[ref.key] = original
    blobs.blobs[capture.barrier_receipt.key] = b""
    with pytest.raises(ContractError, match="size"):
        capture.validate_blobs(blobs)


@pytest.mark.parametrize("key", ["../blob", "/tmp/blob", "C:/blob", "file:blob", "x\\blob"])
def test_artifact_keys_are_controlled_relative_keys(key):
    with pytest.raises(ContractError):
        ArtifactRef(blob_sha256(b""), 0, key)


def test_strict_json_and_capture_rejection():
    capture, _ = build_capture()
    for raw in ({}, capture.to_dict() | {"extra": True},
                capture.to_dict() | {"schema_version": "workspace-v2"},
                capture.to_dict() | {"capture_id": "not-a-uuid"},
                capture.to_dict() | {"admissible": False},
                capture.to_dict() | {"violations": [{"reason": "bad"}]},
                capture.to_dict() | {"origin": {"cpus": 0.5}}):
        with pytest.raises(ContractError):
            WorkspaceCapture.from_dict(raw)
    with pytest.raises(ContractError):
        WorkspaceCapture.from_json('{"schema_version":"workspace-v1","schema_version":"other"}')
    with pytest.raises(ContractError):
        WorkspaceCapture.from_json(capture.to_json().decode().encode("utf-16"))


@pytest.mark.parametrize("field", ["entries", "changed", "deleted", "corpus_blobs", "excluded", "violations"])
@pytest.mark.parametrize("value", [None, "x", {}])
def test_json_collection_fields_require_arrays(field, value):
    capture, _ = build_capture()
    with pytest.raises(ContractError):
        WorkspaceCapture.from_json(json.dumps(capture.to_dict() | {field: value}))


def test_no_redundant_unchanged_delta_or_missing_deletions():
    base, _ = build_capture()
    with pytest.raises(ContractError, match="complete"):
        replace(base, changed=base.corpus_blobs[:1]).validate_against_base(base.entries)
    files = dict(BASE_FILES)
    del files["pkg/calc.py"]
    deleted, _ = build_capture(files)
    deleted.validate_against_base(base.entries)
    with pytest.raises(ContractError, match="complete"):
        replace(deleted, deleted=()).validate_against_base(base.entries)


def test_corpus_subset_kinds_and_reference_membership():
    capture, _ = build_capture()
    py = next(entry for entry in capture.entries if entry.path.endswith(".py"))
    resource = next(entry for entry in capture.entries if not entry.path.endswith(".py"))
    rule = corpus_rules_id(FIXTURE["CORPUS_RULES"])
    assert corpus_id([(py, "source")], corpus_rules_id=rule) != corpus_id([(py, "test_support")], corpus_rules_id=rule)
    for files in ([(py, "regular")], [(resource, "source")], [(py, "source"), (py, "source")]):
        with pytest.raises(ContractError):
            corpus_id(files, corpus_rules_id=rule)
    ref = ArtifactRef(resource.sha256, resource.size, "blobs/resource")
    with pytest.raises(ContractError, match="Python"):
        replace(capture, corpus_blobs=((resource.path, ref),))
