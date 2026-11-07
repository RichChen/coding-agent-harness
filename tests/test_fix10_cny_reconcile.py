"""Ledger totals next to costs recomputed from provider usage, and the run_swe_task ledger options."""
import inspect
import json
from decimal import Decimal

from scripts import cny_reconcile
from scripts.run_swe_task import make_ledger, run_task


def usage(prompt, hit, completion, reasoning=None):
    out = {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion,
           "prompt_cache_hit_tokens": hit, "prompt_cache_miss_tokens": prompt - hit, "provenance": "provider"}
    if reasoning is not None:
        out["completion_tokens_details"] = {"reasoning_tokens": reasoning}
    return out


def settled(timestamp, record_usage, elapsed_ms=1000.0):
    return {"event_type": "model.settled", "timestamp": timestamp,
            "payload": {"record": {"elapsed_ms": elapsed_ms, "success": True, "usage": record_usage}}}


def write_run(run_dir, events, ledger_rows, cost, mode=None):
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    snapshot = {"schema": "a5.cny.ledger.v1", "cost_committed": cost, "settlements": ledger_rows}
    if mode:
        snapshot["settlement"] = {"mode": mode, "time_of_day": False}
    (run_dir / "cny-ledger.json").write_text(json.dumps(snapshot), encoding="utf-8")
    (run_dir / "summary.json").write_text(json.dumps({"cost_cny": cost}), encoding="utf-8")


# 2026-11-09 is a Monday. 02:00 UTC is 10:00 Beijing (peak); 05:30 UTC is 13:30 Beijing (off-peak).
PEAK = "2026-11-09T02:00:01+00:00"
OFF_PEAK = "2026-11-09T05:30:01+00:00"
A = usage(100_000, 90_000, 1_000)
B = usage(20_000, 0, 500, reasoning=200)


def legacy_row(rid, u, cost):
    return {"reservation_id": rid, "status": "provider_usage", "cost_cny": cost,
            "prompt_tokens": u["prompt_tokens"], "completion_tokens": u["completion_tokens"]}


def test_legacy_run_recomputes_its_own_ledger_and_the_provider_prices(tmp_path):
    run = tmp_path / "r1"
    # cache_miss: A = 0.2 + 0.008 = 0.208, B = 0.04 + 0.004 = 0.044
    write_run(run, [settled(PEAK, A), settled(OFF_PEAK, B)],
              [legacy_row(1, A, "0.208"), legacy_row(2, B, "0.044")], "0.252")
    row = cny_reconcile.run_row(run, make_ledger(None).price)
    assert row["ledger_mode"] == "cache_miss"
    assert row["calls"] == {"settled": 2, "with_usage": 2, "unknown_usage": 0, "peak": 1, "off_peak": 1}
    # provider: A = 0.0036 + 0.02 + 0.008 = 0.0316; B = 0.04 + 0.004 = 0.044; off-peak B = 0.022
    assert row["recomputed_cny"] == {"cache_miss": "0.252", "provider_peak": "0.075600",
                                     "provider_time_of_day": "0.053600"}
    assert row["ledger_cny"] == "0.252" and row["ledger_matches_own_mode"] is True
    assert row["tokens"] == {"hit": 90_000, "miss": 30_000, "completion": 1_500, "reasoning": 200}


def test_unknown_usage_keeps_its_reservation_in_every_column(tmp_path):
    run = tmp_path / "r1"
    write_run(run, [settled(PEAK, A), settled(PEAK, {"provenance": "unknown"})],
              [legacy_row(1, A, "0.208"), {"reservation_id": 2, "status": "unknown_keeps_reserve", "cost_cny": "0.100"}],
              "0.308")
    row = cny_reconcile.run_row(run, make_ledger(None).price)
    assert row["calls"]["unknown_usage"] == 1
    assert row["unknown_reserved_cny"] == "0.100"
    assert row["recomputed_cny"]["cache_miss"] == "0.308"
    assert row["recomputed_cny"]["provider_peak"] == "0.131600"
    assert row["ledger_matches_own_mode"] is True


def test_provider_usage_ledger_is_checked_against_the_provider_recompute(tmp_path):
    run = tmp_path / "r1"
    write_run(run, [settled(PEAK, A)], [legacy_row(1, A, "0.031600")], "0.031600", mode="provider_usage")
    row = cny_reconcile.run_row(run, make_ledger(None).price)
    assert row["ledger_mode"] == "provider_usage"
    assert row["ledger_matches_own_mode"] is True


def test_period_uses_the_request_start_not_the_settle_time(tmp_path):
    run = tmp_path / "r1"
    # settled at 09:00:30 Beijing after a 60 s call: started 08:59:30, which is off-peak
    write_run(run, [settled("2026-11-09T01:00:30+00:00", A, elapsed_ms=60_000.0)], [legacy_row(1, A, "0.208")], "0.208")
    row = cny_reconcile.run_row(run, make_ledger(None).price)
    assert row["calls"]["off_peak"] == 1


def test_batch_total_and_ratio(tmp_path):
    for name in ("r1", "r2"):
        write_run(tmp_path / name, [settled(PEAK, A)], [legacy_row(1, A, "0.208")], "0.208")
    result = cny_reconcile.reconcile([tmp_path / "r1", tmp_path / "r2"], make_ledger(None).price)
    total = result["total"]
    assert total["runs"] == 2
    assert total["ledger_cny"] == "0.416"
    assert total["recomputed_cny"]["provider_peak"] == "0.063200"
    assert total["ledger_over_provider_time_of_day"] == float(Decimal("0.416") / Decimal("0.063200"))
    assert result["prices_cny_per_million"] == {"hit": "0.040", "miss": "2.000", "completion": "8.000"}


def test_runs_from_report_include_retired_attempts(tmp_path, monkeypatch):
    monkeypatch.setattr(cny_reconcile, "ROOT", tmp_path)
    batch = tmp_path / "runs" / "batch"
    for rel in ("t1/n/r1", "t1/n/r2.infra1"):
        (batch / rel).mkdir(parents=True)
        (batch / rel / "summary.json").write_text("{}", encoding="utf-8")
    report = batch / "main-report.json"
    report.write_text(json.dumps({"slots": ["t1/n/r1", "t1/n/r9"], "infra_retired": ["runs/batch/t1/n/r2.infra1"]}),
                      encoding="utf-8")
    assert cny_reconcile.runs_from_report(report) == [batch / "t1/n/r1", batch / "t1/n/r2.infra1"]


def test_make_ledger_default_is_unchanged(tmp_path):
    book = make_ledger(tmp_path / "ledger.json")
    assert book.settlement == "cache_miss" and book.time_of_day is False
    assert "settlement" not in book.snapshot()
    assert book.plan.total_cny == Decimal("1.000") and book.plan.pilot_calls == 40


def test_make_ledger_options(tmp_path):
    book = make_ledger(tmp_path / "ledger.json", settlement="provider_usage", time_of_day=True)
    assert book.snapshot()["settlement"] == {"mode": "provider_usage", "time_of_day": True}


def test_run_task_takes_ledger_options():
    parameter = inspect.signature(run_task).parameters["ledger_options"]
    assert parameter.default is None
