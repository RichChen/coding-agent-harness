"""Pilot ingestion: exclusion clauses, manifest rows and the replacement rule. No Docker."""
import pytest

from scripts.rebench.pilot_ingest import choose_replacement, ingest_plan, task_map
from scripts.rebench.prepare import failed_clauses, manifest_row


def runs(empty_f2p=(0, 2), gold_f2p=(2, 2), n=3):
    return {"empty": [{"f2p": list(empty_f2p)}] * n, "gold": [{"f2p": list(gold_f2p)}] * n}


def test_clean_task_fails_no_clause():
    assert failed_clauses(isolation_ok=True, runs=runs(), stable=True, repaired=True, excluded=0, p2p=10) == []


@pytest.mark.parametrize("kwargs, clause", [
    ({"isolation_ok": False}, 2),
    ({"runs": runs(gold_f2p=(1, 2))}, 3),
    ({"repaired": False}, 3),
    ({"runs": runs(empty_f2p=(1, 2))}, 4),
    ({"stable": False}, 5),
    ({"excluded": 6, "p2p": 10}, 8),
])
def test_each_design_clause(kwargs, clause):
    base = dict(isolation_ok=True, runs=runs(), stable=True, repaired=True, excluded=0, p2p=10)
    assert clause in failed_clauses(**{**base, **kwargs})


def test_half_of_p2p_excluded_is_still_accepted():
    assert failed_clauses(isolation_ok=True, runs=runs(), stable=True, repaired=True, excluded=5, p2p=10) == []


def task_row(issue, **fields):
    return {"instance_id": "o__r-1", "repo": "o/r", "base_commit": "abc", "problem_statement": issue,
            "FAIL_TO_PASS": '["t::a"]', "PASS_TO_PASS": "[]", "patch": "diff --git a/x b/x", "test_patch": "",
            **fields}


def test_empty_id_list_is_not_a_leak(tmp_path, monkeypatch):
    from scripts.rebench import prepare

    monkeypatch.setattr(prepare, "PUBLIC", tmp_path / "public")
    monkeypatch.setattr(prepare, "PRIVATE", tmp_path / "private")
    monkeypatch.setattr(prepare, "rel", str)
    prepare.write_task_files(task_row("calling f([]) raises"))
    with pytest.raises(ValueError, match="PASS_TO_PASS"):
        prepare.write_task_files(task_row('see ["t::b"]', PASS_TO_PASS='["t::b"]'))
    with pytest.raises(ValueError, match="patch"):
        prepare.write_task_files(task_row("apply diff --git a/x b/x"))


def test_calibration_collects_with_the_gold_patch(tmp_path, monkeypatch):
    """A test module that imports a name only the gold patch adds collects nothing without it."""
    from scripts.rebench import prepare
    from src.eval import image_grader

    gold = {"changed": {"pkg/new.py": "Z29sZA=="}, "deleted": []}
    calls = []

    class Grader:
        def __init__(self, **kwargs):
            pass

        async def collect(self, **kwargs):
            calls.append(kwargs)
            return {"collected": ["t.py::a"] if kwargs.get("delta") == gold else [], "reason": None}

        async def evaluate(self, **kwargs):
            outcome = "passed" if kwargs["delta"] == gold else "failed"
            return {"status": "pass" if outcome == "passed" else "fail", "reason": None, "execution_status": "completed",
                    "cases": [{"nodeid": "t.py::a", "outcome": outcome}]}

    monkeypatch.setattr(image_grader, "ImageGrader", Grader)
    monkeypatch.setattr(prepare, "staging", lambda row, work: ({"t.py": "dA=="}, gold, ["t.py"], ["pkg/new.py"]))
    row = {"FAIL_TO_PASS": '["t.py::a"]', "PASS_TO_PASS": "[]", "install_config": {"test_cmd": "pytest -p no:cacheprovider"}}
    state = {"image": {"clean_digest": "img@sha256:" + "0" * 64}, "isolation": {"answer_isolation_pass": True}}
    result = prepare.calibration_stage(row, tmp_path, 3, True)(state)["calibration"]
    assert calls[0]["delta"] == gold and calls[0]["keep_original_files"] is True
    assert result["failed_clauses"] == [] and result["f2p"] == ["t.py::a"]


def record(keep=True, memory=None):
    cal = {"options": ["-p", "no:cacheprovider"], "f2p": ["t::a"], "p2p": ["t::b"], "p2p_excluded": [],
           "summary": {"gold_f2p": [1, 1]}, "keep_original_files": keep}
    digest = "x@sha256:" + "0" * 64
    return {"instance_id": "o__r-1", "repo": "o/r", "base_commit": "abc", "created_at": "2026-01-01",
            "task_files": {"public": "runs/rebench/public/o__r-1.json"}, "calibration": cal,
            "image": {"clean_digest": digest, "official_digest": digest},
            **({"memory": {"memory_bytes": memory}} if memory else {})}


def test_manifest_row_records_export_rule_and_memory():
    row = manifest_row(record(keep=True, memory=2 * 1024**3))
    assert row["export_keep_original_files"] is True and row["memory_bytes"] == 2 * 1024**3
    plain = manifest_row(record(keep=False))
    assert "export_keep_original_files" not in plain and "memory_bytes" not in plain


def plan():
    def task(stratum, rank, role, iid, repo):
        return {"stratum": stratum, "rank": rank, "role": role, "instance_id": iid, "repo": repo}
    return {"tasks": {
        "easy": [task("easy", 1, "primary", "e1", "r/e1"), task("easy", 2, "primary", "e2", "r/e2"),
                 task("easy", 3, "replacement", "e3", "r/e3"), task("easy", 4, "replacement", "e4", "r/e2"),
                 task("easy", 5, "replacement", "e5", "r/e5")],
        "other": [task("other", 1, "primary", "o1", "r/o1"), task("other", 2, "replacement", "o2", "r/o2")]}}


def fake(outcomes):
    calls = []

    def ingest(instance_id, repeats):
        calls.append(instance_id)
        outcome = outcomes.get(instance_id, [])
        if outcome == "boom":
            raise RuntimeError("pull failed")
        return {"accepted": not outcome, "calibration": {"failed_clauses": list(outcome), "summary": {},
                                                         "p2p_excluded": []}, "memory": {"memory_bytes": 1024**3}}
    return ingest, calls


def test_all_primaries_accepted_means_no_substitution():
    ingest, calls = fake({})
    result = ingest_plan(plan(), ingest)
    assert calls == ["e1", "e2", "o1"] and result["substitutions"] == [] and result["accepted"] == 3


def test_failed_primary_is_replaced_in_queue_order_and_recorded():
    ingest, calls = fake({"e1": [3], "e3": "boom"})
    result = ingest_plan(plan(), ingest)
    assert calls == ["e1", "e3", "e5", "e2", "o1"]
    assert [(s["replaced"], s["failed_clauses"], s["replacement"]) for s in result["substitutions"]] == [
        ("e1", [3], "e3"), ("e3", [1], "e5")]
    assert result["substitutions"][1]["original"] == "e1" and "pull failed" in result["substitutions"][1]["error"]
    assert task_map(result) == {"e1": "e5", "e2": "e2", "o1": "o1"}


def test_replacement_skips_taken_repositories():
    queue = [{"instance_id": "e4", "repo": "r/e2"}, {"instance_id": "e5", "repo": "r/e5"}]
    assert choose_replacement(queue, set(), {"r/e2"})["instance_id"] == "e5"
    assert choose_replacement(queue, {"e5"}, {"r/e2"}) is None


@pytest.mark.parametrize("error", ["grader", "timeout"])
def test_infra_failure_is_pending_not_a_substitution(error):
    import subprocess

    from scripts.rebench.prepare import GraderInfraError

    def ingest(instance_id, repeats):
        if instance_id == "e1":
            raise GraderInfraError("Docker create failed") if error == "grader" else \
                subprocess.TimeoutExpired(["docker", "run"], 3600)
        return {"accepted": True, "calibration": {"failed_clauses": [], "summary": {}, "p2p_excluded": []},
                "memory": {"memory_bytes": 1024**3}}
    result = ingest_plan(plan(), ingest)
    assert result["substitutions"] == [] and result["pending"] == ["e1"] and result["unfilled"] == []


def test_evaluate_retrying_retries_infra_and_gives_up():
    import asyncio

    from scripts.rebench import prepare

    class Grader:
        def __init__(self, results):
            self.results, self.calls = list(results), 0

        async def evaluate(self, **kwargs):
            self.calls += 1
            return self.results.pop(0)

    async def no_sleep(_):
        return None

    infra = {"execution_status": None, "reason": "DockerUnavailable"}
    ok = {"execution_status": "failed", "reason": None, "status": "fail"}
    original, prepare.asyncio.sleep = prepare.asyncio.sleep, no_sleep
    try:
        grader = Grader([infra, ok])
        assert asyncio.run(prepare.evaluate_retrying(grader)) is ok and grader.calls == 2
        with pytest.raises(prepare.GraderInfraError):
            asyncio.run(prepare.evaluate_retrying(Grader([infra, infra, infra])))
    finally:
        prepare.asyncio.sleep = original


def test_exhausted_queue_leaves_the_slot_unfilled():
    ingest, _ = fake({"o1": [4], "o2": [5]})
    result = ingest_plan(plan(), ingest)
    assert result["unfilled"] == ["o1"] and result["accepted"] == 2


def test_reopening_a_slot_moves_no_task_of_another_slot():
    from scripts.rebench.pilot_ingest import reopen_slot

    first, _ = fake({"e1": [3]})
    record = ingest_plan(plan(), first)
    assert task_map(record) == {"e1": "e3", "e2": "e2", "o1": "o1"}
    again, calls = fake({"e2": [9]})
    reopened = reopen_slot(plan(), record, "e2", again)
    assert calls == ["e2", "e4"]
    assert task_map(reopened) == {"e1": "e3", "e2": "e4", "o1": "o1"}
    last = reopened["substitutions"][-1]
    assert (last["replaced"], last["failed_clauses"], last["replacement"], last["reopened"]) == ("e2", [9], "e4", True)
    assert reopened["reopened"] == ["e2"] and reopened["accepted"] == 3
    assert reopened["slots"][0] == record["slots"][0]


class FakeBackend:
    outcomes: list = []
    made: list = []

    def __init__(self, image, *, keep_original_files, memory_bytes):
        self.workspace_bytes = 256 * 1024**2
        FakeBackend.made.append((image, keep_original_files, memory_bytes))

    async def start(self):
        outcome = FakeBackend.outcomes.pop(0)
        if outcome:
            raise RuntimeError(outcome)

    async def close(self):
        pass


def workspace(monkeypatch, outcomes, *, accepted=True, memory=2 * 1024**3):
    from scripts import run_swe_task
    from scripts.rebench import prepare

    FakeBackend.outcomes, FakeBackend.made = list(outcomes), []
    monkeypatch.setattr(run_swe_task, "clean_backend", FakeBackend)
    state = {"image": {"clean_digest": "img"}, "memory": {"memory_bytes": memory},
             "calibration": {"accepted": accepted, "failed_clauses": [] if accepted else [3]}, "accepted": accepted}
    return prepare.workspace_stage({}, None, keep_original_files=True)(state)


def test_a_workspace_that_cannot_start_twice_fails_clause_9(monkeypatch):
    out = workspace(monkeypatch, ["Docker control output quota exceeded"] * 2)
    assert out["accepted"] is False and out["calibration"]["accepted"] is False
    assert out["calibration"]["failed_clauses"] == [9] and "quota" in out["workspace"]["error"]
    assert FakeBackend.made == [("img", True, 2 * 1024**3)] * 2


def test_one_transient_start_failure_does_not_exclude(monkeypatch):
    out = workspace(monkeypatch, ["transient", None])
    assert out == {"workspace": {**out["workspace"], "ok": True, "error": None}} and len(FakeBackend.made) == 2


def test_rejected_tasks_are_not_probed(monkeypatch):
    assert workspace(monkeypatch, [], accepted=False) == {"workspace": {"probed": False}}
