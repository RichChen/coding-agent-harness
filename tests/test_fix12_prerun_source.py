"""Run-time checks of a rebench task follow its ingest: the source check applies R7 (confirmed export-subst
placeholders), and the empty-delta regrade compares against the ingest calibration."""
import json

import pytest

import scripts.build_clean_baseline as baseline
import scripts.rebench.provenance as provenance
import scripts.rewritten_sources as rewritten
import scripts.run_swe_task as rst
import src.eval.a5_image_tree as image_tree
from src.eval.candidate_error import expected_empty


@pytest.fixture
def checks(monkeypatch):
    calls = {"source_check": 0}
    state = {"exact": False, "excused": True}
    monkeypatch.setattr(rst, "grader_spec", lambda task: {"spec": True})
    monkeypatch.setattr(rst, "load_json", lambda path: {"source_attempt_ref": "work/source-receipt.json",
                                                         "base_revision": "abc"})
    monkeypatch.setattr(rst, "resolve_image", lambda task, receipt, **kw: ("img", {"answer_isolation_pass": True}))
    monkeypatch.setattr(image_tree, "archive_manifest", lambda archive, repo, rev: {"entries": {}})
    monkeypatch.setattr(rewritten, "overrides_for", lambda task, manifest: {})
    monkeypatch.setattr(baseline, "source_only_check", lambda image, manifest, overrides: (state["exact"], "h"))

    def source_check(image, archive, manifest):
        calls["source_check"] += 1
        return {"match": state["excused"]}

    monkeypatch.setattr(provenance, "source_check", source_check)
    return calls, state


def task(family):
    return {"id": "t", "repo": "o/r", "receipt": "receipt.json", "family": family}


def test_a_rebench_task_passes_with_a_confirmed_placeholder(checks):
    calls, _ = checks
    assert rst.prerun_check(task("rebench"), mock=False)[3] is None and calls["source_check"] == 1


def test_a_rebench_task_still_fails_on_any_other_mismatch(checks):
    calls, state = checks
    state["excused"] = False
    assert rst.prerun_check(task("rebench"), mock=False)[3] == "opt/repo source-only hash mismatch"


def test_other_families_keep_the_exact_check(checks):
    calls, _ = checks
    assert rst.prerun_check(task("a5"), mock=False)[3] == "opt/repo source-only hash mismatch"
    assert calls["source_check"] == 0


def test_an_exact_match_needs_no_second_check(checks):
    calls, state = checks
    state["exact"] = True
    assert rst.prerun_check(task("rebench"), mock=False)[3] is None and calls["source_check"] == 0


def _empty_run(*missing):
    return {"f2p_outcomes": {n: "missing" if n in missing else "failed" for n in ("a", "b", "c")}}


def test_rebench_calibration_reads_the_manifest_counts_and_the_ingest_missing_ids(tmp_path, monkeypatch):
    work = tmp_path / "runs" / "rebench" / "work" / "t"
    work.mkdir(parents=True)
    (work / "state.json").write_text(json.dumps({"calibration": {"runs": {"empty": [
        _empty_run("a", "b"), _empty_run("a", "b"), _empty_run("a")]}}}), encoding="utf-8")
    monkeypatch.setattr(rst, "ROOT", tmp_path)
    row = {"id": "t", "family": "rebench", "receipt": "runs/rebench/work/t/receipt.json", "p2p_excluded": ["x"],
           "calibration": {"empty_f2p": [0, 3], "empty_p2p": [4, 5], "gold_f2p": [3, 3], "gold_p2p": [4, 5]}}
    found = rst.empty_calibration(row)
    assert found == {"empty_f2p": [0, 3], "empty_p2p": [4, 5], "p2p_excluded": ["x"], "empty_missing": ["a"]}
    assert expected_empty(found) == {"f2p": [0, 3], "p2p": [4, 4], "missing": ["a"]}
    assert rst.empty_calibration({**row, "receipt": "runs/rebench/work/gone/receipt.json"}) is None


def test_other_families_keep_the_sprint_calibration(monkeypatch):
    monkeypatch.setattr(rst, "calibration_row", lambda task_id: {"from": task_id})
    assert rst.empty_calibration({"id": "django__django-1", "family": "django"}) == {"from": "django__django-1"}
