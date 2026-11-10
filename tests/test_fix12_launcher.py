"""Unleash pilot batch launcher on simulated runs: stop rules, batch cap, forecast, off-peak wait, price page."""
import asyncio
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.rebench import run_unleash as ru
from src.policy.cny_budget import BEIJING


def record(group, *, reasoning=None, prompt=100, admission=200, **extra):
    thinking, effort = ru.THINKING[group]
    if reasoning is None:
        reasoning = 5 if group == "UT" else None
    telemetry = {"request": {"model": "deepseek-flash", "max_tokens": 8192, "thinking": thinking,
                             "reasoning_effort": effort, "stream": True},
                 "admission_input_tokens": admission, "prompt_tokens": prompt, "prompt_cache_hit_tokens": 0,
                 "prompt_cache_miss_tokens": prompt, "completion_tokens": 5, "reasoning_tokens": reasoning,
                 "masked": False, "replaced_count": 0}
    return {"success": True, "usage": {"prompt_tokens": prompt}, "telemetry": telemetry, **extra}


def write_run(path: Path, group: str, *, cost="0.100", status="fail", records=None, cap_stop=False,
              models=("deepseek-flash",), prints=("fp_1",), mock=True, task="t1"):
    path.mkdir(parents=True, exist_ok=True)
    summary = {"task_id": task, "mock": mock, "profile": ru.PROFILES[group].stem, "variant": ru.VARIANTS[group],
               "status": status, "solved": status == "pass", "cost_cny": cost, "harness_tool_errors": 0,
               "answer_isolation_pass": True, "grader_ids": {"missing": 0, "reason": None},
               "grader_status": status, "model": {"name": "deepseek-flash", "base_url_host": "api.deepseek.com"}}
    if status == "infra_error":
        summary["infra_reason"] = "container cleanup failed"
    (path / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    rows = records if records is not None else [record(group)]
    (path / "events.jsonl").write_text("".join(json.dumps({"event_type": "model.settled", "payload": {"record": r}})
                                               + "\n" for r in rows), encoding="utf-8")
    ledger = {"hard_caps": {"total": "4.000"}, **({"rejections": [{"reserve_cny": "1.5"}]} if cap_stop else {})}
    (path / "cny-ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
    (path / "model-names.json").write_text(json.dumps({"models": list(models), "system_fingerprints": list(prints)}),
                                           encoding="utf-8")
    return path


def slots(blocks=2):
    out, seq = [], 0
    for block in range(1, blocks + 1):
        for group in ru.GROUPS:
            for repeat in (1, 2):
                seq += 1
                out.append({"seq": seq, "block": block, "instance_id": f"t{block}", "task": f"t{block}",
                            "group": group, "repeat": repeat})
    return out


# --- per-request rules -----------------------------------------------------------------------------

def test_clean_requests_pass_in_every_group():
    for group in ru.GROUPS:
        assert ru.telemetry_problems(group, [record(group), record(group)]) == []


@pytest.mark.parametrize("group, records, expected", [
    ("A", [record("A", reasoning=3)], "reasoning tokens with thinking disabled"),
    ("U", [record("U", reasoning=1)], "reasoning tokens with thinking disabled"),
    ("UT", [record("UT", reasoning=0), record("UT", reasoning=0)], "no request of the run had reasoning"),
    ("U", [record("U", prompt=300, admission=200)], "above the admission estimate"),
    ("UT", [record("UT", prompt=300, admission=200)], "above the admission estimate"),
    ("UT", [{**record("UT"), "telemetry": {**record("UT")["telemetry"],
                                           "request": {"thinking": "disabled", "reasoning_effort": None}}}],
     "sent thinking=disabled"),
    ("A", [{**record("A"), "telemetry": {**record("A")["telemetry"],
                                         "request": {"thinking": None, "reasoning_effort": None}}}],
     "sent thinking=None"),
    ("A", [{**record("A"), "telemetry": {k: v for k, v in record("A")["telemetry"].items() if k != "prompt_tokens"}}],
     "telemetry fields missing: prompt_tokens"),
    ("U", [{"success": True, "usage": {}}], "no request telemetry"),
    ("A", [record("A", billing_stop_reason="billing_usage_unknown")], "billing usage unknown"),
    ("UT", [{"success": False, "error_kind": "http", "status_code": 400}, record("UT")], "HTTP 400"),
])
def test_each_request_rule(group, records, expected):
    problems = ru.telemetry_problems(group, records)
    assert problems and expected in problems[0]


def test_rules_that_do_not_apply():
    # A uses the byte estimate: provider above it is not a stop. A failed attempt carries no telemetry.
    assert ru.telemetry_problems("A", [record("A", prompt=300, admission=200)]) == []
    assert ru.telemetry_problems("U", [{"success": False, "error_kind": "network"}, record("U")]) == []
    # U+T only needs one request with reasoning.
    assert ru.telemetry_problems("UT", [record("UT", reasoning=0), record("UT", reasoning=7)]) == []


def test_a_failed_request_settled_at_usage_is_checked_like_any_other():
    truncated = {**record("U"), "success": False, "error_kind": "incomplete"}
    assert ru.telemetry_problems("U", [truncated, record("U")]) == []
    no_fields = {**truncated, "telemetry": {k: v for k, v in truncated["telemetry"].items() if k != "prompt_tokens"}}
    assert "telemetry fields missing: prompt_tokens" in ru.telemetry_problems("U", [no_fields])[0]
    assert "no request telemetry" in ru.telemetry_problems("U", [{**truncated, "telemetry": None}])[0]
    over = {**record("UT", prompt=300, admission=200), "success": False, "error_kind": "incomplete"}
    assert "above the admission estimate" in ru.telemetry_problems("UT", [over, record("UT")])[0]


# --- batch rules ---------------------------------------------------------------------------------

def rows_for(tmp_path, specs, *, mock=True):
    rows = {}
    for i, (group, kwargs) in enumerate(specs):
        path = write_run(tmp_path / f"r{i}" / group / "r1", group, mock=mock, **kwargs)
        rows[path] = ru.inspect_run(path, group)
    return rows


def test_clean_batch_has_no_halt(tmp_path):
    rows = rows_for(tmp_path, [(g, {}) for g in ru.GROUPS for _ in range(3)])
    assert ru.batch_halt(rows, slots(), tmp_path, mock=True) is None


def test_spend_at_the_cap_stops(tmp_path):
    rows = rows_for(tmp_path, [("A", {"cost": "2.000"}), ("U", {"cost": "1.000"})])
    assert ru.batch_halt(rows, slots(), tmp_path, mock=True, cap=Decimal("3")).startswith("spend 3.000 CNY reached")
    assert ru.batch_halt(rows, slots(), tmp_path, mock=True, cap=Decimal("3.001")) is None


def test_forecast_after_five_blocks(tmp_path):
    plan = slots(blocks=6)
    first = [s for s in plan if s["block"] <= 5]

    def fill(cost):
        rows = {}
        for s in first:
            path = write_run(ru.slot_dir(tmp_path / cost, s), s["group"], cost=cost)
            rows[path] = ru.inspect_run(path, s["group"])
        return rows

    # 30 runs x 1.2 = 36 CNY -> 36 x 6 x 1.14 = 246.24, under 250; 1.25 each -> 256.5, over.
    assert ru.forecast(fill("1.200"), plan, tmp_path / "1.200") == Decimal("246.24")
    assert ru.batch_halt(fill("1.200"), plan, tmp_path / "1.200", mock=True) is None
    reason = ru.batch_halt(fill("1.250"), plan, tmp_path / "1.250", mock=True)
    assert reason.startswith("forecast 256.500 CNY after 5 blocks")
    partial = fill("1.250")
    partial.pop(next(iter(partial)))
    assert ru.forecast(partial, plan, tmp_path / "1.250") is None


def test_forecast_counts_only_the_first_five_blocks(tmp_path):
    plan = slots(blocks=6)
    rows = {}
    for s in plan:
        path = write_run(ru.slot_dir(tmp_path, s), s["group"], cost="1.200" if s["block"] <= 5 else "3.000")
        rows[path] = ru.inspect_run(path, s["group"])
    final = ru.slot_dir(tmp_path, plan[0])
    retired = write_run(final.with_name(final.name + ".infra1"), plan[0]["group"], cost="0.500")
    rows[retired] = ru.inspect_run(retired, plan[0]["group"])
    # 36 CNY in blocks 1-5 plus 0.5 retired in block 1 -> 36.5 x 6.84; the 18 CNY of block 6 stays out.
    assert ru.forecast(rows, plan, tmp_path) == Decimal("249.660")
    assert ru.batch_halt(rows, plan, tmp_path, mock=True) is None


def test_per_run_cap_stops_over_ten_percent_in_u_and_ut_only(tmp_path):
    ten = [("U", {"cap_stop": i == 0}) for i in range(10)]
    assert ru.batch_halt(rows_for(tmp_path / "a", ten), slots(), tmp_path, mock=True) is None
    two = [("UT", {"cap_stop": i < 2}) for i in range(10)]
    assert "group UT: 2 of 10" in ru.batch_halt(rows_for(tmp_path / "b", two), slots(), tmp_path, mock=True)
    a_only = [("A", {"cap_stop": True}) for _ in range(5)]
    assert ru.batch_halt(rows_for(tmp_path / "c", a_only), slots(), tmp_path, mock=True) is None


def test_infra_ratio_tool_errors_and_402(tmp_path):
    infra = [("A", {"status": "infra_error"}) for _ in range(3)] + [("U", {}) for _ in range(17)]
    assert ru.batch_halt(rows_for(tmp_path / "a", infra), slots(), tmp_path, mock=True).startswith("infra_error 3 of 20")
    fine = [("A", {"status": "infra_error"}) for _ in range(3)] + [("U", {}) for _ in range(27)]
    assert ru.batch_halt(rows_for(tmp_path / "b", fine), slots(), tmp_path, mock=True) is None
    paid = [("A", {"records": [{"success": False, "error_kind": "http", "status_code": 402}]})]
    rows = rows_for(tmp_path / "c", paid)
    assert next(iter(rows.values()))["http402"]
    assert ru.batch_halt(rows, slots(), tmp_path, mock=True).startswith("HTTP 402")


def test_model_identity_rules_on_real_runs(tmp_path):
    names = rows_for(tmp_path / "a", [("A", {"models": ("deepseek-flash",)}), ("U", {"models": ("deepseek-v5",)})],
                     mock=False)
    assert ru.batch_halt(names, slots(), tmp_path, mock=False).startswith("returned model names differ")
    assert ru.batch_halt(names, slots(), tmp_path, mock=True) is None
    prints = rows_for(tmp_path / "b", [("A", {"prints": ("fp_1",)}), ("U", {"prints": ("fp_2",)})], mock=False)
    assert ru.batch_halt(prints, slots(), tmp_path, mock=False).startswith("returned system_fingerprint changed")
    silent = rows_for(tmp_path / "c", [(g, {"models": (), "prints": ()}) for g in ru.GROUPS], mock=False)
    reason = ru.batch_halt(silent, slots(), tmp_path, mock=False, finished_order=list(silent))
    assert reason.startswith("no returned model name recorded in the first 3")


def test_a_consistent_but_unexpected_model_stops(tmp_path):
    wrong = rows_for(tmp_path / "a", [(g, {"models": ("deepseek-pro",)}) for g in ru.GROUPS], mock=False)
    assert ru.batch_halt(wrong, slots(), tmp_path, mock=False) == "returned model name deepseek-pro is not deepseek-flash"
    configured = rows_for(tmp_path / "b", [(g, {}) for g in ru.GROUPS], mock=True)
    for row in configured.values():
        row["summary"]["model"]["name"] = "deepseek-pro"
    assert ru.batch_halt(configured, slots(), tmp_path, mock=True) == "configured model deepseek-pro is not deepseek-flash"


def test_preflight_rejects_a_model_override(monkeypatch):
    monkeypatch.setattr(ru, "load_inputs", lambda: (None, {"substitutions": []}, [], {}))
    monkeypatch.setattr(ru, "fetch_price_page", lambda: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    assert ru.preflight()["model_ok"] is True
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-pro")
    report = ru.preflight()
    assert report["models"] == ["deepseek-pro"] and report["model_ok"] is False and report["pass"] is False


def test_request_problem_stops_the_batch(tmp_path):
    rows = rows_for(tmp_path, [("A", {}), ("U", {"records": [record("U", reasoning=2)]})])
    assert "reasoning tokens with thinking disabled" in ru.batch_halt(rows, slots(), tmp_path, mock=True)


# --- off-peak and price page ---------------------------------------------------------------------

@pytest.mark.parametrize("local, seconds", [
    ((2026, 11, 7, 10, 0), 0),          # Saturday
    ((2026, 11, 9, 8, 59), 0),          # Monday before 9:00
    ((2026, 11, 9, 10, 30), 5400),      # Monday morning peak until 12:00
    ((2026, 11, 9, 12, 30), 0),         # lunch break is off-peak
    ((2026, 11, 9, 17, 59, 30), 30),    # afternoon peak until 18:00
    ((2026, 11, 13, 14, 0), 4 * 3600),  # Friday afternoon
])
def test_seconds_until_off_peak(local, seconds):
    assert ru.seconds_until_off_peak(datetime(*local, tzinfo=BEIJING)) == seconds


class Clock:
    def __init__(self, start):
        self.now, self.slept = start, []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        from datetime import timedelta
        self.slept.append(seconds)
        self.now += timedelta(seconds=seconds)
        await asyncio.sleep(0)


def test_wait_off_peak_sleeps_until_the_boundary():
    clock = Clock(datetime(2026, 11, 9, 11, 0, tzinfo=BEIJING))
    waited = asyncio.run(ru.wait_off_peak(clock, clock.sleep, log=lambda _: None))
    assert waited >= 3600 and ru.seconds_until_off_peak(clock()) == 0
    assert clock().astimezone(BEIJING).hour == 12 and all(s <= 601 for s in clock.slept)


PAGE = """<html><body><h2>模型细节</h2><table><tr><td>模型</td><td>deepseek-flash</td></tr>
<tr><td>模型版本</td><td>DeepSeek-V4.1-Flash-1017</td><td>DeepSeek-V4-Pro-0813</td></tr>
<tr><td>百万tokens输入 （缓存命中）</td><td>空闲时段</td><td>0.02元</td></tr><tr><td>高峰时段</td><td>0.04元</td><td>0.30元</td></tr>
<tr><td>百万tokens输入 （缓存未命中）</td><td>空闲时段</td><td>1元</td></tr><tr><td>高峰时段</td><td>2元</td><td>9.0元</td></tr>
<tr><td>百万tokens输出</td><td>空闲时段</td><td>4元</td></tr><tr><td>高峰时段</td><td>8元</td><td>27.0元</td></tr></table>
<h2>网页搜索与抓取价格</h2><script>var t = Date.now();</script></body></html>"""


def test_price_snapshot_and_problems():
    snapshot = ru.price_snapshot(PAGE)
    assert snapshot["model_version"] == "DeepSeek-V4.1-Flash-1017"
    assert snapshot["peak_cny_per_million"] == ["0.04", "2", "8"]
    assert ru.price_problem(snapshot) is None
    assert ru.price_problem(ru.price_snapshot(PAGE.replace("Date.now()", "1"))) is None
    raised = ru.price_snapshot(PAGE.replace("<td>8元</td>", "<td>10元</td>"))
    assert "peak_cny_per_million" in ru.price_problem(raised)
    renamed = ru.price_snapshot(PAGE.replace("Flash-1017", "Flash-1201"))
    assert "model_version" in ru.price_problem(renamed)
    footnote = ru.price_snapshot(PAGE.replace("</table>", "</table><p>新增说明</p>"))
    assert ru.price_problem(footnote) is None and "changed since" in ru.price_problem(footnote, snapshot)
    assert "not found" in ru.price_problem(ru.price_snapshot("<html></html>"))


# --- the launch loop -----------------------------------------------------------------------------

class FakeRuns:
    """run_task stand-in: writes a simulated run; scenario maps (seq, attempt) to write_run keyword arguments."""

    def __init__(self, plan, scenario=None, delay=0.01):
        self.by_dir, self.scenario, self.delay = {}, scenario or {}, delay
        self.started, self.active, self.max_active, self.attempts = [], 0, 0, {}
        self.plan = plan

    async def __call__(self, task, profile, output, *, mock, variant, mock_llm, on_agent, ledger_options):
        slot = next(s for s in self.plan if output.parts[-3:] == (s["task"], s["group"], "r" + str(s["repeat"])))
        attempt = self.attempts[slot["seq"]] = self.attempts.get(slot["seq"], 0) + 1
        self.started.append(slot["seq"])
        assert profile == ru.PROFILES[slot["group"]] and variant == ru.VARIANTS[slot["group"]]
        assert ledger_options == {"settlement": "provider_usage", "time_of_day": False, "run_cap_cny": "4.000",
                                  "max_calls": 40 if slot["group"] == "A" else 100}
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(self.delay)
        self.active -= 1
        write_run(output, slot["group"], task=slot["task"], **self.scenario.get((slot["seq"], attempt), {}))
        return {}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "launcher-test")


def launch(tmp_path, plan, fake, **kwargs):
    kwargs.setdefault("fetch", None)
    kwargs.setdefault("wait_for_off_peak", False)
    return asyncio.run(ru.run_batch(plan, {s["task"]: {"id": s["task"]} for s in plan}, out_root=tmp_path / "out",
                                    mock=True, run=fake, log=lambda _: None, **kwargs))


def test_runs_start_in_seq_order_three_at_a_time(tmp_path, env):
    plan = slots(blocks=3)
    fake = FakeRuns(plan)
    report = launch(tmp_path, plan, fake)
    assert fake.started == list(range(1, 19)) and fake.max_active == 3
    assert report["halt_reason"] is None and report["final_runs"] == 18 and report["max_concurrent"] == 3
    assert report["spent_cny"] == "1.800"
    again = FakeRuns(plan)
    assert launch(tmp_path, plan, again)["started_this_launch"] == 0 and again.started == []


def test_a_stop_rule_stops_new_starts_but_in_flight_runs_finish(tmp_path, env):
    plan = slots(blocks=3)
    fake = FakeRuns(plan, {(4, 1): {"records": [record("A", reasoning=9)]}})
    report = launch(tmp_path, plan, fake)
    assert "reasoning tokens with thinking disabled" in report["halt_reason"]
    assert 4 in fake.started and max(fake.started) <= 6 and len(fake.started) < 18


def test_infra_run_is_retired_and_rerun(tmp_path, env):
    plan = slots(blocks=1)
    fake = FakeRuns(plan, {(2, 1): {"status": "infra_error"}})
    report = launch(tmp_path, plan, fake)
    out = tmp_path / "out" / "t1" / "A" / "r2"
    assert fake.attempts[2] == 2 and (out.parent / "r2.infra1" / "summary.json").is_file()
    assert json.loads((out / "summary.json").read_text())["status"] == "fail"
    assert report["by_group"]["A"]["infra_retired"] == 1 and report["halt_reason"] is None


def test_http_402_stops_without_rerun(tmp_path, env):
    plan = slots(blocks=2)
    fake = FakeRuns(plan, {(1, 1): {"status": "infra_error",
                                    "records": [{"success": False, "error_kind": "http", "status_code": 402}]}})
    report = launch(tmp_path, plan, fake)
    assert report["halt_reason"].startswith("HTTP 402") and fake.attempts[1] == 1
    assert not (tmp_path / "out" / "t1" / "A" / "r1.infra1").exists()


def test_batch_cap_stops_new_starts(tmp_path, env):
    plan = slots(blocks=2)
    fake = FakeRuns(plan, {(seq, 1): {"cost": "2.000"} for seq in range(1, 13)})
    report = launch(tmp_path, plan, fake, cap=Decimal("6"))
    assert report["halt_reason"].startswith("spend") and len(fake.started) <= 5


def test_waits_for_off_peak_before_each_start(tmp_path, env):
    plan = slots(blocks=1)
    clock = Clock(datetime(2026, 11, 9, 11, 30, tzinfo=BEIJING))
    fake = FakeRuns(plan)
    launch(tmp_path, plan, fake, wait_for_off_peak=True, clock=clock, sleep=clock.sleep)
    assert clock.slept and sum(clock.slept) >= 1800 and fake.started == list(range(1, 7))


def test_price_page_is_archived_and_a_change_stops(tmp_path, env):
    plan = slots(blocks=3)
    pages = iter([PAGE] + [PAGE.replace("<td>8元</td>", "<td>10元</td>")] * 50)
    clock = Clock(datetime(2026, 11, 7, 10, 0, tzinfo=BEIJING))
    fake = FakeRuns(plan)

    async def slow_sleep(seconds):
        await clock.sleep(seconds)

    original = ru.PRICE_RECHECK_SECONDS
    ru.PRICE_RECHECK_SECONDS = 0
    try:
        report = launch(tmp_path, plan, fake, fetch=lambda: next(pages), clock=clock, sleep=slow_sleep)
    finally:
        ru.PRICE_RECHECK_SECONDS = original
    assert "price-start-" in report["price"]["start"]
    assert "peak_cny_per_million" in report["halt_reason"] and len(fake.started) < 18
    assert list((tmp_path / "out" / "price").glob("price-changed-*.html"))
    with pytest.raises(SystemExit):
        launch(tmp_path / "bad", plan, FakeRuns(plan), fetch=lambda: PAGE.replace("Flash-1017", "Flash-1201"))


def test_without_an_injected_run_every_slot_goes_to_a_child_process(tmp_path, env, monkeypatch):
    plan = slots(blocks=2)
    calls, active = [], {"now": 0, "max": 0}

    async def child(task, group, output, *, mock):
        calls.append((task["id"], group, output.parts[-3:], mock))
        active["now"] += 1
        active["max"] = max(active["max"], active["now"])
        await asyncio.sleep(0.01)
        active["now"] -= 1
        write_run(output, group, task=task["id"])

    monkeypatch.setattr(ru, "run_in_child", child)
    report = asyncio.run(ru.run_batch(plan, {s["task"]: {"id": s["task"]} for s in plan}, out_root=tmp_path / "out",
                                      mock=True, fetch=None, wait_for_off_peak=False, log=lambda _: None))
    assert report["halt_reason"] is None and report["final_runs"] == 12 and active["max"] == 3
    assert [c[2] for c in calls] == [(s["task"], s["group"], "r" + str(s["repeat"])) for s in plan]
    assert all(c[0] == c[2][0] and c[1] == c[2][1] and c[3] is True for c in calls)


def test_a_child_gets_its_slot_on_stdin_and_a_failed_exit_raises(tmp_path, monkeypatch):
    import os
    import sys

    seen = tmp_path / "seen.json"
    script = ("import json, os, sys; data = json.loads(sys.stdin.buffer.read().decode('utf-8')); "
              f"open({str(seen)!r}, 'w').write(json.dumps([os.getpid(), data]))")
    monkeypatch.setattr(ru, "child_command", lambda: [sys.executable, "-c", script])
    asyncio.run(ru.run_in_child({"id": "t1", "title": "ü"}, "UT", tmp_path / "r1", mock=False))
    pid, spec = json.loads(seen.read_text())
    assert pid != os.getpid()
    assert spec == {"task": {"id": "t1", "title": "ü"}, "group": "UT", "output": str(tmp_path / "r1"), "mock": False}
    monkeypatch.setattr(ru, "child_command", lambda: [sys.executable, "-c", "raise SystemExit(3)"])
    with pytest.raises(RuntimeError, match="code 3"):
        asyncio.run(ru.run_in_child({"id": "t1"}, "A", tmp_path / "r2", mock=True))


def test_the_child_entry_runs_the_slot_it_reads(tmp_path, monkeypatch):
    import io
    import sys
    from types import SimpleNamespace

    got = []

    async def one(task, group, output, *, mock):
        got.append((task, group, output, mock))

    spec = {"task": {"id": "t1"}, "group": "U", "output": str(tmp_path / "r1"), "mock": True}
    monkeypatch.setattr(ru, "run_one", one)
    monkeypatch.setattr(sys, "argv", ["run_unleash.py", "--child"])
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(spec).encode("utf-8"))))
    ru.main()
    assert got == [({"id": "t1"}, "U", tmp_path / "r1", True)]


def test_unauthorized_real_run_is_refused():
    with pytest.raises(SystemExit):
        ru.check_authorized(None)
    with pytest.raises(SystemExit):
        ru.check_authorized("180,300")
    ru.check_authorized("180,250")


def test_load_slots_maps_replacements():
    plan = {"run_order": [{"seq": 1, "block": 1, "instance_id": "orig", "group": "A", "repeat": 1},
                          {"seq": 2, "block": 1, "instance_id": "keep", "group": "UT", "repeat": 2}]}
    record = {"slots": [{"original": "orig", "final": "sub"}, {"original": "keep", "final": "keep"}]}
    assert [s["task"] for s in ru.load_slots(plan, record)] == ["sub", "keep"]
    with pytest.raises(SystemExit):
        ru.load_slots(plan, {"slots": [{"original": "orig", "final": None}, {"original": "keep", "final": "keep"}]})
