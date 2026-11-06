"""Erratum E2: the A6-R (and A5) `model_parse_error` stops are the per-run 1 CNY cap refusing a reservation.

Reads docs/sprint/a6-retry-runs.json, the local runs/a6-retry and runs/a5-main run directories and the
runner logs under runs/a6-retry-logs and runs/a5-main-logs. Builds the b0-v2 agent offline only to get
the tool schemas the loop sends (no model calls, no Docker). Writes e2_cny_cap_parse_errors.json.

The refused reservation is rebuilt the way RetryPolicy.execute computes it: estimate_input_tokens over
{"messages": view + ["remaining model calls" line], "tools": schemas}, priced by CnyBudgetLedger.estimate
with max_output_tokens from the profile. The rebuild is first checked against every earlier attempt's
recorded estimated_input_tokens in model.settled.
"""

from __future__ import annotations

import json
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import HARNESS, load_json, payload, read_events, sha256_file, write_output  # noqa: E402

sys.path.insert(0, str(HARNESS))
from src.harness.factory import build_agent, load_config  # noqa: E402
from src.runtime.awaitctl import estimate_input_tokens  # noqa: E402

sys.path.insert(0, str(HARNESS / "scripts"))
from run_swe_task import make_ledger  # noqa: E402

PROFILE = HARNESS / "configs" / "sprint" / "b0-v2.yaml"
A6_RUNS = HARNESS / "docs" / "sprint" / "a6-retry-runs.json"
A5_ROOT = HARNESS / "runs" / "a5-main"
LOG_DIRS = (HARNESS / "runs" / "a6-retry-logs", HARNESS / "runs" / "a5-main-logs")
CAP_MESSAGE = "CNY hard cap would be exceeded"


def tool_schemas_and_limits():
    config = load_config(PROFILE)
    config["llm"]["api_key"] = "offline"
    agent = build_agent(config, Path(tempfile.mkdtemp()))
    return agent.tools.schemas(agent.enabled_tools), int(agent.llm.max_tokens), int(config["runtime"]["budget"]["max_model_calls"])


def tail_signature(events: list[dict]) -> bool:
    """context.prepared after the last model.settled, and no model.started after it."""
    last = max((i for i, e in enumerate(events) if e["event_type"] == "model.settled"), default=-1)
    after = [e["event_type"] for e in events[last + 1:]]
    return "context.prepared" in after and "model.started" not in after


def rebuild(run_dir: Path, tools, max_out: int, max_calls: int, ledger) -> dict:
    events = read_events(run_dir)
    started = 0
    view = None
    left = None
    checked = matched = 0
    mismatches = []
    for event in events:
        kind = event["event_type"]
        if kind == "context.prepared":
            view = payload(run_dir, event)["view"]
            left = max(max_calls - started, 0)
        elif kind == "model.started":
            started += 1
        elif kind == "model.settled":
            record = event["payload"]["record"]
            messages = list(view) + [{"role": "user", "content": f"remaining model calls: {left}/{max_calls}"}]
            est, _ = estimate_input_tokens(messages, tools)
            checked += 1
            if est == record.get("estimated_input_tokens"):
                matched += 1
            else:
                mismatches.append({"seq": event["seq"], "rebuilt": est, "recorded": record.get("estimated_input_tokens")})
    left = max(max_calls - started, 0)
    messages = list(view) + [{"role": "user", "content": f"remaining model calls: {left}/{max_calls}"}]
    est, _ = estimate_input_tokens(messages, tools)
    reserve = ledger.estimate(est, max_out)
    cny = load_json(run_dir / "cny-ledger.json")
    committed = Decimal(cny["cost_committed"])
    return {
        "model_calls_started": started,
        "cny_calls_started": cny["calls_started"],
        "estimate_check": {"attempts": checked, "matched": matched, "mismatches": mismatches[:5]},
        "final_estimated_input_tokens": est,
        "final_reserve_cny": str(reserve),
        "cost_committed_cny": str(committed),
        "committed_plus_reserve_cny": str(committed + reserve),
        "exceeds_cap": committed + reserve > ledger.plan.total_cny - ledger.plan.reserve_cny,
        "call_cap_reached": cny["phase_calls"]["pilot"] >= ledger.plan.pilot_calls,
        "tail_signature": tail_signature(events),
    }


def log_hits() -> dict:
    hits = {}
    for root in LOG_DIRS:
        for path in sorted(root.glob("*")):
            if path.is_file() and path.suffix in {".out", ".log"}:
                raw = path.read_bytes()
                encoding = "utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8"
                count = raw.decode(encoding, errors="replace").count(CAP_MESSAGE)
                if count:
                    hits[str(path.relative_to(HARNESS)).replace("\\", "/")] = count
    return hits


def a5_runs() -> list[Path]:
    return sorted(p for p in A5_ROOT.glob("*/b[02]/r[1-4]") if "." not in p.name and (p / "summary.json").is_file())


def main() -> None:
    tools, max_out, max_calls = tool_schemas_and_limits()
    ledger = make_ledger(Path(tempfile.mkdtemp()) / "ledger.json")
    rows = load_json(A6_RUNS)["runs"]

    signature_by_stop: dict[str, dict] = {}
    for row in rows:
        events = read_events(HARNESS / row["run_dir"])
        bucket = signature_by_stop.setdefault(f"{row['arm']}:{row['stop_reason']}", {"runs": 0, "tail_signature": 0})
        bucket["runs"] += 1
        bucket["tail_signature"] += tail_signature(events)

    a6_parse = []
    for row in rows:
        if row["stop_reason"] != "model_parse_error":
            continue
        detail = rebuild(HARNESS / row["run_dir"], tools, max_out, max_calls, ledger)
        a6_parse.append({"slot": row["run_dir"].removeprefix("runs/a6-retry/"), "arm": row["arm"], "status": row["status"],
                         "solved": row["solved"], "model_calls": row["model_calls"], "calls_short_of_40": max_calls - row["model_calls"],
                         "cost_cny": row["cost_cny"], **detail})

    a5_parse = []
    for run_dir in a5_runs():
        summary = load_json(run_dir / "summary.json")
        if summary.get("stop_reason") == "model_parse_error":
            detail = rebuild(run_dir, tools, max_out, max_calls, ledger)
            a5_parse.append({"slot": str(run_dir.relative_to(A5_ROOT)).replace("\\", "/"), "status": summary.get("status"),
                             "model_calls": summary.get("model_calls"), "cost_cny": summary.get("cost_cny"), **detail})

    def arm_stats(arm: str) -> dict:
        costs = [Decimal(r["cost_cny"]) for r in rows if r["arm"] == arm]
        return {"runs": len(costs), "mean_cost_cny": str((sum(costs) / len(costs)).quantize(Decimal("0.001"))),
                "runs_at_or_above_0_9": sum(c >= Decimal("0.9") for c in costs), "max_cost_cny": str(max(costs))}

    def span(items, key):
        values = [Decimal(i[key]) for i in items]
        return [str(min(values)), str(max(values))] if values else None

    result = {
        "schema": "errata-2026-11.e2.v1",
        "inputs": {"a6_runs": "docs/sprint/a6-retry-runs.json", "a6_runs_sha256": sha256_file(A6_RUNS),
                   "profile": "configs/sprint/b0-v2.yaml", "max_output_tokens": max_out, "max_model_calls": max_calls,
                   "cap_cny": str(ledger.plan.total_cny), "price_cache_miss_per_million": str(ledger.price.prompt_cache_miss_per_million),
                   "price_completion_per_million": str(ledger.price.completion_per_million)},
        "a6_parse_error_runs": len(a6_parse),
        "a6_parse_error_by_arm": {arm: sum(r["arm"] == arm for r in a6_parse) for arm in ("r0", "r1")},
        "a6_parse_error_cost_span_cny": span(a6_parse, "cost_committed_cny"),
        "a6_parse_error_reserve_span_cny": span(a6_parse, "final_reserve_cny"),
        "a6_parse_error_all_exceed_cap": all(r["exceeds_cap"] for r in a6_parse),
        "a6_parse_error_any_call_cap": any(r["call_cap_reached"] for r in a6_parse),
        "a6_parse_error_all_tail_signature": all(r["tail_signature"] for r in a6_parse),
        "a6_parse_error_estimates_all_match": all(r["estimate_check"]["attempts"] == r["estimate_check"]["matched"] for r in a6_parse),
        "a6_parse_error_calls_short_span": [min(r["calls_short_of_40"] for r in a6_parse), max(r["calls_short_of_40"] for r in a6_parse)] if a6_parse else None,
        "a6_parse_error_status": {s: sum(r["status"] == s for r in a6_parse) for s in sorted({r["status"] for r in a6_parse})},
        "a6_arm_cost": {arm: arm_stats(arm) for arm in ("r0", "r1")},
        "a6_tail_signature_by_arm_and_stop": signature_by_stop,
        "a6_parse_error_detail": a6_parse,
        "a5_parse_error_detail": a5_parse,
        "log_cap_message": CAP_MESSAGE,
        "log_hits": log_hits(),
    }
    path = write_output("e2_cny_cap_parse_errors.json", result)
    print(json.dumps({k: v for k, v in result.items() if not k.endswith("_detail")}, ensure_ascii=False, indent=2))
    for item in a6_parse + a5_parse:
        print(item["slot"], item["cost_committed_cny"], item["final_reserve_cny"], item["committed_plus_reserve_cny"],
              item["estimate_check"]["matched"], "/", item["estimate_check"]["attempts"])
    print("wrote", path)


if __name__ == "__main__":
    main()
