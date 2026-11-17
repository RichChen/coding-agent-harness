"""FIX-13 scheduler tests with injected local runners. No Docker or provider calls."""
import asyncio
import copy
import json
import os
from decimal import Decimal
from hashlib import sha256

import pytest

from scripts.rebench import run_fix13 as launcher


def slots_for(blocks=1):
    slots = []
    for block in range(1, blocks + 1):
        for group in launcher.GROUPS:
            for repeat in (1, 2):
                slots.append(dict(seq=len(slots) + 1, block=block, instance_id=f"task-{block}",
                                  task=f"task-{block}", group=group, repeat=repeat))
    return slots


def write_attempt(output, group, *, cost="0.20", infra=False):
    summary = {"status": "infra_error" if infra else "pass", "cost_cny": cost,
               "mock": True, "profile": launcher.PROFILES[group].stem, "variant": launcher.VARIANTS[group],
               "solved": not infra, "model": {"name": "deepseek-flash", "base_url_host": "api.deepseek.com"}}
    (output / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (output / "cny-ledger.json").write_text(json.dumps({"cost_committed": cost, "hard_caps": {"total": "4"},
                                                       "outstanding": 0,
                                                       "settlement": {"mode": "provider_usage", "time_of_day": False}}),
                                              encoding="utf-8")


@pytest.fixture
def isolated_binding(monkeypatch):
    monkeypatch.setattr(launcher, "freeze_inputs", lambda *a, **kw: Decimal(0))


def test_four_profiles_have_only_reviewed_differences():
    report = launcher.config_report()
    assert report["ok"] and report["models"] == ["deepseek-flash"]
    assert launcher.START_THRESHOLD + launcher.BUFFER == Decimal(160)
    assert launcher.EXPECTED_RUNS == 240 and launcher.PARALLEL == 3


@pytest.mark.parametrize("value", [None, "180,250", "240,250", "480,300", "239,160"])
def test_rejects_other_experiment_authorization(value):
    with pytest.raises(SystemExit):
        launcher.check_authorized(value)


def test_explicit_four_arm_authorization():
    launcher.check_authorized("240,160")


def test_slots_require_full_30_four_arm_pairs(tmp_path, monkeypatch):
    plan = {"run_order": [{k: v for k, v in s.items() if k != "task"} for s in slots_for(30)]}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    monkeypatch.setattr(launcher, "PLAN", path)
    record = {"plan_sha256": sha256(path.read_bytes()).hexdigest(),
              "slots": [{"original": f"task-{i}", "final": f"accepted-{i}"} for i in range(1, 31)]}
    slots = launcher.load_slots(plan, record)
    assert len(slots) == 240 and slots[0]["task"] == "accepted-1"
    with pytest.raises(ValueError, match="all 30 positions"):
        launcher.load_slots(plan, {**record, "slots": record["slots"][:5]})
    broken = copy.deepcopy(plan)
    broken["run_order"][0]["group"] = "A"
    with pytest.raises(ValueError, match="eight four-arm"):
        launcher.load_slots(broken, record)


async def test_blocks_parallel_three_and_resume_no_duplicate(tmp_path, isolated_binding):
    slots = slots_for(2)
    started, active, peak = [], 0, 0

    async def fake(task, group, output, *, mock):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        started.append(task["id"])
        await asyncio.sleep(0.005)
        write_attempt(output, group)
        active -= 1

    tasks = {f"task-{i}": {"id": f"task-{i}"} for i in (1, 2)}
    report = await launcher.run_batch(slots, tasks, out_root=tmp_path, mock=True, run=fake, fetch=None)
    assert report["halt_reason"] is None and report["final_runs"] == 16
    assert peak == report["max_concurrent"] == 3
    assert started == ["task-1"] * 8 + ["task-2"] * 8
    resumed = await launcher.run_batch(slots, tasks, out_root=tmp_path, mock=True, run=fake, fetch=None)
    assert resumed["started_this_launch"] == [] and resumed["final_runs"] == 16


async def test_budget_boundary_includes_parallel_inflight(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "freeze_inputs", lambda *a, **kw: Decimal("147.90"))
    tasks = {"task-1": {"id": "task-1"}}
    started = []

    async def fake(task, group, output, *, mock):
        started.append(output)
        await asyncio.sleep(0.01)
        write_attempt(output, group, cost="4")

    report = await launcher.run_batch(slots_for(), tasks, out_root=tmp_path, mock=True, run=fake, fetch=None)
    assert len(started) == 3 and report["spent_cny"] == "159.90"
    assert report["halt_reason"].startswith("settled spend reached 148")


async def test_recovery_at_threshold_starts_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "freeze_inputs", lambda *a, **kw: Decimal("148"))

    async def forbidden(*a, **kw):
        pytest.fail("must preserve the buffer")

    report = await launcher.run_batch(slots_for(), {}, out_root=tmp_path, mock=True, run=forbidden, fetch=None)
    assert report["started_this_launch"] == [] and report["spent_cny"] == "148"


async def test_infra_retry_once_and_all_cost_retained(tmp_path, isolated_binding):
    slot = slots_for()[0]
    attempts = 0

    async def fake(task, group, output, *, mock):
        nonlocal attempts
        attempts += 1
        write_attempt(output, group, infra=True)

    report = await launcher.run_batch([slot], {"task-1": {}}, out_root=tmp_path, mock=True, run=fake, fetch=None)
    assert attempts == 2 and Decimal(report["spent_cny"]) == Decimal("0.40")
    assert (launcher.legacy.slot_dir(tmp_path, slot).with_name("r1.infra1") / "summary.json").is_file()
    await launcher.run_batch([slot], {"task-1": {}}, out_root=tmp_path, mock=True, run=fake, fetch=None)
    assert attempts == 2


async def test_interrupted_attempt_refused_without_overwrite(tmp_path, isolated_binding):
    slot = slots_for()[0]
    output = launcher.legacy.slot_dir(tmp_path, slot)
    output.mkdir(parents=True)
    with pytest.raises(ValueError, match="process and billing reconciliation"):
        await launcher.run_batch([slot], {}, out_root=tmp_path, mock=True, fetch=None)
    assert output.exists() and not (tmp_path / launcher.legacy.LOCK_FILE).exists()


def test_live_child_refused_even_with_summary(tmp_path):
    slot = slots_for()[0]
    output = launcher.legacy.slot_dir(tmp_path, slot)
    output.mkdir(parents=True)
    write_attempt(output, "U")
    (output / "child-process.json").write_text(json.dumps({"pid": os.getpid(), "finished": False}))
    with pytest.raises(ValueError, match="still alive"):
        launcher.existing_attempts(tmp_path, [slot], mock=True)


def test_budget_binding_freezes_prior_cost_and_files(tmp_path, monkeypatch):
    import src.runtime.session

    monkeypatch.setattr(src.runtime.session, "source_fingerprint", lambda root: "source-version")
    paths = []
    for name in ("PLAN", "INGEST", "MANIFEST"):
        path = tmp_path / name
        path.write_text("frozen")
        monkeypatch.setattr(launcher, name, path)
        paths.append(path)
    ledger = tmp_path / "dev-ledger.json"
    ledger.write_text(json.dumps({"cost_committed": "7.50", "outstanding": 0}))
    output = tmp_path / "batch"
    output.mkdir()
    assert launcher.freeze_inputs(output, mock=True, prior_ledgers=[ledger]) == Decimal("7.50")
    assert launcher.freeze_inputs(output, mock=True, prior_ledgers=[ledger]) == Decimal("7.50")
    paths[0].write_text("changed")
    with pytest.raises(ValueError, match="frozen inputs"):
        launcher.freeze_inputs(output, mock=True, prior_ledgers=[ledger])


def test_unknown_usage_and_resource_thresholds_halt(tmp_path):
    rows = {}
    for index in range(3):
        path = tmp_path / str(index)
        path.mkdir()
        write_attempt(path, "R")
        row = launcher.inspect_run(path, "R")
        row["cny_cap_stop"] = True
        rows[path] = row
    assert "3/3 CNY cap" in launcher.batch_halt(rows, mock=True)
    for row in rows.values():
        row["cny_cap_stop"] = False
        row["compressed"] = True
    assert "context/admission" in launcher.batch_halt(rows, mock=True)
    rows[path]["problems"] = ["billing usage unknown"]
    assert launcher.batch_halt(rows, mock=True) == "billing usage unknown"


async def test_crash_keeps_partial_directory_and_recovery_refuses_relaunch(tmp_path, isolated_binding):
    slot = slots_for()[0]
    starts = 0

    async def crash(task, group, output, *, mock):
        nonlocal starts
        starts += 1
        (output / "cny-ledger.json").write_text('{"cost_committed":"0.10"}')
        raise RuntimeError("fixture interrupted after usage")

    report = await launcher.run_batch([slot], {"task-1": {}}, out_root=tmp_path, mock=True, run=crash, fetch=None)
    assert report["halt_reason"].startswith("attempt needs reconciliation")
    assert report["spent_cny"] is None and report["unreconciled_reserve_cny"] == "4"
    with pytest.raises(ValueError, match="reconciliation"):
        await launcher.run_batch([slot], {"task-1": {}}, out_root=tmp_path, mock=True, run=crash, fetch=None)
    assert starts == 1


async def test_changed_price_prevents_any_new_attempt(tmp_path, isolated_binding):
    from tests.test_fix12_launcher import PAGE

    async def forbidden(*args, **kw):
        pytest.fail("changed price must halt")

    report = await launcher.run_batch(slots_for(), {}, out_root=tmp_path, mock=True, run=forbidden,
                                      fetch=lambda: PAGE.replace("Flash-1017", "Flash-1201"))
    assert "model_version" in report["halt_reason"] and report["started_this_launch"] == []


def test_assignment_mismatch_halts_immediately(tmp_path):
    write_attempt(tmp_path, "E")
    row = launcher.inspect_run(tmp_path, "R")
    assert "assigned FIX-13 group" in launcher.batch_halt({tmp_path: row}, mock=True)


def test_forecast_includes_remaining_e_extra_and_retired_cost(tmp_path):
    slots = slots_for()
    predicted = launcher.forecast({}, slots, tmp_path, Decimal("10"))
    expected = Decimal("10") + sum(4 * mean for mean in launcher.HISTORICAL_MEAN.values()) + Decimal("0.50")
    assert predicted == expected


def test_readiness_rejects_changed_evidence(tmp_path, monkeypatch):
    import src.runtime.session

    monkeypatch.setattr(src.runtime.session, "source_fingerprint", lambda root: "runtime")
    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    monkeypatch.setattr(launcher, "PREFLIGHT", tmp_path)
    monkeypatch.setattr(launcher, "READY", tmp_path / "ready.json")
    source = tmp_path / "reference.json"
    source.write_text("frozen")
    (tmp_path / "identity.json").write_text(json.dumps({"ok": True, "model": "deepseek-flash", "system_fingerprint": "fp"}))
    ready = {"ok": True, "tasks": 30, "e2e_cases": 64, "source_fingerprint": "runtime",
             "sources": {"reference.json": sha256(source.read_bytes()).hexdigest()}}
    launcher.READY.write_text(json.dumps(ready))
    assert launcher.readiness()[1]["system_fingerprint"] == "fp"
    source.write_text("changed")
    with pytest.raises(ValueError, match="readiness source changed"):
        launcher.readiness()


def test_formal_fingerprint_must_match_identity_smoke(tmp_path):
    write_attempt(tmp_path, "U")
    row = launcher.inspect_run(tmp_path, "U")
    row["models"] = {"deepseek-flash"}
    row["fingerprints"] = {"fp_new"}
    assert "preflight identity window" in launcher.batch_halt({tmp_path: row}, mock=False,
                                                             identity={"system_fingerprint": "fp_original"})


def test_fix13_price_fetch_uses_profile_direct_network_setting(monkeypatch):
    import httpx

    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, text="price page", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)
    assert launcher.fetch_price_page() == "price page"
    assert calls[0][0] == launcher.legacy.PRICE_URL and calls[0][1]["trust_env"] is False
