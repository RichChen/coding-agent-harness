"""FIX-12 question 2: classify the 180 formal pilot runs with the rules committed in rules.py.

Read-only, no model calls, no Docker. Reads runs/unleash-pilot/<task>/<group>/<repeat>/ (events.jsonl,
session/artifacts, summary.json) and out/references.json; writes out/runs.json and out/summary.json.

Per run: status, stop_reason and changed_files from summary.json; requests = model.started events, a call's request
follows model.started order (as P4); located_request = request of the first line-range read on a reference code
file whose output shows a reference def/class line; file_read_request = first line-range read on a reference code
file; first_edit_request = first call reread.call_kind marks "edit"; plan_text over assistant content after the
located request.

Post-hoc, descriptive only (added after the manual check; no class or decision depends on them):
- tail_repeat: share of the run's last TAIL calls whose tool name and arguments exactly repeat an earlier call;
- source_change: the final candidate changes a non-test file that exists in the task's baseline.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
HARNESS = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "reread"))
import rules  # noqa: E402
from _load import payload, read_events, step_number  # noqa: E402
from reads import call_io  # noqa: E402
from reread import call_kind  # noqa: E402

RUNS = HARNESS / "runs" / "unleash-pilot"
WORK = HARNESS / "runs" / "rebench" / "work"
OUT = HERE / "out"
TAIL = 20
LOOP_SHARE = 0.5
FAILED_CLASSES = ("found_idle", "not_found", "budget", "other", "edited_reference", "edited_elsewhere",
                  "edited_reverted")


def references() -> dict[str, dict]:
    return json.loads((OUT / "references.json").read_text(encoding="utf-8"))["tasks"]


def run_specs(refs: dict[str, dict]) -> list[dict]:
    return [dict(task=t, group=g, repeat=r, run_dir=RUNS / t / g / r)
            for t in sorted(refs) for g in rules.GROUPS for r in rules.REPEATS]


def on_reference(path: str | None, files: list[str]) -> bool:
    return bool(path) and any(path == f or path.endswith("/" + f) for f in files)


def assistant_contents(run_dir: Path, events: list[dict]) -> list[str]:
    last = next((e for e in reversed(events) if e["event_type"] == "state.saved"), None)
    texts = []
    for m in payload(run_dir, last)["messages"] if last else []:
        if m.get("role") != "assistant":
            continue
        c = m.get("content")
        if isinstance(c, list):
            c = "\n".join(p.get("text", "") for p in c if isinstance(p, dict))
        texts.append(c or "")
    return texts


def run_calls(run_dir: Path, ref: dict) -> tuple[list[dict], int, list[dict]]:
    events = read_events(run_dir)
    code_files = [f["path"] for f in ref["files"] if f["code"]]
    patterns = [rules.ref_pattern(x) for x in ref["refs"]]
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
            command = args.get("command") if isinstance(args.get("command"), str) else ""
            step = step_number(e)
            lines_on_ref = any(u.kind != "search" and on_reference(u.path, code_files) for u in io.units)
            shows_ref = bool(output) and (any(pt.search(output) for pt in patterns) if patterns else True)
            calls.append(dict(request=request_of_step.get(step, (step or 0) + 1), name=p["name"], args=args,
                              output=output, edit=call_kind(p["name"], io, command) == "edit",
                              lines_on_ref=lines_on_ref, located=lines_on_ref and shows_ref))
    return calls, requests, events


def run_row(spec: dict, ref: dict) -> dict:
    run_dir = spec["run_dir"]
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    calls, requests, events = run_calls(run_dir, ref)

    def first(key):
        return next((c["request"] for c in calls if c[key]), None)

    located, file_read, first_edit = first("located"), first("lines_on_ref"), first("edit")
    texts = assistant_contents(run_dir, events)
    names = [x["name"] for x in ref["refs"]]
    code_files = [f["path"] for f in ref["files"] if f["code"]]
    plan = located is not None and any(rules.plan_text(t, names, code_files)
                                       for i, t in enumerate(texts) if i + 1 > located)
    changed = summary.get("changed_files") or []
    keys = [json.dumps([c["name"], c["args"]], sort_keys=True) for c in calls]
    tail_start = max(0, len(keys) - TAIL)
    tail_repeat = (sum(k in set(keys[:i]) for i, k in enumerate(keys) if i >= tail_start) / (len(keys) - tail_start)
                   if keys else None)
    baseline = WORK / spec["task"] / "staging" / "baseline"
    row = dict(task=spec["task"], group=spec["group"], repeat=spec["repeat"], status=summary["status"],
               stop=summary.get("stop_reason"), requests=requests, located_request=located,
               file_read_request=file_read, first_edit_request=first_edit,
               edit_calls=sum(c["edit"] for c in calls), changed_files=changed,
               after_located=requests - located if located is not None else None, plan_text=plan,
               granularity=ref["granularity"], tail_repeat=tail_repeat,
               source_change=any(not rules.is_test_path(f) and (baseline / f).is_file() for f in changed))
    if row["status"] != "pass":
        row["cls"] = (rules.edited_class(row["edit_calls"], changed, code_files)
                      or rules.classify(located, requests, row["stop"]))
    return row


def median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def arm_table(rows: list[dict]) -> dict:
    out = {}
    for g in rules.GROUPS:
        arm = [r for r in rows if r["group"] == g]
        failed = [r for r in arm if r["status"] != "pass"]
        no_edit = [r for r in failed if not r["cls"].startswith("edited")]
        counts = Counter(r["cls"] for r in failed)
        idle = counts.get("found_idle", 0)
        out[g] = dict(runs=len(arm), passed=len(arm) - len(failed), failed=len(failed), no_edit_failed=len(no_edit),
                      counts={c: counts.get(c, 0) for c in FAILED_CLASSES},
                      found_idle_share=idle / len(failed) if failed else None,
                      found_idle_share_no_edit=idle / len(no_edit) if no_edit else None,
                      located_any_failed=sum(r["located_request"] is not None for r in failed),
                      stops_failed=dict(Counter(r["stop"] for r in failed)))
    return out


def posthoc(rows: list[dict]) -> dict:
    out = dict(note="descriptive, added after the manual check; does not change any class or the decision",
               tail=TAIL, loop_share=LOOP_SHARE)
    for g in rules.GROUPS:
        arm = [r for r in rows if r["group"] == g]
        failed = [r for r in arm if r["status"] != "pass"]
        idle = [r for r in failed if r["cls"] == "found_idle"]
        loops = [r for r in failed if (r["tail_repeat"] or 0) >= LOOP_SHARE]
        out[g] = dict(
            failed_without_source_change=sum(not r["source_change"] for r in failed),
            failed_without_source_change_located=sum(not r["source_change"] and r["located_request"] is not None
                                                     for r in failed),
            failed_loop_tail=len(loops),
            loop_tail_by_class=dict(Counter(r["cls"] for r in loops)),
            found_idle_loop_tail=sum((r["tail_repeat"] or 0) >= LOOP_SHARE for r in idle),
            found_idle_plan_text=sum(r["plan_text"] for r in idle),
            passed_loop_tail=sum((r["tail_repeat"] or 0) >= LOOP_SHARE for r in arm if r["status"] == "pass"),
            tail_repeat_median_failed=median([r["tail_repeat"] for r in failed]),
            tail_repeat_median_passed=median([r["tail_repeat"] for r in arm if r["status"] == "pass"]),
        )
    return out


def summarize(rows: list[dict]) -> dict:
    arms = arm_table(rows)
    no_interface = arm_table([r for r in rows if not r["task"].endswith(rules.INTERFACE_SUFFIX)])
    passed = [r for r in rows if r["status"] == "pass"]
    idle = [r for r in rows if r.get("cls") == "found_idle"]
    gaps = {g: sorted(r["first_edit_request"] - r["located_request"] for r in passed if r["group"] == g
                      and r["first_edit_request"] is not None and r["located_request"] is not None)
            for g in rules.GROUPS}
    return dict(
        schema="fix12-no-edit-gold.v1",
        arms=arms,
        decision=rules.decide({g: arms[g]["found_idle_share"] for g in rules.GROUPS}),
        without_interface_tasks=no_interface,
        decision_without_interface=rules.decide({g: no_interface[g]["found_idle_share"] for g in rules.GROUPS}),
        found_idle_detail={g: [dict(task=r["task"], repeat=r["repeat"], stop=r["stop"], requests=r["requests"],
                                    located=r["located_request"], after=r["after_located"], plan=r["plan_text"])
                               for r in idle if r["group"] == g] for g in rules.GROUPS},
        passing_check=dict(
            passed=len(passed),
            located=sum(r["located_request"] is not None for r in passed),
            located_before_or_at_first_edit=sum(r["located_request"] is not None and r["first_edit_request"] is not None
                                                and r["located_request"] <= r["first_edit_request"] for r in passed),
            gap_located_to_edit=gaps,
            gap_median={g: median(v) for g, v in gaps.items()},
        ),
        posthoc=posthoc(rows),
        tasks_all_failed={g: sorted({r["task"] for r in rows if r["group"] == g}
                                    - {r["task"] for r in passed if r["group"] == g}) for g in rules.GROUPS},
    )


def main() -> None:
    refs = references()
    rows = [run_row(s, refs[s["task"]]) for s in run_specs(refs)]
    for r in rows:
        r.pop("run_dir", None)
    (OUT / "runs.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = summarize(rows)
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(arms=summary["arms"], decision=summary["decision"], posthoc=summary["posthoc"],
                          without_interface={g: v["counts"] for g, v in summary["without_interface_tasks"].items()},
                          decision_without_interface=summary["decision_without_interface"],
                          passing_check={k: v for k, v in summary["passing_check"].items() if k != "gap_located_to_edit"}),
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
