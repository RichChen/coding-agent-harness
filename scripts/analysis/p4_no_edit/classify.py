"""P4 no-edit analysis: why the 44 failed prompt-timing runs never edited.

Read-only, no model calls, no Docker. Reads the three P4 batch roots (fees.jsonl, result.json, session journals)
through scripts/analysis/reread, applies the pre-registered rules in rules.py and writes out/no_edit_runs.json and
out/no_edit_summary.json. Passing runs are measured too, as a check that the locate rule fires before their edits.

Per run:
- stop: result.json termination (natural_end when absent).
- requests: model requests started; request numbers of calls follow model.started order (as prompt_timing.py).
- located_request: request that issued the first call reading a reference function (rules.py docstring);
  file_read_request / search_hit_request: weaker signals, reported only.
- after_located: requests - located_request; calls, re-read calls (reread.py) and repeated reference reads after it.
- plan_text / rehearsal: rules.plan_text over assistant text of responses after located_request, rules.rehearsal
  over commands issued after it.
- budget: per-request cost and input tokens (fees.jsonl request_usage), admission reserves, the rejected reserve,
  and extra requests at 0.9 / 1.35 CNY by the rules.py extrapolation, with and without the 24-request cap.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "reread"))
import rules  # noqa: E402
from _load import P4_BATCHES, load_json, payload, read_events, step_number  # noqa: E402
from reads import call_io  # noqa: E402
from reread import analyze_run, discover  # noqa: E402

OUT = HERE / "out"
BUDGET = Decimal("0.45")
CURVE_POINTS = (1, 5, 10, 15)


def fee_records() -> dict[tuple[str, int], dict]:
    runs: dict[tuple[str, int], dict] = defaultdict(lambda: dict(cost={}, tokens={}, reserve={}, rejected=None))
    for name, root in P4_BATCHES:
        with (root / "batch" / "fees.jsonl").open(encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                d = rec["data"]
                key = (name, d.get("run_index"))
                if rec["kind"] == "request_usage":
                    runs[key]["cost"][d["request_number"]] = Decimal(d["cost_cny"])
                    usage = (d.get("metadata") or {}).get("raw_usage") or {}
                    runs[key]["tokens"][d["request_number"]] = usage.get("prompt_tokens")
                elif rec["kind"] == "request_stage" and d.get("stage") == "admitted":
                    runs[key]["reserve"][d["request_number"]] = Decimal(d["reserve_cny"])
                elif rec["kind"] == "request_stage" and d.get("stage") == "fee_rejected_unsent":
                    runs[key]["rejected"] = Decimal(d["reserve_cny"])
    return runs


def slope(values: list[Decimal]) -> Decimal:
    tail = values[-(rules.SLOPE_WINDOW + 1):]
    diffs = [b - a for a, b in zip(tail, tail[1:])]
    return max(Decimal("0"), Decimal(str(statistics.median(diffs)))) if diffs else Decimal("0")


def extra_requests(fees: dict, budget: Decimal) -> int | None:
    costs = [fees["cost"][k] for k in sorted(fees["cost"])]
    reserves = [fees["reserve"][k] for k in sorted(fees["reserve"])]
    if not costs or not reserves:
        return None
    settled = sum(costs, Decimal("0"))
    sc, sr = slope(costs), slope(reserves)
    if fees["rejected"] is not None:
        next_reserve, offset = fees["rejected"], 0
    else:
        next_reserve, offset = reserves[-1] + sr, 1
    k = 0
    while k < 1000:
        reserve = next_reserve + sr * k
        if settled + reserve > budget:
            break
        settled += costs[-1] + sc * (k + offset)
        k += 1
    return k


def assistant_texts(run_dir: Path, events: list[dict]) -> list[str]:
    last = None
    for e in events:
        if e["event_type"] == "state.saved":
            last = e
    texts = []
    for m in payload(run_dir, last)["messages"] if last else []:
        if m.get("role") != "assistant":
            continue
        c = m.get("content")
        if isinstance(c, list):
            c = "\n".join(p.get("text", "") for p in c if isinstance(p, dict))
        texts.append(c or "")
    return texts


def def_line(functions: list[str], output: str | None) -> bool:
    return bool(output) and any(re.search(r"def\s+" + re.escape(fn) + r"\s*\(", output) for fn in functions)


def on_reference(path: str | None, files: list[str]) -> bool:
    return bool(path) and any(path == f or path.endswith("/" + f) for f in files)


def run_row(spec: dict, fees_all) -> dict:
    run_dir = Path(spec["run_dir"])
    ref = rules.REFERENCES[spec["task"]]
    events = read_events(run_dir)
    results = {e["tool_call_id"]: e for e in events if e["event_type"] == "tool.result"}
    request_of_step: dict[int, int] = {}
    requests = 0
    calls = []
    for e in events:
        if e["event_type"] == "model.started":
            requests += 1
            request_of_step.setdefault(step_number(e), requests)
        elif e["event_type"] == "tool.intent":
            p = payload(run_dir, e)
            res = results.get(e["tool_call_id"])
            rp = payload(run_dir, res) if res else {}
            args = p.get("arguments") or {}
            output = rp.get("output") if isinstance(rp.get("output"), str) else None
            io = call_io(p["name"], args, output, bool(rp.get("success")))
            step = step_number(e)
            calls.append(dict(call_id=e["tool_call_id"], name=p["name"], step=step,
                              request=request_of_step.get(step, (step or 0) + 1),
                              command=args.get("command") if isinstance(args.get("command"), str) else "",
                              lines_on_ref=any(u.kind != "search" and on_reference(u.path, ref["files"]) for u in io.units),
                              search_on_ref=any(u.kind == "search" for u in io.units),
                              shows_def=def_line(ref["functions"], output)))
    analyzed = analyze_run(spec)["calls"]
    reread_ids = {c["call_id"] for c in analyzed if c["reread"]}
    edit_ids = {c["call_id"] for c in analyzed if c["kind"] == "edit"}
    for c in calls:
        c["edit"] = c["call_id"] in edit_ids

    def first(pred):
        return next((c["request"] for c in calls if pred(c)), None)

    located = first(lambda c: c["lines_on_ref"] and c["shows_def"])
    file_read = first(lambda c: c["lines_on_ref"])
    search_hit = first(lambda c: c["search_on_ref"] and c["shows_def"])
    first_edit = first(lambda c: c["edit"])
    result = load_json(run_dir / "result.json")
    stop = result.get("termination") or "natural_end"
    if isinstance(stop, dict):
        stop = stop.get("reason") or stop.get("kind") or json.dumps(stop, sort_keys=True)

    texts = assistant_texts(run_dir, events)
    after_calls = [c for c in calls if located is not None and c["request"] > located]
    plan_hits = [dict(request=i + 1, text=t[:600]) for i, t in enumerate(texts)
                 if located is not None and i + 1 > located and rules.plan_text(t, ref["functions"], ref["files"])]
    rehearsals = [dict(request=c["request"], command=c["command"][:600]) for c in after_calls
                  if c["name"] == "run_command" and rules.rehearsal(c["command"], ref["functions"], ref["files"])]

    fees = fees_all.get((spec["p4_batch"], spec["p4_run_index"]), dict(cost={}, tokens={}, reserve={}, rejected=None))
    costs = [fees["cost"][k] for k in sorted(fees["cost"])]
    tokens = [fees["tokens"][k] for k in sorted(fees["tokens"])]
    extra = {b: extra_requests(fees, Decimal(b)) for b in rules.COUNTERFACTUAL_BUDGETS}
    row = dict(
        batch=spec["p4_batch"], run_index=spec["p4_run_index"], task=spec["task"], condition=spec["config"],
        repeat=spec["repeat"], outcome=spec["p4_outcome"], stop=stop, requests=requests,
        located_request=located, file_read_request=file_read, search_hit_request=search_hit,
        first_edit_request=first_edit,
        after_located=requests - located if located is not None else None,
        calls_after_located=len(after_calls),
        rereads_after_located=sum(c["call_id"] in reread_ids for c in after_calls),
        ref_reads_after_located=sum(c["lines_on_ref"] and c["shows_def"] for c in after_calls),
        plan_text=bool(plan_hits), rehearsal=bool(rehearsals), plan_hits=plan_hits, rehearsals=rehearsals,
        assistant_text_responses=sum(bool(t.strip()) for t in texts),
        settled_cny=str(sum(costs, Decimal("0"))),
        cost_curve={n: str(fees["cost"].get(n)) if n in fees["cost"] else None for n in CURVE_POINTS},
        tokens_curve={n: fees["tokens"].get(n) for n in CURVE_POINTS},
        last_cost=str(costs[-1]) if costs else None, last_tokens=tokens[-1] if tokens else None,
        rejected_reserve=str(fees["rejected"]) if fees["rejected"] is not None else None,
        extra_requests=extra,
        extra_requests_capped={b: (min(v, max(0, rules.STEP_CAP - requests)) if v is not None else None)
                               for b, v in extra.items()},
    )
    if spec["p4_outcome"] == "behavior_failed":
        row["cls"] = rules.classify(located, requests, stop)
    return row


def median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def summarize(rows: list[dict]) -> dict:
    failed = [r for r in rows if r["outcome"] == "behavior_failed"]
    passed = [r for r in rows if r["outcome"] == "passed"]
    classes = ("budget", "not_found", "found_idle", "other")
    counts = Counter(r["cls"] for r in failed)
    by_task = defaultdict(Counter)
    by_cond = defaultdict(Counter)
    by_stop = defaultdict(Counter)
    for r in failed:
        by_task[r["task"]][r["cls"]] += 1
        by_cond[r["condition"]][r["cls"]] += 1
        by_stop[r["stop"]][r["cls"]] += 1
    task_runs = Counter(r["task"] for r in rows)
    task_pass = Counter(r["task"] for r in passed)
    all_fail = sorted(t for t in task_runs if task_pass[t] == 0)
    resource = [r for r in failed if r["stop"] == "insufficient_reserve"]
    extra = {b: dict(uncapped_median=median([r["extra_requests"][b] for r in resource]),
                     capped_median=median([r["extra_requests_capped"][b] for r in resource]),
                     uncapped=sorted(r["extra_requests"][b] for r in resource if r["extra_requests"][b] is not None))
             for b in rules.COUNTERFACTUAL_BUDGETS}
    idle = [r for r in failed if r["cls"] == "found_idle"]
    decision = rules.decide(dict(counts), extra["0.9"]["uncapped_median"], len(all_fail))
    pass_gaps = sorted(r["first_edit_request"] - r["located_request"] for r in passed
                       if r["first_edit_request"] is not None and r["located_request"] is not None)
    executable = [r for r in idle if r["stop"] != "response_nonexecutable"]
    timing = {(r["batch"], r["run_index"]): r
              for r in json.loads((HERE.parent / "reread" / "out" / "prompt_timing.json").read_text(encoding="utf-8"))["rows"]}
    from_prompt = {c: sorted(timing[(r["batch"], r["run_index"])]["requests"]
                             - timing[(r["batch"], r["run_index"])]["prompt_request"] + 1
                             for r in failed if r["condition"] == c
                             and timing[(r["batch"], r["run_index"])]["prompt_request"])
                   for c in ("I", "M")}
    posthoc = dict(
        note="descriptive, added after the classification; does not change any class or the decision",
        passing_gap_located_to_edit=pass_gaps,
        found_idle_after_located=sorted(r["after_located"] for r in idle),
        found_idle_after_located_gt_max_pass_gap=sum(r["after_located"] > max(pass_gaps) for r in idle),
        found_idle_nonexecutable_stop=dict(Counter(r["task"] for r in idle if r["stop"] == "response_nonexecutable")),
        found_idle_without_nonexecutable=len(executable),
        counts_without_nonexecutable=dict(Counter(r["cls"] for r in failed if r["stop"] != "response_nonexecutable")),
        high_reliability_failed=dict(Counter(r["cls"] for r in failed
                                             if rules.REFERENCES[r["task"]]["reliability"] == "high")),
        failed_requests_from_prompt_on=from_prompt,
        text_responses_per_failed_run=dict(Counter(r["assistant_text_responses"] for r in failed)),
        plan_hits=[dict(task=r["task"][-4:], condition=r["condition"], repeat=r["repeat"], hits=r["plan_hits"])
                   for r in failed if r["plan_hits"]],
    )
    curve = {str(n): dict(cost_median=median([float(r["cost_curve"][n]) for r in failed if r["cost_curve"][n]]),
                          tokens_median=median([r["tokens_curve"][n] for r in failed]),
                          runs=sum(r["tokens_curve"][n] is not None for r in failed)) for n in CURVE_POINTS}
    return dict(
        schema="p4-no-edit.v1", failed=len(failed), passed=len(passed),
        counts={c: counts.get(c, 0) for c in classes},
        by_task={t: {c: by_task[t].get(c, 0) for c in classes} for t in sorted(by_task)},
        by_condition={k: {c: by_cond[k].get(c, 0) for c in classes} for k in ("N", "I", "M")},
        by_stop={k: {c: by_stop[k].get(c, 0) for c in classes} for k in sorted(by_stop)},
        tasks={t: dict(runs=task_runs[t], passed=task_pass[t], reliability=rules.REFERENCES[t]["reliability"])
               for t in sorted(task_runs)},
        all_fail_tasks=all_fail,
        located_any=sum(r["located_request"] is not None for r in failed),
        file_read_any=sum(r["file_read_request"] is not None for r in failed),
        search_hit_only=sum(r["located_request"] is None and r["search_hit_request"] is not None for r in failed),
        found_idle_detail=dict(
            runs=len(idle),
            plan_text=sum(r["plan_text"] for r in idle), rehearsal=sum(r["rehearsal"] for r in idle),
            either=sum(r["plan_text"] or r["rehearsal"] for r in idle),
            located_request_median=median([r["located_request"] for r in idle]),
            after_located_median=median([r["after_located"] for r in idle]),
            calls_after=sum(r["calls_after_located"] for r in idle),
            rereads_after=sum(r["rereads_after_located"] for r in idle),
            ref_reads_after_median=median([r["ref_reads_after_located"] for r in idle]),
        ),
        not_found_detail=dict(
            runs=counts.get("not_found", 0),
            file_read=sum(r["file_read_request"] is not None for r in failed if r["cls"] == "not_found"),
            search_hit=sum(r["search_hit_request"] is not None for r in failed if r["cls"] == "not_found"),
        ),
        budget=dict(
            stops=dict(Counter(r["stop"] for r in failed)), insufficient_reserve_runs=len(resource),
            requests_median=median([r["requests"] for r in resource]),
            settled_median=median([float(r["settled_cny"]) for r in resource]),
            curve=curve, counterfactual=extra,
        ),
        passing_check=[dict(task=r["task"][-4:], condition=r["condition"], repeat=r["repeat"],
                            located=r["located_request"], first_edit=r["first_edit_request"],
                            file_read=r["file_read_request"]) for r in passed],
        decision=decision,
        posthoc=posthoc,
    )


def main() -> None:
    fees_all = fee_records()
    specs = [s for s in discover() if s["experiment"] == "P4" and s["p4_outcome"] in ("passed", "behavior_failed")]
    rows = sorted((run_row(s, fees_all) for s in specs), key=lambda r: (r["task"], r["condition"], r["repeat"]))
    OUT.mkdir(exist_ok=True)
    (OUT / "no_edit_runs.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = summarize(rows)
    (OUT / "no_edit_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                                              encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    for r in rows:
        print(r["task"][-4:], r["condition"], r["repeat"], r["outcome"][:6], r["stop"], "req", r["requests"],
              "loc", r["located_request"], "file", r["file_read_request"], "srch", r["search_hit_request"],
              "edit", r["first_edit_request"], "cls", r.get("cls"), "plan", r["plan_text"], "reh", r["rehearsal"],
              "extra", r["extra_requests"])


if __name__ == "__main__":
    main()
