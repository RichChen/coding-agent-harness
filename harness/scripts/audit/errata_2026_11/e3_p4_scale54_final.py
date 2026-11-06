"""Erratum E3: recount the P4 prompt-timing scale-54 batches (v2, v3 recovery46, v4 single row) from raw ledgers.

Reads only D:/crq/artifacts/p4-prompt-timing-scale54-{v2,v3,v4}-*/ (batch/fees.jsonl, batch.json, the v2 stop
diagnostic and the v3 recovery diagnosis). No model calls, no Docker. Writes e3_p4_scale54_final.json.

Outcome per started allocation:
- passed / behavior_failed: the check_bound status in fees.jsonl ("passed" or anything else);
- unknown: no check_bound and the batch stopped on this run with send_or_evidence_unknown;
- invalid_candidate: no check_bound, the batch stopped on this run, and the recovery diagnosis records the
  final capture as not admissible.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import load_json, sha256_file, write_output  # noqa: E402

ARTIFACTS = Path("D:/crq/artifacts")
BATCHES = (
    ("v2", ARTIFACTS / "p4-prompt-timing-scale54-v2-20261031-01"),
    ("v3", ARTIFACTS / "p4-prompt-timing-scale54-v3-recovery46-20261101-01"),
    ("v4", ARTIFACTS / "p4-prompt-timing-scale54-v4-single-row-20261101-01"),
)


def read_fees(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def termination_kind(session: Path) -> str | None:
    """termination_kind of run.finished in the session journal; opened immutable so nothing is written."""
    db = session / "events.sqlite3"
    if not db.is_file():
        return None
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro&immutable=1", uri=True)
    try:
        rows = con.execute("SELECT body FROM events ORDER BY seq").fetchall()
    finally:
        con.close()
    for (body,) in reversed(rows):
        event = json.loads(body)
        if event.get("event_type") != "run.finished":
            continue
        data = event["payload"]
        ref = data.get("artifact_ref")
        if ref:
            data = json.loads((session / "artifacts" / ref["path"]).read_bytes())
        return data.get("termination_kind") or (data.get("result") or {}).get("termination_kind")
    return None


def main() -> None:
    v2_diag = load_json(BATCHES[0][1] / "analysis" / "stopped-batch-diagnostic.json")
    v3_diag = load_json(BATCHES[1][1] / "reviews" / "recovery-diagnosis" / "report.json")
    allocations, batches = [], {}
    for name, root in BATCHES:
        fees_path = root / "batch" / "fees.jsonl"
        records = read_fees(fees_path)
        batch = load_json(root / "batch" / "batch.json")
        started, checks, stops = {}, {}, []
        usage = Decimal("0")
        held, settled = {}, set()
        for rec in records:
            kind, data = rec["kind"], rec["data"]
            if kind == "run_started":
                started[data["run_index"]] = data
            elif kind == "check_bound":
                checks[data["run_index"]] = data["status"]
            elif kind == "batch_stopped":
                stops.append(data)
            elif kind == "request_usage":
                usage += Decimal(data["cost_cny"])
                settled.add((data["run_index"], data["request_number"]))
            elif kind == "request_held":
                held[(data["run_index"], data["request_number"])] = Decimal(data["reserve_cny"])
        unknown_holds = {k: v for k, v in held.items() if k not in settled}
        stop_by_run = {s["run_index"]: s for s in stops}
        for run_index, data in sorted(started.items()):
            status = checks.get(run_index)
            if status is not None:
                outcome = "passed" if status == "passed" else "behavior_failed"
            else:
                stop = stop_by_run.get(run_index, {})
                if stop.get("reason") == "send_or_evidence_unknown":
                    outcome = "unknown"
                elif (name == "v3" and v3_diag["scope"]["failed_run_index"] == run_index
                      and v3_diag["known_facts"]["final_capture_candidate_admissible"] is False):
                    outcome = "invalid_candidate"
                else:
                    outcome = "unclassified"
            allocations.append({
                "batch": name, "run_index": run_index,
                "source_row": data.get("source_row_index", run_index),
                "replacement_of": data.get("replacement_of"),
                "task_id": data["task_id"], "condition": data["condition"], "repeat": data["repeat"],
                "check_status": status, "outcome": outcome,
                "termination": termination_kind(root / "batch" / f"run-{run_index:02d}-{data['condition']}" / "session"),
            })
        batches[name] = {
            "root": str(root).replace("\\", "/"),
            "fees_sha256": sha256_file(fees_path),
            "planned_rows": len(batch["rows"]),
            "started": len(started),
            "checks_bound": len(checks),
            "stops": [{"run_index": s["run_index"], "reason": s["reason"], "error_type": s.get("error_type")} for s in stops],
            "known_settled_cny": str(usage),
            "unknown_held_cny": str(sum(unknown_holds.values(), Decimal("0"))),
            "historical_peak_cny_at_start": batch["budget"]["historical_peak_cny"],
            "cumulative_cap_cny": batch["budget"]["cumulative_cap_cny"],
        }

    outcomes = Counter(a["outcome"] for a in allocations)
    by_condition = defaultdict(Counter)
    by_task = defaultdict(lambda: defaultdict(Counter))
    for a in allocations:
        by_condition[a["condition"]][a["outcome"]] += 1
        by_task[a["task_id"]][a["condition"]][a["outcome"]] += 1
    valid = {c: {"passed": by_condition[c]["passed"], "valid": by_condition[c]["passed"] + by_condition[c]["behavior_failed"]}
             for c in ("N", "I", "M")}
    source_rows = sorted(a["source_row"] for a in allocations)
    historical = Decimal(batches["v2"]["historical_peak_cny_at_start"])
    new_known = sum(Decimal(b["known_settled_cny"]) for b in batches.values())
    new_held = sum(Decimal(b["unknown_held_cny"]) for b in batches.values())
    result = {
        "schema": "errata-2026-11.e3.v1",
        "batches": batches,
        "allocations_total": len(allocations),
        "source_rows_covered": len(set(source_rows)),
        "source_rows_repeated": sorted({r for r in source_rows if source_rows.count(r) > 1}),
        "outcomes": dict(outcomes),
        "by_condition_valid": valid,
        "by_task_condition": {t: {c: dict(v) for c, v in sorted(conds.items())} for t, conds in sorted(by_task.items())},
        "termination_of_valid_by_condition": {c: dict(Counter(str(a["termination"]) for a in allocations
                                                              if a["condition"] == c and a["outcome"] in ("passed", "behavior_failed")))
                                              for c in ("N", "I", "M")},
        "v2_stop": {"status": v2_diag["status"], "stop": {k: v2_diag["stop"]["data"].get(k) for k in
                    ("run_index", "task_id", "condition", "repeat", "request_number", "reason", "error_type")},
                    "unknown_reserve_cny": v2_diag["unknown_request"]["body_binding"]["reserve_cny"]},
        "v3_stop": {"stop_reason": v3_diag["known_facts"]["stop_reason"], "stderr_error": v3_diag["known_facts"]["stderr_error"],
                    "root_cause": v3_diag["root_cause"]["classification"],
                    "violations": v3_diag["known_facts"]["final_capture_candidate_violations"],
                    "development_validation": [v3_diag["known_facts"]["workspace_development_validation_status"],
                                               v3_diag["known_facts"]["workspace_development_validation_reason"]]},
        "cost": {"historical_peak_before_v2_cny": str(historical), "new_known_settled_cny": str(new_known),
                 "new_unknown_held_cny": str(new_held), "cumulative_peak_cny": str(historical + new_known + new_held)},
        "allocations": allocations,
    }
    path = write_output("e3_p4_scale54_final.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "allocations"}, ensure_ascii=False, indent=2))
    print("wrote", path)


if __name__ == "__main__":
    main()
