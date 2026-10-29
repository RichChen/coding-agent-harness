"""CR-2e-a: per-run report and threshold of the T2 development check (CR-2e contract section 2).

    analyze_cr2e.py [--round 1]      docs/retrieval/cr2e-runs-round1.json -> docs/retrieval/cr2e-dev-round1.json

Gold files are read only here, after every run of the round has finished (the runs file must be complete).
Definitions, fixed before the first real run (unit-tested in tests/test_cr2e.py):
- Steps, gold touch and path normalization are those of analyze_cr2.py: the step of an event is the number of
  model.started events before it.
- A retrieve_code call is a tool.intent named retrieve_code. Its query and k are the intent's arguments (k defaults
  to 5). Its returned files are the files of the retrieval-calls.jsonl row with the same call_id.
- Read after a call: a read_file intent at step s+1 or s+2, for a call at step s, whose normalized path is one of
  that call's returned files. A run "read after retrieval" when at least one of its calls has such a read.
- Repeated retrieval without new information: a retrieve_code call (other than the first) whose normalized query
  (lower case, whitespace collapsed) equals an earlier call's, or that comes at a later step than the previous
  retrieve_code call with no tool.intent of another tool in between. Parallel calls with different queries in one
  turn are not repeats.
- Threshold: at least 6 of the 12 runs call retrieve_code, and among those runs at least half read after retrieval.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.analyze_cr2 import _read_jsonl, first_gold_step, normalize_path, steps  # noqa: E402

DEFAULT_K = 5
READ_WINDOW = 2
MIN_CALLING_RUNS = 6
CALLS_FILE = "retrieval-calls.jsonl"


def runs_path(round_no: int) -> Path:
    return ROOT / "docs" / "retrieval" / f"cr2e-runs-round{round_no}.json"


def out_path(round_no: int) -> Path:
    return ROOT / "docs" / "retrieval" / f"cr2e-dev-round{round_no}.json"


def _norm_query(text) -> str:
    return " ".join(str(text or "").lower().split())


def tool_trace(events: list[dict]) -> list[dict]:
    """Every tool.intent with its step, name, arguments and call id."""
    out = []
    for step, event in steps(events):
        if event.get("event_type") != "tool.intent":
            continue
        payload = event.get("payload") or {}
        out.append({"step": step, "name": payload.get("name"), "arguments": payload.get("arguments") or {},
                    "call_id": event.get("tool_call_id")})
    return out


def retrieval_report(events: list[dict], calls: list[dict], gold) -> dict:
    gold = {normalize_path(path) for path in gold}
    returned_by_call = {row.get("call_id"): [normalize_path(f["path"]) for f in row.get("files") or []] for row in calls}
    trace = tool_trace(events)
    reads = [(t["step"], normalize_path(t["arguments"].get("path"))) for t in trace if t["name"] == "read_file"]
    rows, seen_queries, last_retrieve_index = [], set(), None
    for i, t in enumerate(trace):
        if t["name"] != "retrieve_code":
            continue
        query = t["arguments"].get("query")
        returned = returned_by_call.get(t["call_id"], [])
        read = sorted({path for step, path in reads if t["step"] < step <= t["step"] + READ_WINDOW and path in returned})
        repeated = False
        if last_retrieve_index is not None:
            previous = trace[last_retrieve_index]
            other_since = any(x["name"] != "retrieve_code" for x in trace[last_retrieve_index + 1:i])
            repeated = _norm_query(query) in seen_queries or (t["step"] > previous["step"] and not other_since)
        rows.append({"step": t["step"], "call_id": t["call_id"], "query": query, "k": t["arguments"].get("k", DEFAULT_K),
                     "returned": returned, "gold_in_result": bool(set(returned) & gold), "read_within_2": read,
                     "repeated_without_new_info": repeated})
        seen_queries.add(_norm_query(query))
        last_retrieve_index = i
    return {
        "called": bool(rows),
        "calls": len(rows),
        "first_call_step": rows[0]["step"] if rows else None,
        "call_rows": rows,
        "gold_in_any_result": any(r["gold_in_result"] for r in rows),
        "read_after_retrieval": any(r["read_within_2"] for r in rows),
        "repeated_calls": sum(1 for r in rows if r["repeated_without_new_info"]),
        "search_code_calls": sum(1 for t in trace if t["name"] == "search_code"),
    }


def threshold(per_run: list[dict]) -> dict:
    calling = [r for r in per_run if r["called"]]
    reading = [r for r in calling if r["read_after_retrieval"]]
    first = len(calling) >= MIN_CALLING_RUNS
    second = bool(calling) and 2 * len(reading) >= len(calling)
    return {"runs": len(per_run), "calling_runs": len(calling), "reading_runs": len(reading),
            "min_calling_runs": MIN_CALLING_RUNS, "calls_ok": first, "reads_ok": second, "pass": first and second}


def analyze(runs: list[dict], gold_by_task: dict, root: Path = ROOT) -> dict:
    per_run = []
    for row in runs:
        run_dir = root / row["run_dir"]
        events = _read_jsonl(run_dir / "events.jsonl")
        report = retrieval_report(events, _read_jsonl(run_dir / CALLS_FILE), gold_by_task[row["task_id"]])
        per_run.append({"task_id": row["task_id"], "k": row["k"], "run_dir": row["run_dir"], **report,
                        "first_gold_step": first_gold_step(events, gold_by_task[row["task_id"]]),
                        "solved": bool(row.get("solved")) and not row.get("no_candidate"),
                        "status": row.get("status"), "stop_reason": row.get("stop_reason"),
                        "model_calls": row.get("model_calls"), "cost_cny": row.get("cost_cny")})
    return {"threshold": threshold(per_run), "per_run": per_run}


def cost(runs: list[dict], round_report: Path) -> dict:
    """Ledger next to cache-priced cost (cr2_cost.py prices), counting retired infra attempts too."""
    from scripts.cr2_cost import cost_rows

    run_dirs = [ROOT / row["run_dir"] for row in runs]
    if round_report.is_file():
        retired = json.loads(round_report.read_text(encoding="utf-8")).get("infra_retired") or []
        run_dirs += [ROOT / path for path in retired if (ROOT / path / "summary.json").is_file()]
    return cost_rows(run_dirs)["total"]


def main():
    from scripts.analyze_cr2 import load_gold

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--round", type=int, default=1)
    args = parser.parse_args()
    payload = json.loads(runs_path(args.round).read_text(encoding="utf-8"))
    runs = payload["runs"]
    if len(runs) != payload["slots_expected"] or any(row.get("rerun_reason") for row in runs):
        raise SystemExit("the round is not complete; gold files are read only after every run has finished")
    report = analyze(runs, load_gold(sorted({row["task_id"] for row in runs})))
    report.update(round=args.round, runs_file=runs_path(args.round).relative_to(ROOT).as_posix(),
                  cost=cost(runs, ROOT / "runs" / "cr2e" / f"round{args.round}" / "report.json"))
    out = out_path(args.round)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report["threshold"], indent=2))


if __name__ == "__main__":
    main()
