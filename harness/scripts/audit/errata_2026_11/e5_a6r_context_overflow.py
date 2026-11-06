"""Erratum E5: the A6-R `context_overflow` stops are the byte-based admission estimate, not a full model window.

Reads docs/sprint/a6-retry-runs.json and the local runs/a6-retry run directories (also the retired 402 runs,
for completeness), and counts the same stop in runs/a5-main. Read-only: no model calls, no Docker. Writes e5_a6r_context_overflow.json.

For each overflow run: the refused request is the `context.rejected` event (the last `context.prepared` is
the previous, admitted step). Its admission estimate plus the reserved output is compared with the
configured `max_tokens`, and with the provider `prompt_tokens` of the last successful call before it.
The task prompt bytes (first user message of the first model view) show how much each R1 request resends.
`admission_without_record_approx` subtracts the raw <previous_attempt> bytes from the estimate; JSON escaping
makes the real contribution slightly larger, so this is approximate and only covers the refused step.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import HARNESS, load_json, payload, read_events, sha256_file, write_output  # noqa: E402

A6_RUNS = HARNESS / "docs" / "sprint" / "a6-retry-runs.json"
A6_ROOT = HARNESS / "runs" / "a6-retry"
A5_ROOT = HARNESS / "runs" / "a5-main"
RECORD_TAG = "<previous_attempt>"


def first_user_bytes(run_dir: Path) -> tuple[int, int]:
    """Bytes of the first user message in the first model view, and of its <previous_attempt> block."""
    with (run_dir / "events.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            if '"context.prepared"' not in line[:400]:
                continue
            view = payload(run_dir, json.loads(line))["view"]
            text = next(m["content"] for m in view if m.get("role") == "user")
            start = text.find(RECORD_TAG)
            end = text.find("</previous_attempt>")
            record = len(text[start:end].encode("utf-8")) if start >= 0 and end > start else 0
            return len(text.encode("utf-8")), record
    return 0, 0


def overflow_detail(run_dir: Path) -> dict:
    events = read_events(run_dir)
    prepared = [e for e in events if e["event_type"] == "context.prepared"]
    rejected = [e for e in events if e["event_type"] == "context.rejected"]
    settled = [payload(run_dir, e)["record"] for e in events if e["event_type"] == "model.settled"]
    prompt_tokens = [r["usage"]["prompt_tokens"] for r in settled
                     if r.get("success") and (r.get("usage") or {}).get("prompt_tokens")]
    stats = payload(run_dir, rejected[-1]) if rejected else {}
    last_prepared = payload(run_dir, prepared[-1])["stats"] if prepared else {}
    admission = stats.get("admission_input_tokens")
    reserved = stats.get("reserved_output_tokens")
    return {
        "rejected_events": len(rejected),
        "rejected_step": stats.get("step"),
        "last_prepared_step": prepared and payload(run_dir, prepared[-1]).get("step"),
        "last_prepared_overflow": last_prepared.get("overflow"),
        "strategy": stats.get("strategy"),
        "error_kind": stats.get("error_kind"),
        "serialized_bytes": stats.get("serialized_bytes"),
        "raw_serialized_bytes": stats.get("raw_serialized_bytes"),
        "admission_input_tokens": admission,
        "reserved_output_tokens": reserved,
        "max_tokens": stats.get("max_tokens"),
        "admission_plus_output": (admission or 0) + (reserved or 0),
        "exceeds_max_tokens": (admission or 0) + (reserved or 0) > (stats.get("max_tokens") or 0),
        "successful_calls": len(prompt_tokens),
        "previous_prompt_tokens": prompt_tokens[-1] if prompt_tokens else None,
        "max_prompt_tokens": max(prompt_tokens) if prompt_tokens else None,
        "admission_over_previous_prompt_tokens": round(admission / prompt_tokens[-1], 2) if admission and prompt_tokens else None,
    }


def main() -> None:
    rows = load_json(A6_RUNS)["runs"]
    final = []
    for row in rows:
        if row["stop_reason"] != "context_overflow":
            continue
        run_dir = HARNESS / row["run_dir"]
        task_bytes, record_bytes = first_user_bytes(run_dir)
        detail = overflow_detail(run_dir)
        without_record = detail["admission_input_tokens"] - record_bytes
        final.append({"slot": row["run_dir"].removeprefix("runs/a6-retry/"), "arm": row["arm"], "first": row["first"],
                      "status": row["status"], "solved": row["solved"], "model_calls": row["model_calls"],
                      "task_prompt_bytes": task_bytes, "previous_attempt_bytes": record_bytes,
                      "admission_without_record_approx": without_record,
                      "step_admitted_without_record": without_record + detail["reserved_output_tokens"] <= detail["max_tokens"],
                      **detail})

    listed = {HARNESS / row["run_dir"] for row in rows}
    retired = []
    for summary in sorted(A6_ROOT.glob("*/r[01]/*/r*/summary.json")):
        run_dir = summary.parent
        if run_dir in listed or load_json(summary).get("stop_reason") != "context_overflow":
            continue
        retired.append({"run": str(run_dir.relative_to(A6_ROOT)).replace("\\", "/"), **overflow_detail(run_dir)})

    a5_summaries = [load_json(p) for p in sorted(A5_ROOT.glob("*/b[02]/r[1-4]/summary.json"))]
    a5_overflow = sum(s.get("stop_reason") == "context_overflow" for s in a5_summaries)

    prompt_bytes = {"r0": [], "r1": []}
    record_bytes = {"r0": [], "r1": []}
    for row in rows:
        task, record = first_user_bytes(HARNESS / row["run_dir"])
        prompt_bytes[row["arm"]].append(task)
        record_bytes[row["arm"]].append(record)

    def span(key):
        values = [r[key] for r in final if r[key] is not None]
        return [min(values), max(values)] if values else None

    result = {
        "schema": "errata-2026-11.e5.v1",
        "inputs": {"a6_runs": "docs/sprint/a6-retry-runs.json", "a6_runs_sha256": sha256_file(A6_RUNS),
                   "profile": "configs/sprint/b0-v2.yaml"},
        "overflow_runs": len(final),
        "overflow_by_arm": {arm: sum(r["arm"] == arm for r in final) for arm in ("r0", "r1")},
        "all_exceed_max_tokens": all(r["exceeds_max_tokens"] for r in final),
        "admission_span": span("admission_input_tokens"),
        "reserved_output_span": span("reserved_output_tokens"),
        "max_tokens": sorted({r["max_tokens"] for r in final}),
        "previous_prompt_tokens_span": span("previous_prompt_tokens"),
        "max_prompt_tokens_span": span("max_prompt_tokens"),
        "admission_over_previous_prompt_tokens_span": span("admission_over_previous_prompt_tokens"),
        "rejection_only_in_context_rejected": all(r["rejected_events"] == 1 and not r["last_prepared_overflow"]
                                                  and r["last_prepared_step"] == r["rejected_step"] - 1 for r in final),
        "task_prompt_bytes_by_arm": {arm: {"runs": len(v), "mean": round(mean(v)), "median": median(v)}
                                     for arm, v in prompt_bytes.items()},
        "previous_attempt_bytes_by_arm": {arm: {"mean": round(mean(v)), "max": max(v)} for arm, v in record_bytes.items()},
        "r1_overflow_admitted_without_record": [r["slot"] for r in final if r["arm"] == "r1" and r["step_admitted_without_record"]],
        "a5_runs": len(a5_summaries),
        "a5_context_overflow": a5_overflow,
        "overflow_detail": final,
        "retired_run_overflow_detail": retired,
    }
    path = write_output("e5_a6r_context_overflow.json", result)
    print(json.dumps({k: v for k, v in result.items() if not k.endswith("_detail")}, ensure_ascii=False, indent=2))
    for item in final + retired:
        print(item.get("slot") or item["run"], item["admission_input_tokens"], item["reserved_output_tokens"],
              item["max_tokens"], item["previous_prompt_tokens"])
    print("wrote", path)


if __name__ == "__main__":
    main()
