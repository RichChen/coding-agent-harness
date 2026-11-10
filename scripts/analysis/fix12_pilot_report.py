"""Read-only FIX-PLAN-12 unleash pilot audit.

Run from the repository root with ``python -B scripts/analysis/fix12_pilot_report.py``.
No model calls are made. The JSON output is a reproducible audit snapshot.
"""
from __future__ import annotations

import hashlib
import json
import re
import statistics
import subprocess
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PILOT = ROOT / "runs" / "unleash-pilot"
OUT = ROOT / "docs" / "analysis" / "fix12" / "pilot-report-audit.json"
GROUPS = ("A", "U", "UT")
REQUIRED_TELEMETRY = ("request", "admission_input_tokens", "prompt_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "completion_tokens", "reasoning_tokens", "masked", "replaced_count")


def load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def sha256(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "unknown"


def dec(value):
    try:
        return Decimal(str(value or "0"))
    except Exception:
        return Decimal(0)


def settled(run):
    result = []
    events = run / "events.jsonl"
    if not events.is_file():
        return result
    for line in events.read_text(encoding="utf-8", errors="replace").splitlines():
        if '"model.settled"' not in line:
            continue
        try:
            event = json.loads(line)
            if event.get("event_type") != "model.settled":
                continue
            payload = event.get("payload") or {}
            record = payload.get("record") or {}
            ref = payload.get("artifact_ref")
            if ref:
                artifact = run / "session" / "artifacts" / ref.get("path", "")
                record = (load(artifact) or {}).get("record") or {"artifact_missing": True}
            result.append(record)
        except (json.JSONDecodeError, TypeError):
            result.append({"parse_error": True})
    return result


def final_dirs():
    found = []
    for path in PILOT.glob("*/*/r*/summary.json"):
        if re.fullmatch(r"r\d+", path.parent.name):
            found.append(path.parent)
    return sorted(found)


def counted_status(summary):
    # Frozen status convention used by run_unleash/run_cr2. Snapshot failures remain
    # visible as delta_too_large task results; other infra_error stays infra_error.
    if summary.get("status") == "infra_error" and summary.get("snapshot_error") in {
        "ValueError: delta_too_large",
        "DockerUnavailable: Docker control output quota exceeded",
    }:
        return "delta_too_large"
    return summary.get("status", "unknown")


def run_row(path):
    summary = load(path / "summary.json") or {}
    records = settled(path)
    return {"path": path, "summary": summary, "group": str(summary.get("variant", "")).replace("UNLEASH-", "") or path.parent.name,
            "status": counted_status(summary), "cost": dec(summary.get("cost_cny")), "records": records}


def main():
    finals = [run_row(path) for path in final_dirs()]
    attempts = []
    for path in PILOT.glob("*/*/r*/summary.json"):
        attempts.append(run_row(path.parent))
    retired = [r for r in attempts if not re.fullmatch(r"r\d+", r["path"].name)]
    missing_fields = []
    for row in finals:
        for field in ("task_id", "variant", "status", "solved", "cost_cny"):
            if field not in row["summary"]:
                missing_fields.append(f"{row['path'].relative_to(ROOT).as_posix()}:summary.{field}")
    report = load(ROOT / "docs/analysis/fix12/pilot-report.json") or {}
    groups = {}
    for group in GROUPS:
        rows = [r for r in finals if r["group"] == group]
        costs = sum((r["cost"] for r in rows), Decimal(0))
        solved = sum(bool(r["summary"].get("solved")) for r in rows)
        requests = [rec for r in rows for rec in r["records"]]
        solved_tasks = len({r["summary"].get("task_id") for r in rows if r["summary"].get("solved")})
        means = [sum((int((x.get("telemetry") or {}).get("reasoning_tokens") or 0) for x in r["records"]), 0) / max(len(r["records"]), 1) for r in rows]
        groups[group] = {"final_runs": len(rows), "solved_runs": solved, "solve_rate": solved / len(rows) if rows else None,
                         "solved_task_count_any_repeat": solved_tasks, "final_slot_cost_cny": f"{costs:.6f}",
                         "cost_per_solved_run_cny": f"{(costs / solved):.6f}" if solved else "unknown",
                         "request_count": len(requests), "reasoning_run_mean_median": statistics.median(means) if means else None,
                         "status_counts": dict(Counter(r["status"] for r in rows)),
                         "stop_reason_counts": dict(Counter(r["summary"].get("stop_reason", "unknown") for r in rows))}

    all_records = [rec for r in finals for rec in r["records"]]
    telemetry_complete = 0
    g2 = {g: {"requests": 0, "masked": 0, "summarized": 0, "admission_over": 0, "missing_fields": 0} for g in GROUPS}
    g3_records = []
    failure_taxonomy = Counter()
    for r in finals:
        for rec in r["records"]:
            g = r["group"] if r["group"] in GROUPS else "unknown"
            if rec.get("success") is False or rec.get("error_kind") or rec.get("status_code"):
                failure_taxonomy[str(rec.get("error_kind") or rec.get("status_code") or "success_false")] += 1
            t = rec.get("telemetry")
            usage = rec.get("usage")
            complete = isinstance(t, dict) and all(k in t for k in REQUIRED_TELEMETRY) and isinstance(usage, dict) and rec.get("usage_provenance") is not None
            telemetry_complete += int(complete)
            if g in GROUPS:
                g2[g]["requests"] += 1
                if not isinstance(t, dict):
                    g2[g]["missing_fields"] += 1
                else:
                    g2[g]["masked"] += int(bool(t.get("masked")))
                    g2[g]["summarized"] += int(bool(t.get("summarized")))
                    if t.get("prompt_tokens") is not None and t.get("admission_input_tokens") is not None and t["prompt_tokens"] > t["admission_input_tokens"]:
                        g2[g]["admission_over"] += 1
            if g == "UT":
                g3_records.append(rec)
    ut_reasoned = sum(int(((r.get("telemetry") or {}).get("reasoning_tokens") or 0) > 0) for r in g3_records)
    timing = {}
    for g in GROUPS:
        rows = [r for r in finals if r["group"] == g]
        walls = [r["summary"].get("timing", {}).get("agent_wall_seconds") for r in rows if r["summary"].get("timing", {}).get("agent_wall_seconds") is not None]
        recs = [r["summary"].get("timing", {}).get("session_record_seconds") for r in rows if r["summary"].get("timing", {}).get("session_record_seconds") is not None]
        timing[g] = {"runs_with_timing": len(walls), "agent_wall_seconds_sum": sum(walls), "session_record_seconds_sum": sum(recs), "session_record_seconds_median": statistics.median(recs) if recs else None}

    source_paths = ["docs/analysis/fix12/pilot-report.json", "docs/analysis/fix12/pilot-analysis-prelim.txt", "docs/analysis/UNLEASH-PILOT-DESIGN.zh-CN.md", "docs/analysis/fix12/FIX-12-AUDIT-REVIEW.zh-CN.md", "configs/rebench/unleash-pilot-tasks.yaml", "scripts/rebench/out/unleash_pilot_ingest.json", "scripts/rebench/out/unleash_pilot_tasks.json", "configs/rebench/unleash-a.yaml", "configs/rebench/unleash-u.yaml", "configs/rebench/unleash-ut.yaml", "scripts/rebench/run_unleash.py"]
    sources = {p: {"exists": (ROOT / p).is_file(), "sha256": sha256(ROOT / p)} for p in source_paths}
    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        head = "unknown"
    pre = list((ROOT / "runs/unleash-pilot-prerun-1").glob("**/summary.json"))
    void = list((ROOT / "runs/unleash-pilot-voided").glob("**/summary.json"))
    missing_fields.extend(p for p, info in sources.items() if not info["exists"])
    run_heads = sorted({r["summary"].get("git", {}).get("coding-harness", {}).get("head", "unknown") for r in finals})
    result = {"schema": "fix12.unleash-pilot-audit.v1", "source_hashes": sources, "missing_fields": missing_fields, "git_head": head,
              "experiment_identity": {"task_manifest": "configs/rebench/unleash-pilot-tasks.yaml", "ingest": "scripts/rebench/out/unleash_pilot_ingest.json", "run_order": "scripts/rebench/out/unleash_pilot_tasks.json", "profiles": {g: f"configs/rebench/unleash-{g.lower()}.yaml" for g in GROUPS}, "model": report.get("returned_models", "unknown"), "system_fingerprints": report.get("system_fingerprints", "unknown"), "run_summary_coding_harness_heads": run_heads},
              "run_accounting": {"final_slots": len(finals), "expected_final_slots": 180, "attempt_summaries": len(attempts), "retired_attempts": len(retired), "final_slot_cost_cny": f"{sum((r['cost'] for r in finals), Decimal(0)):.6f}", "retired_cost_cny": f"{sum((r['cost'] for r in retired), Decimal(0)):.6f}", "pre_run_attempts": len(pre), "voided_attempts": len(void), "pre_run_cost_cny": f"{sum((dec((load(p) or {}).get('cost_cny')) for p in pre), Decimal(0)):.6f}", "voided_cost_cny": f"{sum((dec((load(p) or {}).get('cost_cny')) for p in void), Decimal(0)):.6f}", "batch_spend_cny": report.get("spent_cny", "unknown"), "forecast_cny": report.get("forecast_cny", "unknown")},
              "by_group": groups, "retired": [{"path": str(r["path"].relative_to(ROOT)).replace("\\", "/"), "group": r["group"], "cost_cny": f"{r['cost']:.6f}", "status": r["status"]} for r in retired],
              "gates": {"G1": {"tasks_with_all_6_counted_valid_runs": len({r["summary"].get("task_id") for r in finals})}, "G2": g2, "G3": {"UT_runs": len([r for r in finals if r["group"] == "UT"]), "UT_requests": len(g3_records), "UT_reasoning_nonzero_requests": ut_reasoned, "UT_reasoning_request_fraction": ut_reasoned / len(g3_records) if g3_records else None, "note": "low exposure; run-level gate and request-level exposure are separate"}, "G4": {"cny_cap_stops": report.get("by_group", {}) and {g: report.get("by_group", {}).get(g, {}).get("cny_cap_stops", "unknown") for g in GROUPS}}, "G5": {"final_settled_requests": len(all_records), "telemetry_complete_requests": telemetry_complete, "missing_or_incomplete": len(all_records) - telemetry_complete, "independent_posthoc_scan": True}},
              "failure_taxonomy": {"request_level": dict(failure_taxonomy), "run_status": {g: groups[g]["status_counts"] for g in GROUPS}, "run_stop_reason": {g: groups[g]["stop_reason_counts"] for g in GROUPS}}, "timing_recording": timing, "model_fingerprints": {"models": report.get("returned_models", "unknown"), "system_fingerprints": report.get("system_fingerprints", "unknown")}, "paired_bootstrap_source": {"path": "docs/analysis/fix12/pilot-analysis-prelim.txt", "sha256": sha256(ROOT / "docs/analysis/fix12/pilot-analysis-prelim.txt"), "reported_in_prelim": True}}
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["run_accounting"], ensure_ascii=False))
    print(json.dumps({"groups": groups, "G5": result["gates"]["G5"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
