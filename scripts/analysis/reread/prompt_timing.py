"""P4 prompt timing (scale-54, v2+v3+v4): when the action prompt reached the model and what changed after it.

Read-only, no model calls, no Docker. Reads the three batch roots (fees.jsonl, run result.json, session journals)
and the allocation list in scripts/audit/errata_2026_11/e3_p4_scale54_final.json. Writes out/prompt_timing.json.

Per valid run (outcome passed or behavior_failed; 53 runs):
- prompt_request: first request whose view (context.prepared) holds the action prompt, i.e. a second user
  message. Checked against the recorded insertion (audit.insertion.completed_responses + 1).
- cost_trigger_request: request after the one at which the settled cost of the run (fees.jsonl request_usage)
  first reached 0.225 CNY. For M this is the trigger rule; for N and I it is the point where M would have
  inserted the prompt, used as a comparison split. None when the run ended before that request.
- first_edit_request: first request with an edit call (write_file/search_replace/edit_file, or a command that
  writes a workspace file); first_native_edit_request counts only the three edit tools, as process-analysis-v1.
- Calls before and after the split (prompt_request for M, cost_trigger_request for N and I) by kind:
  read, edit, test_or_run, list, other; plus re-read calls (reread.py definition).
All results are descriptive; every cell reports its n.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _load import P4_BATCHES, load_json, payload, read_events, step_number, write_json  # noqa: E402
from reread import analyze_run, discover  # noqa: E402

THRESHOLD = Decimal("0.225")
KINDS = ("read", "edit", "test_or_run", "list", "other")


def ledger() -> dict[tuple[str, int], dict[int, Decimal]]:
    costs: dict[tuple[str, int], dict[int, Decimal]] = defaultdict(dict)
    for name, root in P4_BATCHES:
        with (root / "batch" / "fees.jsonl").open(encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                if rec["kind"] == "request_usage":
                    d = rec["data"]
                    costs[(name, d["run_index"])][d["request_number"]] = Decimal(d["cost_cny"])
    return costs


def cost_trigger(per_request: dict[int, Decimal], total_requests: int) -> int | None:
    settled = Decimal("0")
    for number in sorted(per_request):
        settled += per_request[number]
        if settled >= THRESHOLD:
            return number + 1 if number + 1 <= total_requests else None
    return None


def run_row(spec: dict, costs) -> dict:
    run_dir = Path(spec["run_dir"])
    events = read_events(run_dir)
    request_of_step: dict[int, int] = {}
    prompt_request = None
    requests = 0
    for e in events:
        if e["event_type"] == "model.started":
            requests += 1
            request_of_step.setdefault(step_number(e), requests)
        elif e["event_type"] == "context.prepared" and prompt_request is None:
            view = payload(run_dir, e)["view"]
            if sum(1 for m in view if m.get("role") == "user") >= 2:
                prompt_request = step_number(e) + 1
    audit = load_json(run_dir / "result.json").get("audit") or {}
    insertion = audit.get("insertion")
    recorded = insertion["completed_responses"] + 1 if insertion else None
    per_request = costs.get((spec["p4_batch"], spec["p4_run_index"]), {})
    trigger = cost_trigger(per_request, requests)
    calls = analyze_run(spec)["calls"]
    for c in calls:
        c["request"] = request_of_step.get(c["step"], c["step"] + 1)
    edits = [c["request"] for c in calls if c["kind"] == "edit"]
    native = [c["request"] for c in calls if c["name"] in ("write_file", "search_replace", "edit_file")]
    split = prompt_request if spec["config"] == "M" else trigger
    phases = {}
    for label, part in (("before", [c for c in calls if split is None or c["request"] < split]),
                        ("after", [c for c in calls if split is not None and c["request"] >= split])):
        phases[label] = dict(calls=len(part), requests=len({c["request"] for c in part}),
                             kinds={k: sum(c["kind"] == k for c in part) for k in KINDS},
                             reread=sum(c["reread"] for c in part))
    return dict(batch=spec["p4_batch"], run_index=spec["p4_run_index"], task=spec["task"], condition=spec["config"],
                repeat=spec["repeat"], outcome=spec["p4_outcome"], requests=requests,
                prompt_request=prompt_request, recorded_prompt_request=recorded,
                prompt_count=audit.get("prompt_count"), cost_trigger_request=trigger,
                settled_cny=str(sum(per_request.values(), Decimal("0"))),
                first_edit_request=min(edits) if edits else None,
                first_native_edit_request=min(native) if native else None,
                split_request=split, phases=phases,
                tool_calls=len(calls), reread_calls=sum(c["reread"] for c in calls))


def edit_timing(rows: list[dict]) -> dict:
    """Where the first edit falls relative to the split request."""
    out = Counter()
    gaps = []
    for r in rows:
        split, first = r["split_request"], r["first_edit_request"]
        if split is None:
            out["no_split" if first is None else "edit_no_split"] += 1
        elif first is None:
            out["no_edit"] += 1
        elif first < split:
            out["edit_before_split"] += 1
        else:
            out["edit_after_split"] += 1
            gaps.append(first - split)
    return dict(n=len(rows), counts=dict(out), gaps_after_split=sorted(gaps))


def phase_mix(rows: list[dict], phase: str) -> dict:
    kinds = Counter()
    calls = rereads = 0
    for r in rows:
        p = r["phases"][phase]
        kinds.update(p["kinds"])
        calls += p["calls"]
        rereads += p["reread"]
    return dict(runs=len(rows), calls=calls,
                pct={k: round(100 * kinds[k] / calls, 1) if calls else None for k in KINDS},
                reread_pct=round(100 * rereads / calls, 1) if calls else None)


def main() -> None:
    costs = ledger()
    specs = [s for s in discover() if s["experiment"] == "P4" and s["p4_outcome"] in ("passed", "behavior_failed")]
    rows = [run_row(s, costs) for s in specs]
    by = defaultdict(list)
    for r in rows:
        by[r["condition"]].append(r)
        by[(r["condition"], r["outcome"])].append(r)
        by[(r["condition"], r["task"])].append(r)
    m = by["M"]
    checks = dict(
        prompt_matches_record=sum(r["prompt_request"] == r["recorded_prompt_request"] for r in rows),
        rows=len(rows),
        m_prompt_matches_cost_rule=sum(r["prompt_request"] == r["cost_trigger_request"] for r in m if r["prompt_request"]),
        m_with_prompt=sum(r["prompt_request"] is not None for r in m),
        i_prompt_at_request_1=sum(r["prompt_request"] == 1 for r in by["I"]),
        mismatches=[{k: r[k] for k in ("batch", "run_index", "condition", "prompt_request", "recorded_prompt_request",
                                        "cost_trigger_request")}
                    for r in rows if r["prompt_request"] != r["recorded_prompt_request"]
                    or (r["condition"] == "M" and r["prompt_request"] and r["prompt_request"] != r["cost_trigger_request"])],
    )
    result = dict(
        schema="p4-prompt-timing-process.v1", threshold_cny=str(THRESHOLD), checks=checks,
        edit_timing={c: edit_timing(by[c]) for c in ("N", "I", "M")},
        edit_timing_by_outcome={f"{c} {o}": edit_timing(by[(c, o)]) for c in ("N", "I", "M")
                                for o in ("passed", "behavior_failed") if by[(c, o)]},
        phase_mix={f"{c} {o} {ph}": phase_mix(by[(c, o)], ph) for c in ("N", "I", "M")
                   for o in ("passed", "behavior_failed") if by[(c, o)] for ph in ("before", "after")},
        phase_mix_by_condition={f"{c} {ph}": phase_mix(by[c], ph) for c in ("N", "I", "M") for ph in ("before", "after")},
        first_native_edit_requests={c: sorted(r["first_native_edit_request"] for r in by[c]
                                              if r["first_native_edit_request"] is not None) for c in ("N", "I", "M")},
        rows=sorted(rows, key=lambda r: (r["task"], r["condition"], r["repeat"], r["batch"])),
    )
    path = write_json("prompt_timing.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, ensure_ascii=False, indent=1))
    for r in result["rows"]:
        print(r["task"][-4:], r["condition"], r["repeat"], r["outcome"][:6], "req", r["requests"],
              "prompt", r["prompt_request"], "costtrig", r["cost_trigger_request"], "edit", r["first_edit_request"],
              "native", r["first_native_edit_request"], "rr", r["reread_calls"], "/", r["tool_calls"])
    print("wrote", path)


if __name__ == "__main__":
    main()
