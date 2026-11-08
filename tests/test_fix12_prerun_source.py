"""The pre-run source check of a rebench task applies the ingest rule (R7): confirmed export-subst placeholders."""
import pytest

import scripts.build_clean_baseline as baseline
import scripts.rebench.provenance as provenance
import scripts.rewritten_sources as rewritten
import scripts.run_swe_task as rst
import src.eval.a5_image_tree as image_tree


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
