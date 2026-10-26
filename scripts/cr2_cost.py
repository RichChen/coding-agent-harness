"""CR-2: ledger cost next to cache-priced cost, per run (contract section 7.4). Reporting only.

The harness ledger prices every input token at the cache-miss rate, so it is an upper bound. This script sums the
usage of every model.settled record in events.jsonl (prompt_cache_hit_tokens, prompt_cache_miss_tokens,
completion_tokens) and prices it at the ledger's unit prices. Calls whose usage has no hit/miss split count all
prompt tokens as misses. It changes neither the ledger nor the cap logic.

    cr2_cost.py --report runs/cr2-main/canary-report.json [--out X]
"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# CNY per million tokens; the unit prices of run_swe_task.make_ledger (checked by a test)
PRICE_HIT = Decimal("0.040")
PRICE_MISS = Decimal("2.000")
PRICE_OUT = Decimal("8.000")
MILLION = Decimal(1_000_000)


def run_usage(run_dir: Path) -> dict:
    out = {"settled": 0, "with_usage": 0, "no_split": 0, "hit": 0, "miss": 0, "completion": 0}
    path = run_dir / "events.jsonl"
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if '"model.settled"' not in line:
            continue
        event = json.loads(line)
        if event.get("event_type") != "model.settled":
            continue
        out["settled"] += 1
        usage = ((event.get("payload") or {}).get("record") or {}).get("usage") or {}
        if usage.get("prompt_tokens") is None and usage.get("completion_tokens") is None:
            continue
        out["with_usage"] += 1
        hit, miss = usage.get("prompt_cache_hit_tokens"), usage.get("prompt_cache_miss_tokens")
        if hit is None or miss is None:
            out["no_split"] += 1
            hit, miss = 0, int(usage.get("prompt_tokens") or 0)
        out["hit"] += int(hit)
        out["miss"] += int(miss)
        out["completion"] += int(usage.get("completion_tokens") or 0)
    return out


def cache_cost(usage: dict) -> Decimal:
    return (usage["hit"] * PRICE_HIT + usage["miss"] * PRICE_MISS + usage["completion"] * PRICE_OUT) / MILLION


def cost_rows(run_dirs) -> dict:
    rows = []
    for run_dir in run_dirs:
        summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
        usage = run_usage(run_dir)
        rows.append({"run_dir": run_dir.relative_to(ROOT).as_posix() if run_dir.is_relative_to(ROOT) else str(run_dir),
                     "ledger_cny": str(Decimal(str(summary.get("cost_cny") or "0"))),
                     "cache_priced_cny": str(cache_cost(usage).quantize(Decimal("0.000001"))), **usage})
    total = {key: sum(row[key] for row in rows) for key in ("settled", "with_usage", "no_split", "hit", "miss", "completion")}
    ledger = sum((Decimal(row["ledger_cny"]) for row in rows), Decimal("0"))
    cached = cache_cost(total)
    prompt = total["hit"] + total["miss"]
    return {
        "prices_cny_per_million": {"hit": str(PRICE_HIT), "miss": str(PRICE_MISS), "completion": str(PRICE_OUT)},
        "runs": rows,
        "total": {**total, "ledger_cny": str(ledger), "cache_priced_cny": str(cached.quantize(Decimal("0.000001"))),
                  "cache_hit_rate": (total["hit"] / prompt) if prompt else None,
                  "ledger_over_cache_priced": float(ledger / cached) if cached else None},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report", type=Path, required=True, help="a run_cr2 report; its slots are the runs")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    root = args.report.parent
    run_dirs = []
    for label in report["slots"]:
        task, group, rk = label.split("/")
        run_dir = root / task / group / rk
        if (run_dir / "summary.json").is_file():
            run_dirs.append(run_dir)
    # retired infra attempts cost money too
    run_dirs += [ROOT / path for path in report.get("infra_retired") or [] if (ROOT / path / "summary.json").is_file()]
    result = cost_rows(run_dirs)
    text = json.dumps(result, indent=2) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
