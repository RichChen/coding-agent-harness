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
