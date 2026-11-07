"""SWE-rebench family in run_swe_task and the batch prepare script. No Docker, no model."""
import json

import pytest
import yaml

from scripts import run_swe_task as rst
from scripts.rebench import prepare


# ---- run_swe_task ----

def test_family_tables():
    assert rst.TEST_COMMANDS["rebench"] == "python -m pytest <test path>"
    assert rst.PYTHON_PATHS["rebench"] == (".", "src")


def test_grader_options_only_for_tasks_that_declare_them():
    assert rst.grader_options({"id": "x", "family": "django"}) == {}
    task = {"grader": {"autoload_plugins": True, "pytest_options": ["-p", "no:cacheprovider"]}}
    assert rst.grader_options(task) == {"autoload_plugins": True, "pytest_options": ["-p", "no:cacheprovider"]}
    with pytest.raises(ValueError):
        rst.grader_options({"grader": {"autoload_plugins": True, "network": "bridge"}})


async def test_empty_regrade_passes_the_grader_options(monkeypatch):
    seen = {}

    class FakeGrader:
        async def evaluate(self, **kwargs):
            seen.update(kwargs)
            return {"status": "fail"}
    monkeypatch.setattr(rst, "ImageGrader", FakeGrader)
    spec = type("Spec", (), {"test_overlay": {}, "baseline_passed": ("t::p",)})()
    task = {"family": "rebench", "grader_timeout_seconds": 60, "grader": {"autoload_plugins": True, "pytest_options": ["-x"]}}
    await rst.empty_regrade(task, "img@sha256:" + "0" * 64, spec, ["t::f"])
    assert seen["autoload_plugins"] is True and seen["pytest_options"] == ["-x"]
    seen.clear()
    await rst.empty_regrade({"family": "sympy", "grader_timeout_seconds": 60}, "img", spec, ["t::f"])
    assert "autoload_plugins" not in seen and "pytest_options" not in seen


def test_public_issue_uses_the_task_test_command(tmp_path):
    public = tmp_path / "public.json"
    public.write_text(json.dumps({"issue_text": "bug", "repo_ref": {"repo_id": "o/r", "revision": "abc"}}))
    task = {"family": "rebench", "public_task": str(public)}
    assert "`python -m pytest <test path>`" in rst.public_issue(task)
    task["test_command"] = "python -m pytest -p no:cacheprovider <test path>"
    assert "`python -m pytest -p no:cacheprovider <test path>`" in rst.public_issue(task)


def test_resolve_image_reads_the_task_clean_record(tmp_path):
    record = tmp_path / "clean.json"
    record.write_text(json.dumps({"answer_isolation_pass": True, "clean_image_digest": "img@sha256:" + "1" * 64}))
    image, clean = rst.resolve_image({"id": "t", "clean_record": str(record)}, {}, legacy_image=False, mock=True)
    assert image == "img@sha256:" + "1" * 64 and clean["answer_isolation_pass"] is True


def make_task(tmp_path):
    original = tmp_path / "staging" / "original"
    baseline = tmp_path / "staging" / "baseline"
    for root, text in ((original, "def test_a(): pass\n"), (baseline, "def test_a(): pass\ndef test_b(): pass\n")):
        (root / "tests").mkdir(parents=True)
        (root / "tests" / "test_x.py").write_text(text)
    private = tmp_path / "private.json"
    private.write_text(json.dumps({"instance_id": "o__r-1", "FAIL_TO_PASS": "[]", "PASS_TO_PASS": "[]"}))
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"baseline_image_digest": "img@sha256:" + "2" * 64, "private_source_binding": {
        "path": str(private), "test_patch_paths": ["tests/test_x.py"], "gold_patch_paths": ["r/x.py"]}}))
    return {"id": "o__r-1", "family": "rebench", "receipt": str(receipt), "original": str(original),
            "baseline": str(baseline), "grader_timeout_seconds": 600,
            "grader_nodeids": {"f2p": ["tests/test_x.py::test_b"], "p2p": ["tests/test_x.py::test_a", "tests/test_x.py::flaky"]},
            "p2p_excluded": ["tests/test_x.py::flaky"]}


def test_grader_spec_for_a_rebench_task(tmp_path):
    spec = rst.grader_spec(make_task(tmp_path))
    assert spec.python_paths == (".", "src")
    assert spec.expected_nodeids == ("tests/test_x.py::test_b", "tests/test_x.py::test_a")
    assert spec.baseline_passed == ("tests/test_x.py::test_a",)
    assert list(spec.test_overlay) == ["tests/test_x.py"]


# ---- prepare: resumable stages ----

def test_stages_resume_from_the_first_unfinished_one(tmp_path):
    calls = []

    def stage(name, fail=False):
        def run(state):
            calls.append(name)
            if fail:
                raise RuntimeError("boom")
            return {name: True}
        return name, run
    path = tmp_path / "state.json"
    with pytest.raises(RuntimeError):
        prepare.run_stages(path, [stage("a"), stage("b", fail=True), stage("c")])
    assert calls == ["a", "b"]
    saved = json.loads(path.read_text())
    assert saved["done"] == ["a"] and saved["error"]["stage"] == "b"
    calls.clear()
    state = prepare.run_stages(path, [stage("a"), stage("b"), stage("c")])
    assert calls == ["b", "c"] and state["done"] == ["a", "b", "c"] and "error" not in state
    calls.clear()
    prepare.run_stages(path, [stage("a"), stage("b"), stage("c")])
    assert calls == []


def test_stage_can_be_invalidated(tmp_path):
    path = tmp_path / "state.json"
    calls = []
    stages = [("a", lambda s: calls.append("a") or {}), ("b", lambda s: calls.append("b") or {})]
    prepare.run_stages(path, stages)
    calls.clear()
    prepare.run_stages(path, stages, redo={"a"})
    assert calls == ["a", "b"]


# ---- prepare: digest pinning ----

def test_pinned_requires_a_sha256_digest():
    ref = "swerebench/x@sha256:" + "a" * 64
    assert prepare.pinned(ref) == ref
    for bad in ("swerebench/x:latest", "swerebench/x@sha256:abc", "", None):
        with pytest.raises(ValueError):
            prepare.pinned(bad)


def test_official_digest_matches_the_repository(monkeypatch):
    inspect = {"RepoDigests": ["other/y@sha256:" + "b" * 64, "swerebench/x@sha256:" + "a" * 64], "Size": 5}
    monkeypatch.setattr(prepare, "docker", lambda *a, **k: json.dumps([inspect]))
    assert prepare.official_digest("swerebench/x:latest")["digest"] == "swerebench/x@sha256:" + "a" * 64
    inspect["RepoDigests"] = []
    with pytest.raises(ValueError):
        prepare.official_digest("swerebench/x:latest")


# ---- prepare: outputs ----

RECORD = {
    "instance_id": "o__r-1", "repo": "o/r", "created_at": "2026-01-01T00:00:00+00:00", "base_commit": "abc",
    "test_cmd": "pytest -p no:cacheprovider -rA tests/test_x.py",
    "task_files": {"public": "runs/rebench/public/o__r-1.json", "private": "runs/rebench/private/o__r-1.json"},
    "image": {"official_digest": "swerebench/x@sha256:" + "a" * 64, "clean_digest": "c@sha256:" + "c" * 64,
              "clean_id": "sha256:" + "c" * 64},
    "isolation": {"answer_isolation_pass": True, "source_only_match": True, "source_hash": "h",
                  "filesystem_scan": {"pass": True}, "layout": {"natives": []}},
    "calibration": {"options": ["-p", "no:cacheprovider"], "f2p": ["t::f"], "p2p": ["t::a", "t::b"],
                    "p2p_excluded": ["t::b"], "accepted": True,
                    "summary": {"empty_f2p": [0, 1], "gold_f2p": [1, 1], "gold_p2p": [1, 2]},
                    "test_paths": ["tests/test_x.py"], "gold_paths": ["r/x.py"]},
}


def test_manifest_row():
    row = prepare.manifest_row(RECORD)
    assert row["id"] == "o__r-1" and row["family"] == "rebench"
    assert row["clean_record"] == "docs/rebench/clean-images/o__r-1.json"
    assert row["receipt"] == "runs/rebench/work/o__r-1/receipt.json"
    assert row["original"] == "runs/rebench/work/o__r-1/staging/original"
    assert row["baseline"] == "runs/rebench/work/o__r-1/staging/baseline"
    assert row["public_task"].endswith("o__r-1.json")
    assert row["grader"] == {"autoload_plugins": True, "pytest_options": ["-p", "no:cacheprovider"]}
    assert row["test_command"] == "python -m pytest -p no:cacheprovider <test path>"
    assert row["grader_nodeids"] == {"f2p": ["t::f"], "p2p": ["t::a", "t::b"]}
    assert row["p2p_excluded"] == ["t::b"]
    assert row["official_image_digest"].endswith("a" * 64)


def test_clean_record_and_receipt():
    clean = prepare.clean_record(RECORD)
    assert clean["task_id"] == "o__r-1" and clean["answer_isolation_pass"] is True
    assert clean["clean_image_digest"] == "c@sha256:" + "c" * 64
    receipt = prepare.receipt(RECORD, private_sha256="p" * 64)
    binding = receipt["private_source_binding"]
    assert receipt["baseline_image_digest"] == "c@sha256:" + "c" * 64 and receipt["base_revision"] == "abc"
    assert binding["test_patch_paths"] == ["tests/test_x.py"] and binding["gold_patch_paths"] == ["r/x.py"]
    assert binding["path"].endswith("o__r-1.json") and binding["sha256"] == "p" * 64
    assert receipt["source_attempt_ref"].endswith("source-receipt.json")
    assert receipt["model_calls"] == 0 and receipt["paid"] is False


def test_manifest_merge_is_idempotent_and_sorted(tmp_path):
    path = tmp_path / "tasks.yaml"
    prepare.merge_manifest(path, [{"id": "b", "x": 1}])
    prepare.merge_manifest(path, [{"id": "a", "x": 1}, {"id": "b", "x": 2}])
    data = yaml.safe_load(path.read_text())
    assert [t["id"] for t in data["tasks"]] == ["a", "b"] and data["tasks"][1]["x"] == 2
    assert data["schema"] == "rebench.tasks.v1"


def test_rejected_task_is_removed_from_the_manifest(tmp_path):
    path = tmp_path / "tasks.yaml"
    prepare.merge_manifest(path, [{"id": "a"}, {"id": "b"}])
    prepare.merge_manifest(path, [], drop={"a"})
    assert [t["id"] for t in yaml.safe_load(path.read_text())["tasks"]] == ["b"]


# ---- gold mock edits ----

@pytest.mark.parametrize("original,new", [
    ("a\nb\nc\n", "a\nB\nc\n"),
    ("x\n" * 50 + "y\n" + "x\n" * 50, "x\n" * 50 + "z\n" + "x\n" * 50),
    ("def f():\n    return 1\n\ndef g():\n    return 1\n", "def f():\n    return 2\n\ndef g():\n    return 1\n\ndef h():\n    pass\n"),
    ("one\ntwo\nthree\n", "zero\none\nthree\nfour\n"),
    ("same\nsame\nsame\n", "same\nsame\n"),
])
def test_gold_edits_reproduce_the_gold_file(original, new):
    from scripts.rebench.mock_e2e import gold_edits
    text = original
    edits = gold_edits(original, new)
    for search, replace in edits:
        assert text.count(search) == 1
        text = text.replace(search, replace, 1)
    assert text == new
