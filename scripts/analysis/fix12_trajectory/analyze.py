"""Group comparisons over out/runs.json (from extract.py). Descriptive only; written after the pilot results were known.

Definitions
- repeat: a call whose key (tool name + arguments) equals an earlier call's key in the same run.
- tail_repeat: repeat share among the last TAIL calls; loop run: tail_repeat >= LOOP_SHARE (as fix12_no_edit).
- streak: longest run of consecutive identical keys.
- loop onset: first request of the longest suffix in which every call is a repeat (loop runs only).
- retry-after-failure: a failed call immediately followed by the identical call.
- masked re-read (A's masking keeps the last KEEP tool results): a repeated read/search whose previous identical call
  is more than KEEP tool results back, so its output had been replaced by a placeholder in A; same measure in U/U+T
  for comparison (there it was still visible).
- silent request: an assistant turn with tool calls and no visible text.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
GROUPS = ("A", "U", "UT")
TAIL, LOOP_SHARE, KEEP, A_CAP = 20, 0.5, 4, 40
KINDS = ("read", "search", "list", "run", "test", "edit", "other")


def repeats(calls: list[dict]) -> list[bool]:
    seen, out = set(), []
    for c in calls:
        out.append(c["key"] in seen)
        seen.add(c["key"])
    return out


def features(r: dict) -> dict:
    calls, reqs = r["calls"], r["requests"]
    rep = repeats(calls)
    tail = rep[-TAIL:]
    tail_repeat = sum(tail) / len(tail) if tail else 0.0
    streak = best = 1 if calls else 0
    for a, b in zip(calls, calls[1:]):
        streak = streak + 1 if a["key"] == b["key"] else 1
        best = max(best, streak)
    suffix = 0
    for x in reversed(rep):
        if not x:
            break
        suffix += 1
    onset = calls[-suffix]["request"] if suffix else None
    failed = [i for i, c in enumerate(calls[:-1]) if c["failed"]]
    retry = sum(1 for i in failed if calls[i + 1]["key"] == calls[i]["key"])
    last_key, masked_rereads, visible_rereads = {}, 0, 0
    for i, c in enumerate(calls):
        if c["kind"] in ("read", "search") and c["key"] in last_key:
            if i - last_key[c["key"]] > KEEP:
                masked_rereads += 1
            else:
                visible_rereads += 1
        last_key[c["key"]] = i
    edits = [c["request"] for c in calls if c["kind"] == "edit"]
    tests = [c["request"] for c in calls if c["kind"] == "test"]
    answered = [q for q in reqs if "content_chars" in q]
    silent = [q for q in answered if q["tool_calls"] and q["content_chars"] == 0]
    with_reasoning = [q for q in reqs if (q.get("reasoning") or 0) > 0]
    first_edit = r["first_edit"]
    kinds = Counter(c["kind"] for c in calls)
    return dict(
        requests=len(reqs), calls=len(calls), calls_per_request=len(calls) / max(len(reqs), 1),
        kinds={k: kinds[k] / max(len(calls), 1) for k in KINDS},
        repeat_share=sum(rep) / max(len(rep), 1), tail_repeat=tail_repeat, loop=tail_repeat >= LOOP_SHARE,
        streak=best, loop_onset=onset if tail_repeat >= LOOP_SHARE else None,
        failed_calls=len(failed) + (1 if calls and calls[-1]["failed"] else 0),
        retry_after_failure=retry / len(failed) if failed else None,
        masked_rereads=masked_rereads, visible_rereads=visible_rereads,
        edits=len(edits), tests=len(tests), tested_after_last_edit=bool(edits and tests and tests[-1] > edits[-1]),
        tested_before_first_edit=bool(edits and tests and tests[0] < edits[0]),
        located_to_edit=(first_edit - r["located"]) if first_edit and r["located"] else None,
        silent_share=len(silent) / max(len(answered), 1),
        silent_share_tail=(sum(1 for q in answered[-TAIL:] if q["tool_calls"] and q["content_chars"] == 0)
                           / max(len(answered[-TAIL:]), 1)),
        content_chars=mean(q["content_chars"] for q in answered) if answered else 0,
        reasoning_tokens=sum(q.get("reasoning") or 0 for q in reqs),
        reasoning_share=len(with_reasoning) / max(len(reqs), 1),
        reasoning_before_edit=(mean(q.get("reasoning") or 0 for q in reqs[:first_edit - 1])
                               if first_edit and first_edit > 1 else None),
        reasoning_after_edit=(mean(q.get("reasoning") or 0 for q in reqs[first_edit - 1:])
                              if first_edit else None),
        completion_tokens=sum(q.get("completion") or 0 for q in reqs),
        max_prompt=max((q.get("prompt") or 0 for q in reqs), default=0),
        masked_requests=sum(1 for q in reqs if q["masked"]),
        elapsed_s=sum(q.get("elapsed_ms") or 0 for q in reqs) / 1000,
    )


def stat(values: list) -> dict | None:
    v = [x for x in values if x is not None]
    if not v:
        return None
    return dict(n=len(v), mean=round(mean(v), 3), median=round(median(v), 3))


def group_table(rows: list[dict]) -> dict:
    out = {}
    for g in GROUPS:
        out[g] = {}
        for label, sel in (("all", lambda r: True), ("pass", lambda r: r["passed"]),
                           ("fail", lambda r: not r["passed"])):
            rs = [r for r in rows if r["group"] == g and sel(r)]
            f = [r["f"] for r in rs]
            scalar = [k for k, v in f[0].items() if isinstance(v, (int, float)) and not isinstance(v, bool)] if f else []
            t = {k: stat([x[k] for x in f]) for k in scalar}
            t.update(runs=len(rs), loop_runs=sum(x["loop"] for x in f),
                     tested_after_last_edit=sum(x["tested_after_last_edit"] for x in f),
                     tested_before_first_edit=sum(x["tested_before_first_edit"] for x in f),
                     retry_after_failure=stat([x["retry_after_failure"] for x in f]),
                     located_to_edit=stat([x["located_to_edit"] for x in f]),
                     reasoning_before_edit=stat([x["reasoning_before_edit"] for x in f]),
                     reasoning_after_edit=stat([x["reasoning_after_edit"] for x in f]),
                     loop_onset=stat([x["loop_onset"] for x in f]),
                     kinds={k: round(mean(x["kinds"][k] for x in f), 3) for k in KINDS} if f else None,
                     cost=round(sum(r["cost"] for r in rs), 2))
            out[g][label] = t
    return out


def cap_decomposition(rows: list[dict]) -> dict:
    """What lifting A's 40-call cap could have contributed: U/U+T passes by request count, and A's cap stops."""
    out = {}
    for g in ("U", "UT"):
        p = [r for r in rows if r["group"] == g and r["passed"]]
        out[g] = dict(passes=len(p), within_cap=sum(r["f"]["requests"] <= A_CAP for r in p),
                      over_cap=sum(r["f"]["requests"] > A_CAP for r in p))
    a = [r for r in rows if r["group"] == "A"]
    out["A_stops"] = dict(Counter(r["stop"] for r in a if not r["passed"]))
    a_cap_fail = {(r["task"], r["repeat"]) for r in a if not r["passed"] and r["stop"] == "max_model_calls"}
    u = {(r["task"], r["repeat"]): r for r in rows if r["group"] == "U"}
    same = [u[k] for k in a_cap_fail if k in u]
    out["U_on_A_cap_failures"] = dict(n=len(same), passed=sum(r["passed"] for r in same),
                                      passed_over_cap=sum(r["passed"] and r["f"]["requests"] > A_CAP for r in same),
                                      looped=sum(r["f"]["loop"] for r in same))
    return out


def first_window(rows: list[dict]) -> dict:
    """A against the first A_CAP requests of U and U+T (same budget window)."""
    out = {}
    for g in GROUPS:
        rs = [r for r in rows if r["group"] == g]
        vals = defaultdict(list)
        for r in rs:
            calls = [c for c in r["calls"] if c["request"] <= A_CAP]
            rep = repeats(calls)
            vals["repeat_share"].append(sum(rep) / max(len(rep), 1))
            vals["located"].append(bool(r["located"] and r["located"] <= A_CAP))
            vals["edited"].append(bool(r["first_edit"] and r["first_edit"] <= A_CAP))
            vals["calls"].append(len(calls))
            vals["passed_within"].append(r["passed"] and r["f"]["requests"] <= A_CAP)
        out[g] = dict(runs=len(rs), repeat_share=round(mean(vals["repeat_share"]), 3),
                      located=sum(vals["located"]), edited=sum(vals["edited"]),
                      calls=round(mean(vals["calls"]), 1), passed_within=sum(vals["passed_within"]))
    return out


def paired(rows: list[dict]) -> dict:
    by = defaultdict(lambda: {g: 0 for g in GROUPS})
    for r in rows:
        by[r["task"]][r["group"]] += r["passed"]
    out = {}
    for x, y in (("A", "U"), ("U", "UT"), ("A", "UT")):
        out[f"{x}_vs_{y}"] = dict(better=sum(v[y] > v[x] for v in by.values()),
                                  worse=sum(v[y] < v[x] for v in by.values()),
                                  same=sum(v[y] == v[x] for v in by.values()))
    out["per_task"] = {t: by[t] for t in sorted(by)}
    return out


def loop_vs_ut(rows: list[dict]) -> dict:
    """Tasks where U looped in some repeat: what U+T did on them."""
    loop_tasks = sorted({r["task"] for r in rows if r["group"] == "U" and r["f"]["loop"]})
    ut = [r for r in rows if r["group"] == "UT" and r["task"] in loop_tasks]
    return dict(tasks=len(loop_tasks), ut_runs=len(ut), ut_passed=sum(r["passed"] for r in ut),
                ut_loops=sum(r["f"]["loop"] for r in ut), ut_max_tail_repeat=max(r["f"]["tail_repeat"] for r in ut),
                ut_max_streak=max(r["f"]["streak"] for r in ut))


def main() -> None:
    rows = json.loads((OUT / "runs.json").read_text(encoding="utf-8"))
    for r in rows:
        r["f"] = features(r)
    summary = dict(groups=group_table(rows), cap=cap_decomposition(rows), first_window=first_window(rows),
                   paired=paired(rows), u_loop_tasks=loop_vs_ut(rows),
                   runs=[dict(task=r["task"], group=r["group"], repeat=r["repeat"], passed=r["passed"],
                              stop=r["stop"], cls=r["cls"], **{k: v for k, v in r["f"].items() if k != "kinds"})
                         for r in rows])
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k not in ("runs", "groups")}, indent=1)[:6000])


if __name__ == "__main__":
    main()
