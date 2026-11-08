import json

from scripts.run_a5_main import halt_reason
from src.eval.candidate_error import (
    classify_missing,
    classify_timeout,
    expected_empty,
    grader_candidate_error,
    innermost_project_frame,
    regrade_verdict,
)
from src.eval.image_grader import receipt_diagnostics

DJANGO_RUNNER_ERROR = """Traceback (most recent call last):
  File "/tmp/a5-runner.py", line 92, in <module>
    runpy.run_path('tests/runtests.py', run_name='__main__')
  File "<frozen runpy>", line 291, in run_path
  File "tests/runtests.py", line 770, in <module>
    failures = django_tests(
  File "/workspace/django/test/runner.py", line 1031, in run_tests
    suite = self.build_suite(test_labels)
  File "/usr/local/lib/python3.11/unittest/loader.py", line 162, in loadTestsFromName
    module = __import__(module_name)
  File "/workspace/tests/admin_views/test_templatetags.py", line 4, in <module>
    from django.contrib.admin.templatetags.admin_list import date_hierarchy
  File "/workspace/django/contrib/admin/templatetags/admin_list.py", line 11, in <module>
    from django.contrib.admin.views.main import (
  File "/workspace/django/contrib/admin/views/main.py", line 273
    if v is None:
IndentationError: unexpected indent
"""

PYTEST_COLLECTION = """ImportError while importing test module '/workspace/testing/test_foo.py'.
Traceback:
/usr/local/lib/python3.9/importlib/__init__.py:127: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
testing/test_foo.py:4: in <module>
    from _pytest.foo import bar
E     File "/workspace/src/_pytest/foo.py", line 12
E       def bar(:
E               ^
E   SyntaxError: invalid syntax
"""


def _evaluation(**fields):
    base = {"status": "fail", "runner_error": None, "collection_errors": [], "collection_error_tails": []}
    base.update(fields)
    return base


def _summary(root, task_id, **fields):
    path = root / task_id / "b0" / "r1" / "summary.json"
    path.parent.mkdir(parents=True)
    row = {"task_id": task_id, "variant": "B0", "solved": False, "status": "fail", "cost_cny": "0",
           "grader_ids": {"f2p": 1, "p2p": 6, "missing": 7, "reason": None}}
    row.update(fields)
    path.write_text(json.dumps(row), encoding="utf-8")


def test_innermost_project_frame_skips_runner_stdlib_and_frozen_frames():
    assert innermost_project_frame(DJANGO_RUNNER_ERROR) == "django/contrib/admin/views/main.py"
    assert innermost_project_frame(PYTEST_COLLECTION) == "src/_pytest/foo.py"
    assert innermost_project_frame('File "/tmp/a5-runner.py", line 1\nFile "<frozen runpy>", line 2') is None


def test_candidate_indentation_error_is_an_ordinary_failure(tmp_path):
    error = grader_candidate_error(_evaluation(runner_error=DJANGO_RUNNER_ERROR), ["django/contrib/admin/views/main.py"])
    assert error == {"source": "runner_error", "file": "django/contrib/admin/views/main.py"}
    _summary(tmp_path, "django__django-16527", grader_candidate_error=error)
    assert halt_reason(tmp_path, "b0") is None


def test_empty_candidate_with_missing_ids_still_stops(tmp_path):
    assert grader_candidate_error(_evaluation(runner_error=DJANGO_RUNNER_ERROR), []) is None
    _summary(tmp_path, "django__django-16527", grader_candidate_error=None)
    assert halt_reason(tmp_path, "b0") == "grader reported missing ids: django__django-16527"


def test_error_in_a_file_the_candidate_did_not_change_still_stops():
    assert grader_candidate_error(_evaluation(runner_error=DJANGO_RUNNER_ERROR), ["django/contrib/admin/options.py"]) is None


def test_pytest_collection_traceback_counts():
    evaluation = _evaluation(collection_error_tails=[PYTEST_COLLECTION])
    assert grader_candidate_error(evaluation, ["src/_pytest/foo.py"]) == {"source": "collection_errors[0]", "file": "src/_pytest/foo.py"}


SETUPCLASS_STDERR = """ERROR: setUpClass (many_to_one.tests.ManyToOneTests)
Traceback (most recent call last):
  File "/workspace/tests/many_to_one/tests.py", line 26, in setUpTestData
    cls.a.save()
  File "/workspace/django/db/models/base.py", line 828, in _save_table
    raise ValueError("Cannot force an update in save() with no primary key.")
"""
CALIBRATION = {"empty_f2p": [0, 1], "empty_p2p": [36, 36], "p2p_excluded": []}
CHANGED = ["django/db/models/fields/related_descriptors.py", "tests/many_to_one/tests.py"]


def _missing_run():
    cases = [{"nodeid": "f", "outcome": "missing"}] + [{"nodeid": "p" + str(i), "outcome": "missing"} for i in range(36)]
    return _evaluation(execution_stderr_tail=SETUPCLASS_STDERR, cases=cases, f2p_passed=0, f2p_total=1, p2p_passed=0, p2p_total=36)


def _regrade(missing=0, p2p_passed=36, reason=None):
    cases = [{"nodeid": "f", "outcome": "failed"}] + [
        {"nodeid": "p" + str(i), "outcome": "missing" if i < missing else "passed"} for i in range(36)]
    return {"status": "fail", "reason": reason, "cases": cases, "f2p_passed": 0, "f2p_total": 1, "p2p_passed": p2p_passed, "p2p_total": 36}


def test_expected_empty_follows_the_p2p_excluded_reading():
    assert expected_empty(CALIBRATION) == {"f2p": [0, 1], "p2p": [36, 36]}
    sphinx = {"empty_f2p": [0, 1], "empty_p2p": [43, 44], "p2p_excluded": ["tests/test_intl.py::test_text_docfields"]}
    assert expected_empty(sphinx) == {"f2p": [0, 1], "p2p": [43, 43]}
    assert expected_empty(None) is None


def test_execution_missing_with_a_clean_empty_regrade_is_an_ordinary_failure(tmp_path):
    evaluation = _missing_run()
    assert grader_candidate_error(evaluation, CHANGED) is None
    verdict = regrade_verdict(_regrade(), expected_empty(CALIBRATION))
    assert verdict["consistent"] and verdict["missing"] == 0
    error = classify_missing(evaluation, CHANGED, verdict)
    assert error == "execution"
    _summary(tmp_path, "django__django-13964", grader_candidate_error=error, missing_regrade=verdict)
    assert halt_reason(tmp_path, "b0") is None


def test_execution_missing_counts_for_b2_and_the_drift_slot(tmp_path):
    verdict = regrade_verdict(_regrade(), expected_empty(CALIBRATION))
    for phase, slot in (("b2", "r3"), ("b0", "r4")):
        path = tmp_path / "django__django-13964" / phase / slot / "summary.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"task_id": "django__django-13964", "variant": phase.upper(), "solved": False, "status": "fail",
                                    "cost_cny": "0", "grader_ids": {"f2p": 1, "p2p": 36, "missing": 37, "reason": None},
                                    "grader_candidate_error": classify_missing(_missing_run(), CHANGED, verdict)}), encoding="utf-8")
    assert halt_reason(tmp_path, "b2", extra_phases=("b0",)) is None


def test_missing_in_the_empty_regrade_still_stops(tmp_path):
    verdict = regrade_verdict(_regrade(missing=3, p2p_passed=33), expected_empty(CALIBRATION))
    assert not verdict["consistent"] and verdict["missing"] == 3
    error = classify_missing(_missing_run(), CHANGED, verdict)
    assert error is None
    path = tmp_path / "django__django-13964" / "b2" / "r3" / "summary.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"task_id": "django__django-13964", "variant": "B2", "solved": False, "status": "fail", "cost_cny": "0",
                                "grader_ids": {"f2p": 1, "p2p": 36, "missing": 37, "reason": None},
                                "grader_candidate_error": error, "missing_regrade": verdict}), encoding="utf-8")
    assert halt_reason(tmp_path, "b2") == "grader reported missing ids: django__django-13964"


def test_an_empty_regrade_that_differs_from_calibration_or_crashes_still_stops():
    expected = expected_empty(CALIBRATION)
    assert classify_missing(_missing_run(), CHANGED, regrade_verdict(_regrade(p2p_passed=35), expected)) is None
    assert classify_missing(_missing_run(), CHANGED, regrade_verdict(_regrade(reason="TimeoutError"), expected)) is None
    assert classify_missing(_missing_run(), CHANGED, regrade_verdict(_regrade(), None)) is None
    assert classify_missing(_missing_run(), CHANGED, None) is None


def test_the_traceback_rule_still_comes_first_and_no_missing_needs_nothing():
    evaluation = _evaluation(runner_error=DJANGO_RUNNER_ERROR, cases=[{"nodeid": "x", "outcome": "missing"}])
    stale = regrade_verdict(_regrade(missing=1, p2p_passed=35), expected_empty(CALIBRATION))
    assert classify_missing(evaluation, ["django/contrib/admin/views/main.py"], stale) == {
        "source": "runner_error", "file": "django/contrib/admin/views/main.py"}
    assert classify_missing(_evaluation(cases=[{"nodeid": "x", "outcome": "failed"}]), CHANGED, None) is None


INTERFACE_CALIBRATION = {"empty_f2p": [0, 2], "empty_p2p": [3, 3], "p2p_excluded": [], "empty_missing": ["f1", "f0"]}


def _interface_regrade(missing=("f0", "f1")):
    cases = [{"nodeid": n, "outcome": "missing" if n in missing else "failed"} for n in ("f0", "f1")] + [
        {"nodeid": "p" + str(i), "outcome": "missing" if "p" + str(i) in missing else "passed"} for i in range(3)]
    passed = sum(1 for i in range(3) if "p" + str(i) not in missing)
    return {"status": "fail", "reason": None, "cases": cases, "f2p_passed": 0, "f2p_total": 2, "p2p_passed": passed, "p2p_total": 3}


def test_an_empty_regrade_reproducing_the_calibrated_missing_ids_is_consistent(tmp_path):
    expected = expected_empty(INTERFACE_CALIBRATION)
    assert expected == {"f2p": [0, 2], "p2p": [3, 3], "missing": ["f0", "f1"]}
    evaluation = _evaluation(cases=[{"nodeid": "f0", "outcome": "missing"}, {"nodeid": "f1", "outcome": "missing"}])
    verdict = regrade_verdict(_interface_regrade(), expected)
    assert verdict["consistent"] and verdict["missing"] == 2
    assert classify_missing(evaluation, CHANGED, verdict) == "execution"
    _summary(tmp_path, "o__r-1_interface", grader_candidate_error="execution", missing_regrade=verdict)
    assert halt_reason(tmp_path, "b0") is None


def test_missing_ids_other_than_the_calibrated_ones_still_stop():
    expected = expected_empty(INTERFACE_CALIBRATION)
    evaluation = _evaluation(cases=[{"nodeid": "f0", "outcome": "missing"}])
    assert not regrade_verdict(_interface_regrade(missing=("f0",)), expected)["consistent"]
    assert not regrade_verdict(_interface_regrade(missing=("f0", "f1", "p0")), {**expected, "p2p": [2, 3]})["consistent"]
    assert classify_missing(evaluation, CHANGED, regrade_verdict(_interface_regrade(missing=("f0",)), expected)) is None


SPHINX_CALIBRATION = {"empty_f2p": [0, 1], "empty_p2p": [24, 24], "p2p_excluded": []}
RECEIPT_GONE = "DockerUnavailable: Docker exec failed after 3 retries: FileNotFoundError: /tmp/a5-pytest-receipt.json"


def _timed_out_run(reason=RECEIPT_GONE, execution_status="timed_out"):
    return {"status": "invalid_env", "solved": False, "reason": reason, "execution_status": execution_status}


def _sphinx_regrade(p2p_passed=24, reason=None):
    cases = [{"nodeid": "f", "outcome": "failed"}] + [
        {"nodeid": "p" + str(i), "outcome": "passed" if i < p2p_passed else "failed"} for i in range(24)]
    return {"status": "fail", "reason": reason, "cases": cases, "f2p_passed": 0, "f2p_total": 1, "p2p_passed": p2p_passed, "p2p_total": 24}


def _timeout_summary(root, phase, slot, error, verdict):
    path = root / "sphinx-doc__sphinx-7590" / phase / slot / "summary.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"task_id": "sphinx-doc__sphinx-7590", "variant": phase.upper(), "solved": False,
                                "status": "fail" if error else "invalid_env", "grader_status": "invalid_env", "cost_cny": "0",
                                "grader_ids": {"f2p": 1, "p2p": 24, "missing": 0, "reason": RECEIPT_GONE},
                                "grader_candidate_error": error, "timeout_regrade": verdict}), encoding="utf-8")


def test_timeout_with_a_clean_empty_regrade_is_an_ordinary_failure_in_b2_and_the_drift_slot(tmp_path):
    verdict = regrade_verdict(_sphinx_regrade(), expected_empty(SPHINX_CALIBRATION))
    assert verdict["consistent"]
    error = classify_timeout(_timed_out_run(), verdict)
    assert error == "execution_timeout"
    _timeout_summary(tmp_path, "b2", "r3", error, verdict)
    _timeout_summary(tmp_path, "b0", "r4", error, verdict)
    assert halt_reason(tmp_path, "b2", extra_phases=("b0",)) is None


def test_timeout_with_a_bad_empty_regrade_still_stops(tmp_path):
    expected = expected_empty(SPHINX_CALIBRATION)
    assert classify_timeout(_timed_out_run(), regrade_verdict(_sphinx_regrade(p2p_passed=23), expected)) is None
    assert classify_timeout(_timed_out_run(), regrade_verdict(_sphinx_regrade(reason="TimeoutError"), expected)) is None
    assert classify_timeout(_timed_out_run(), regrade_verdict(_sphinx_regrade(), None)) is None
    assert classify_timeout(_timed_out_run(), None) is None
    _timeout_summary(tmp_path, "b2", "r3", None, regrade_verdict(_sphinx_regrade(p2p_passed=23), expected))
    assert halt_reason(tmp_path, "b2").startswith("grader crashed: sphinx-doc__sphinx-7590")


def test_a_grader_reason_without_an_execution_timeout_still_stops():
    verdict = regrade_verdict(_sphinx_regrade(), expected_empty(SPHINX_CALIBRATION))
    assert classify_timeout(_timed_out_run(execution_status="completed"), verdict) is None
    assert classify_timeout(_timed_out_run(execution_status=None), verdict) is None
    assert classify_timeout(_timed_out_run(reason=None), verdict) is None


def test_receipt_diagnostics_keep_the_end_of_a_long_traceback():
    detail = "x" * 5000 + PYTEST_COLLECTION
    diagnostics = receipt_diagnostics({"collection_errors": [{"nodeid": "testing/test_foo.py", "detail": detail}]})
    assert len(diagnostics["collection_errors"][0]) == 2048
    assert diagnostics["collection_error_tails"][0].endswith("SyntaxError: invalid syntax\n")
    assert grader_candidate_error(_evaluation(**diagnostics), ["src/_pytest/foo.py"])["file"] == "src/_pytest/foo.py"
