"""Reconcile a batch's CNY ledger totals with costs recomputed from provider usage. Reporting only.

For every run it reads cny-ledger.json (the ledger) and the model.settled records in events.jsonl (provider usage),
and prices the usage three ways at the unit prices of run_swe_task.make_ledger:

    cache_miss            every prompt token at the miss price, rounded per call like the original ledger
    provider_peak         hit, miss and output tokens priced separately, all at the peak price
    provider_time_of_day  as provider_peak, but calls that started in DeepSeek's off-peak hours at half price

completion_tokens already includes reasoning tokens. A call's start is the settle timestamp minus the attempt's
elapsed_ms. Calls without usage keep their reservation in the ledger; that reserved amount is added to every column
and also reported alone (unknown_reserved_cny), since such calls are usually failed requests the provider never billed.
ledger_matches_own_mode checks the ledger against the recompute of the settlement mode the ledger used.

    cny_reconcile.py --report runs/cr2-main/main-report.json [--run DIR ...] [--out X]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.policy.cny_budget import MICRO, money, price_period, provider_cost  # noqa: E402

COLUMNS = ("cache_miss", "provider_peak", "provider_time_of_day")


def _known(usage: dict) -> bool:
    return all(type(usage.get(key)) is int and usage[key] >= 0 for key in ("prompt_tokens", "completion_tokens"))


def _settled(run_dir: Path):
    path = run_dir / "events.jsonl"
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if '"model.settled"' not in line:
                continue
            event = json.loads(line)
            if event.get("event_type") == "model.settled":
                yield event


def _started(event: dict) -> datetime:
    record = (event.get("payload") or {}).get("record") or {}
    return datetime.fromisoformat(event["timestamp"]) - timedelta(milliseconds=float(record.get("elapsed_ms") or 0))


def run_row(run_dir: Path, price) -> dict:
    ledger = json.loads((run_dir / "cny-ledger.json").read_text(encoding="utf-8"))
    mode = (ledger.get("settlement") or {}).get("mode", "cache_miss")
    reserved_unknown = sum((Decimal(row["cost_cny"]) for row in ledger.get("settlements") or []
                            if row.get("status") == "unknown_keeps_reserve"), Decimal(0))
    calls = {"settled": 0, "with_usage": 0, "unknown_usage": 0, "peak": 0, "off_peak": 0}
    tokens = {"hit": 0, "miss": 0, "completion": 0, "reasoning": 0}
    cost = {column: reserved_unknown for column in COLUMNS}
    for event in _settled(run_dir):
        calls["settled"] += 1
        usage = ((event.get("payload") or {}).get("record") or {}).get("usage") or {}
        if not _known(usage):
            calls["unknown_usage"] += 1
            continue
        calls["with_usage"] += 1
        period = price_period(_started(event))
        calls[period] += 1
        peak = provider_cost(price, usage, "peak")
        cost["cache_miss"] += money((Decimal(usage["prompt_tokens"]) * price.prompt_cache_miss_per_million
                                     + Decimal(usage["completion_tokens"]) * price.completion_per_million) / Decimal(1_000_000))
        cost["provider_peak"] += peak["cost_cny"]
        cost["provider_time_of_day"] += provider_cost(price, usage, period)["cost_cny"]
        tokens["hit"] += peak["hit"]; tokens["miss"] += peak["miss"]
        tokens["completion"] += usage["completion_tokens"]; tokens["reasoning"] += peak["reasoning"] or 0
    ledger_cny = Decimal(str(ledger.get("cost_committed") or "0"))
    own = cost["cache_miss"] if mode == "cache_miss" else (
        cost["provider_time_of_day"] if (ledger.get("settlement") or {}).get("time_of_day") else cost["provider_peak"])
    return {
        "run_dir": run_dir.relative_to(ROOT).as_posix() if run_dir.is_relative_to(ROOT) else str(run_dir),
        "ledger_mode": mode,
        "ledger_cny": str(ledger_cny),
        "recomputed_cny": {"cache_miss": str(cost["cache_miss"]),
                           "provider_peak": str(cost["provider_peak"].quantize(MICRO)),
                           "provider_time_of_day": str(cost["provider_time_of_day"].quantize(MICRO))},
        "ledger_matches_own_mode": ledger_cny == own,
        "unknown_reserved_cny": str(reserved_unknown),
        "calls": calls,
        "tokens": tokens,
    }


def reconcile(run_dirs, price) -> dict:
    rows = [run_row(run_dir, price) for run_dir in run_dirs if (run_dir / "cny-ledger.json").is_file()]
    ledger = sum((Decimal(row["ledger_cny"]) for row in rows), Decimal(0))
    recomputed = {column: sum((Decimal(row["recomputed_cny"][column]) for row in rows), Decimal(0)) for column in COLUMNS}
    total = {
        "runs": len(rows),
        "runs_ledger_mismatch": sum(1 for row in rows if not row["ledger_matches_own_mode"]),
        "ledger_cny": str(ledger),
        "recomputed_cny": {column: str(value) for column, value in recomputed.items()},
        "unknown_reserved_cny": str(sum((Decimal(row["unknown_reserved_cny"]) for row in rows), Decimal(0))),
        "calls": {key: sum(row["calls"][key] for row in rows) for key in rows[0]["calls"]} if rows else {},
        "tokens": {key: sum(row["tokens"][key] for row in rows) for key in rows[0]["tokens"]} if rows else {},
    }
    for column in ("provider_peak", "provider_time_of_day"):
        total["ledger_over_" + column] = float(ledger / recomputed[column]) if recomputed[column] else None
    return {
        "prices_cny_per_million": {"hit": str(price.prompt_cache_hit_per_million),
                                   "miss": str(price.prompt_cache_miss_per_million),
                                   "completion": str(price.completion_per_million)},
        "price_snapshot": price.snapshot_id,
        "total": total,
        "runs": rows,
    }


def runs_from_report(report_path: Path) -> list[Path]:
    """A run_cr2-style report: its slots (task/group/rK under the report's folder) plus retired infra attempts."""
    report = json.loads(report_path.read_text(encoding="utf-8"))
    root = report_path.parent
    runs = [root / label for label in report.get("slots") or [] if (root / label / "summary.json").is_file()]
    runs += [ROOT / path for path in report.get("infra_retired") or [] if (ROOT / path / "summary.json").is_file()]
    return runs


def main():
    from scripts.run_swe_task import make_ledger

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report", type=Path, action="append", default=[], help="a batch report with slots")
    parser.add_argument("--run", type=Path, action="append", default=[], help="a single run directory")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--summary-only", action="store_true", help="print the totals without per-run rows")
    args = parser.parse_args()
    run_dirs = [path for report in args.report for path in runs_from_report(report)] + list(args.run)
    result = reconcile(run_dirs, make_ledger(None).price)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    shown = {key: value for key, value in result.items() if key != "runs"} if args.summary_only else result
    print(json.dumps(shown, indent=2))


if __name__ == "__main__":
    main()
