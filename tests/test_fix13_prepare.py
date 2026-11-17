"""FIX-13 cumulative intake and bounded recovery. No Docker or network."""
import copy
import io
import tarfile

import pytest

from scripts.rebench import fix13_prepare as intake
from scripts.rebench.fix13_sample import build_plan
from tests.test_fix13_sample import candidate_pool


def record():
    return {"slots": [], "substitutions": [], "authorized_positions": []}


def state(accepted=True):
    return {"accepted": accepted, "memory": {"memory_bytes": 1024**3},
            "calibration": {"failed_clauses": [] if accepted else [3], "p2p_excluded": [],
                            "summary": {"empty_f2p": [0, 1], "gold_f2p": [1, 1]}}}


def test_extending_keeps_accepted_slots_substitutions_and_fixed_order():
    plan = build_plan(candidate_pool(), set())
    saved = []
    current = record()
    first = intake.ordered_primaries(plan)[0]
    called = []

    def ingest(task, repeats):
        called.append(task)
        return state(task != first)

    intake.extend_intake(plan, current, 5, ingest, lambda value: saved.append(copy.deepcopy(value)))
    first_slots = copy.deepcopy(current["slots"])
    first_calls = list(called)
    intake.extend_intake(plan, current, 10, ingest, lambda value: saved.append(copy.deepcopy(value)))
    assert current["slots"][:5] == first_slots
    assert called[:len(first_calls)] == first_calls and len(called) == len(first_calls) + 5
    assert len(current["substitutions"]) == 1
    assert current["authorized_positions"] == intake.ordered_primaries(plan)[:10]
    assert len({s["final"] for s in current["slots"]}) == 10
    assert saved[0]["slots"][0]["attempts"][0]["error"] == "not_started"


def test_pending_retries_same_candidate_twice_then_stays_pending_on_resume(monkeypatch):
    monkeypatch.setattr(intake.time, "sleep", lambda seconds: None)
    plan = build_plan(candidate_pool(), set())
    current = record()
    calls = []

    def failure(task, repeats):
        calls.append(task)
        raise OSError("temporary transport failure")

    for _ in range(2):
        with pytest.raises(intake.IntakeBlocked):
            intake.extend_intake(plan, current, 5, failure, lambda value: None)
    assert len(calls) == 2 and len(set(calls)) == 1
    assert current["substitutions"] == []
    assert current["slots"][0]["attempts"][-1]["pending"]
    assert not current["slots"][0]["attempts"][-1]["failed_clauses"]


def test_three_rule_rejections_stop_before_a_fourth_candidate():
    plan = build_plan(candidate_pool(), set())
    current = record()
    called = []

    def reject(task, repeats):
        called.append(task)
        return state(False)

    for _ in range(2):
        with pytest.raises(intake.IntakeBlocked, match="three candidates"):
            intake.extend_intake(plan, current, 5, reject, lambda value: None)
    assert len(called) == 3
    assert len(current["substitutions"]) == 2
    assert len(current["slots"][0]["attempts"]) == 3


def test_capacity_stop_does_not_become_a_rule_rejection():
    plan = build_plan(candidate_pool(), set())
    current = record()

    def capacity(task, repeats):
        raise intake.IntakeBlocked("space buffer")

    with pytest.raises(intake.IntakeBlocked):
        intake.extend_intake(plan, current, 5, capacity, lambda value: None)
    assert current["infra_failures"] == {} and current["substitutions"] == []
    assert current["slots"][0]["attempts"][0]["pending"]


def test_space_estimate_preserves_both_drive_buffers(monkeypatch):
    monkeypatch.setattr(intake, "free_space", lambda: {"D": 45_000_000_000, "E": 100_000_000_000})
    with pytest.raises(intake.IntakeBlocked, match="capacity"):
        intake.check_space(image_bytes=4_000_000_000)


def test_known_baseline_format_rejection_uses_existing_clause_one():
    plan = build_plan(candidate_pool(), set())
    current = record()
    original = intake.ordered_primaries(plan)[0]

    def ingest(task, repeats):
        if task == original:
            raise intake.SourceIncompatible("baseline symlink target absent")
        return state()

    intake.extend_intake(plan, current, 5, ingest, lambda value: None)
    rejected = current["slots"][0]["attempts"][0]
    assert rejected["failed_clauses"] == [1] and not rejected.get("pending")
    assert "empty_f2p" not in rejected and current["infra_failures"] == {}


def test_ingest_atomic_write_retries_a_brief_windows_reader_lock(monkeypatch):
    calls = []
    monkeypatch.setattr(intake.time, "sleep", lambda seconds: None)

    def save(path, data):
        calls.append(copy.deepcopy(data))
        if len(calls) < 3:
            raise PermissionError("brief reader lock")

    monkeypatch.setattr(intake.prepare, "_save", save)
    current = {"slots": [], "substitutions": []}
    intake.save_intake(current)
    assert len(calls) == 3 and calls[0] == calls[-1]


def test_ingest_permanent_write_failure_stays_bounded(monkeypatch):
    calls = []
    monkeypatch.setattr(intake.time, "sleep", lambda seconds: None)

    def save(path, data):
        calls.append(path)
        raise PermissionError("persistent lock")

    monkeypatch.setattr(intake.prepare, "_save", save)
    with pytest.raises(PermissionError):
        intake.save_intake({"slots": [], "substitutions": []})
    assert len(calls) == 6


def renamed_archive(path, commit):
    with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT, pax_headers={"comment": commit}) as archive:
        member = tarfile.TarInfo("new-name-" + commit + "/src/example.py")
        member.mode = 0o640
        member.size = 4
        archive.addfile(member, io.BytesIO(b"test"))


def test_source_root_alias_preserves_relative_members_and_commit(tmp_path):
    raw = tmp_path / "raw.tar.gz"
    output = tmp_path / "source.tar.gz"
    commit = "a" * 40
    renamed_archive(raw, commit)
    intake.stamp_source_archive(raw, output, {"repo": "owner/old-name", "base_commit": commit})
    assert intake.archive_members(raw) == intake.archive_members(output)
    with tarfile.open(output) as archive:
        assert archive.getnames() == ["old-name-" + commit + "/src/example.py"]
        assert archive.pax_headers["comment"] == commit


def test_source_root_alias_rejects_a_different_original_commit(tmp_path):
    raw = tmp_path / "raw.tar.gz"
    renamed_archive(raw, "a" * 40)
    with pytest.raises(intake.prepare.GraderInfraError, match="commit proof"):
        intake.stamp_source_archive(raw, tmp_path / "source.tar.gz",
                                    {"repo": "owner/old-name", "base_commit": "b" * 40})
