"""Counterfactual pruning (post hoc, not pre-registered): replay each recorded trajectory under a masking rule and
assume the model skips every re-read whose content is still verbatim in its view.

Walk the recorded steps in order with a pruned history H (start: the raw history of the first step). At each step,
build the view of H under the rule and classify the step's calls with the re-read test of sim.py, priors limited
to calls that were kept. A call is skipped when it is a re-read, every repeated unit is "raw" in that view, and it
neither writes nor runs code. Skipped calls and their results leave H; the step's assistant message keeps only the
other calls. A step whose calls are all skipped makes no request: the next kept step is asked with the same H.
Every other message of the step (harness notes) stays. Input tokens, the prefix-cache estimate and the cost are
computed as in sim.py / summarize.py on the views that are actually sent; output tokens are the recorded
completion tokens of the kept steps.

Self-check: rule A without pruning must rebuild the recorded raw history of every step exactly.

    pruned.py [--workers 6] [--rules A,B,C,BC,N] [--experiments CR-2]   per-run results: out/pruned_runs.json
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

import sim
from sim import CACHE_BLOCK, LIMIT, RULES, ToolStub, _hooks, _raw_history, _usage_by_step, make_view, reread
from sim import call_io, payload, read_events, step_number, view_hash

PRUNE_RULES = ("A", "B", "C", "BC", "N")


def _load(run_dir: Path):
    events = read_events(run_dir)
    results = {e["tool_call_id"]: e for e in events if e["event_type"] == "tool.result"}
    prepared, calls, saved = {}, [], []
    for e in events:
        kind = e["event_type"]
        if kind == "context.prepared":
            p = payload(run_dir, e)
            step = step_number(e)
            prepared[step if step is not None else p["step"]] = p
        elif kind == "state.saved":
            saved.append(e)
        elif kind == "tool.intent":
            p = payload(run_dir, e)
            res = results.get(e["tool_call_id"])
            rp = payload(run_dir, res) if res else {}
            output = rp.get("output") if isinstance(rp.get("output"), str) else None
            calls.append(dict(index=len(calls), step=step_number(e), call_id=e["tool_call_id"], name=p["name"],
                              io=call_io(p["name"], p.get("arguments") or {}, output, bool(rp.get("success")))))
    return events, prepared, calls, saved


def _replay(rule_name: str, prune: bool, steps, segments, first_raw, calls_by_step, stub, prepared, usage):
    rule = RULES[rule_name]
    hooks = _hooks(rule)
    history = list(first_raw)
    lengths: dict[str, int] = {}
    reads, writes = [], []  # kept calls only
    file_reads = []  # (call, file units) of kept calls, for rule C
    prev_hashes: list[str] = []
    out = dict(tokens=[], hits=[], completion=0, requests=0, skipped_calls=0, skipped_steps=0, rejected=0,
               histories=[])
    for s in steps:
        protected = None
        if rule["content"]:
            protected = {c["call_id"] for c, units in file_reads
                         if not any(wi > c["index"] and reread.touches(w, u) for wi, w in writes for u in units)}
        view = make_view(hooks, history, protected)
        states = reread.view_states(view)
        skip = set()
        if prune:
            for c in calls_by_step.get(s, []):
                io = c["io"]
                lengths.update(io.file_lengths)
                if io.writes or io.unknown_write or io.ran or not io.units:
                    continue
                ok = True
                for u in io.units:
                    fresh = [(p, cid) for i, st, p, cid in reads if st < s and reread.overlaps(u, p, lengths)
                             and not any(i < wi < c["index"] and reread.touches(w, u) for wi, w in writes)]
                    if reread.coverage(u, [p for p, _ in fresh], lengths) < reread.MIN_OVERLAP:
                        ok = False  # not a repeat
                        break
                    if reread.coverage(u, [p for p, cid in fresh if states.get(cid) == "raw"], lengths) < reread.COVER:
                        ok = False
                        break
                if ok:
                    skip.add(c["call_id"])
        step_calls = calls_by_step.get(s, [])
        all_skipped = bool(step_calls) and all(c["call_id"] in skip for c in step_calls)
        if not all_skipped:
            if not prune:
                out["histories"].append(list(history))
            out_tokens = prepared[s]["stats"]["reserved_output_tokens"]
            t = stub.size(view, out_tokens, "calibrated_v1")["admission_input_tokens"]
            out["rejected"] += t + out_tokens > LIMIT
            hashes = [view_hash(m) for m in view]
            k = 0
            while k < min(len(hashes), len(prev_hashes)) and hashes[k] == prev_hashes[k]:
                k += 1
            hit = min(t, stub.size(view[:k], 0, "calibrated_v1")["admission_input_tokens"] // CACHE_BLOCK
                      * CACHE_BLOCK) if prev_hashes else 0
            prev_hashes = hashes
            out["tokens"].append(t)
            out["hits"].append(hit)
            out["completion"] += (usage.get(s) or {}).get("completion_tokens") or 0
            out["requests"] += 1
        else:
            out["skipped_steps"] += 1
        out["skipped_calls"] += len(skip)
        for msg in segments[s]:
            role = msg.get("role")
            if role == "tool" and msg.get("tool_call_id") in skip:
                continue
            if role == "assistant" and msg.get("tool_calls"):
                if all_skipped:
                    continue
                if skip:
                    msg = dict(msg, tool_calls=[tc for tc in msg["tool_calls"] if tc.get("id") not in skip])
            history.append(msg)
        for c in step_calls:
            if c["call_id"] in skip:
                continue
            io = c["io"]
            lengths.update(io.file_lengths)
            for u in io.units:
                reads.append((c["index"], c["step"], u, c["call_id"]))
            units = [u for u in io.units if u.kind != "search"]
            if units:
                file_reads.append((c, units))
            writes.extend((c["index"], w) for w in io.writes)
            if io.unknown_write:
                writes.append((c["index"], "*"))
            if io.ran:
                writes.append((c["index"], reread.RAN))
    return out


def analyze_run(args) -> dict:
    spec, rule_names = args
    run_dir = Path(spec["run_dir"])
    solved, status = reread.outcome(spec)
    row = dict(spec, run_dir=spec["run_dir"].replace("\\", "/"), solved=solved, status=status)
    if status.startswith("excluded"):
        return row
    try:
        return _analyze(run_dir, row, rule_names)
    except Exception as exc:  # recorded per run, reported in the summary
        return dict(row, status="excluded:prune_error", error=f"{type(exc).__name__}: {exc}")


def _analyze(run_dir: Path, row: dict, rule_names) -> dict:
    events, prepared, calls, saved = _load(run_dir)
    steps = sorted(prepared)
    raw_by_step, sources = _raw_history(run_dir, prepared, saved)
    if set(sources) != {"last_saved"}:
        return dict(row, status="excluded:not_prefix_history")
    last = payload(run_dir, saved[-1])["messages"]
    bounds = [len(prepared[s]["view"]) for s in steps] + [len(last)]
    segments = {s: last[bounds[i]:bounds[i + 1]] for i, s in enumerate(steps)}
    calls_by_step = {}
    for c in calls:
        calls_by_step.setdefault(c["step"], []).append(c)
    stub = ToolStub(prepared[steps[0]]["view"], prepared[steps[0]]["stats"])
    usage = _usage_by_step(run_dir, events)
    first_raw = raw_by_step[steps[0]]

    check = _replay("A", False, steps, segments, first_raw, calls_by_step, stub, prepared, usage)
    rebuilt_ok = sum(h == raw_by_step[s] for h, s in zip(check["histories"], steps))

    pairs = [((usage.get(s) or {}).get("prompt_tokens"), t) for s, t in zip(steps, check["tokens"])]
    pairs = [(p, t) for p, t in pairs if p is not None]
    scale = sum(p for p, _ in pairs) / sum(t for _, t in pairs) if pairs else 1.0

    def cost(r):
        total, hit = scale * sum(r["tokens"]), scale * sum(r["hits"])
        return (hit * sim.PRICE_HIT + (total - hit) * sim.PRICE_MISS + r["completion"] * sim.PRICE_OUT) / 1e6

    res = {"recorded_A": dict(input_tokens=sum(check["tokens"]), requests=check["requests"], cost=cost(check),
                              tool_calls=len(calls), rejected=check["rejected"])}
    for r in rule_names:
        x = _replay(r, True, steps, segments, first_raw, calls_by_step, stub, prepared, usage)
        res[r] = dict(input_tokens=sum(x["tokens"]), requests=x["requests"], cost=cost(x),
                      skipped_calls=x["skipped_calls"], skipped_steps=x["skipped_steps"], rejected=x["rejected"])
    return dict(row, steps=len(steps), rebuilt_ok=rebuilt_ok, rebuilt_steps=len(check["histories"]), pruned=res)


def summarize(rows: list[dict], rule_names) -> dict:
    kept = [r for r in rows if not r["status"].startswith("excluded")]
    groups: dict[str, list] = {}
    for r in kept:
        groups.setdefault(f"{r['experiment']} {r['config']}", []).append(r)
        groups.setdefault(f"{r['experiment']} all", []).append(r)
    out = dict(runs=len(kept), excluded=dict(Counter(r["status"] for r in rows if r["status"].startswith("excluded"))),
               rebuilt_steps_equal=sum(r["rebuilt_ok"] for r in kept), steps=sum(r["rebuilt_steps"] for r in kept),
               groups={})
    for g, rs in sorted(groups.items()):
        base = [r["pruned"]["recorded_A"] for r in rs]
        d = dict(runs=len(rs), requests=sum(b["requests"] for b in base), tool_calls=sum(b["tool_calls"] for b in base),
                 cost_cny_recorded_A=round(sum(b["cost"] for b in base), 2), rules={})
        for rule in rule_names:
            xs = [r["pruned"][rule] for r in rs]
            inc_in = [100 * (x["input_tokens"] / b["input_tokens"] - 1) for x, b in zip(xs, base)]
            inc_cost = [100 * (x["cost"] / b["cost"] - 1) for x, b in zip(xs, base)]
            d["rules"][rule] = dict(
                skipped_calls_pct=round(100 * sum(x["skipped_calls"] for x in xs) / max(1, d["tool_calls"]), 1),
                requests_saved_pct=round(100 * (1 - sum(x["requests"] for x in xs) / d["requests"]), 1),
                input_increase_pct=dict(median=round(float(np.median(inc_in)), 1),
                                        p90=round(float(np.percentile(inc_in, 90)), 1)),
                cost_increase_pct=dict(median=round(float(np.median(inc_cost)), 1),
                                       p10=round(float(np.percentile(inc_cost, 10)), 1),
                                       p90=round(float(np.percentile(inc_cost, 90)), 1)),
                runs_cheaper_pct=round(100 * sum(c < 0 for c in inc_cost) / len(inc_cost), 1),
                cost_cny=round(sum(x["cost"] for x in xs), 2),
                rejected=sum(x["rejected"] for x in xs))
        out["groups"][g] = d
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--rules", default=",".join(PRUNE_RULES))
    parser.add_argument("--experiments")
    args = parser.parse_args()
    rule_names = tuple(args.rules.split(","))
    specs = reread.discover()
    if args.experiments:
        wanted = set(args.experiments.split(","))
        specs = [s for s in specs if s["experiment"] in wanted]
    print("runs", dict(Counter(s["experiment"] for s in specs)), flush=True)
    sim.OUT_DIR.mkdir(exist_ok=True)
    partial = sim.OUT_DIR / "pruned_runs.json.partial.jsonl"
    done = {}
    if partial.exists():
        for line in partial.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                done[row["run_dir"]] = row
    todo = [s for s in specs if s["run_dir"].replace("\\", "/") not in done]
    print("resuming with", len(done), "done,", len(todo), "to do", flush=True)
    with ProcessPoolExecutor(args.workers) as pool, partial.open("a", encoding="utf-8") as fh:
        futures = [pool.submit(analyze_run, (s, rule_names)) for s in todo]
        for i, fut in enumerate(as_completed(futures)):
            row = fut.result()
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            done[row["run_dir"]] = row
            if (i + 1) % 100 == 0:
                print("pruned", i + 1, flush=True)
    rows = [done[s["run_dir"].replace("\\", "/")] for s in specs]
    (sim.OUT_DIR / "pruned_runs.json").write_text(json.dumps(rows, ensure_ascii=False) + "\n", encoding="utf-8")
    summary = summarize(rows, rule_names)
    (sim.OUT_DIR / "pruned_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n",
                                                     encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "groups"}, ensure_ascii=False), flush=True)
    partial.unlink()
    print("wrote", sim.OUT_DIR / "pruned_summary.json", flush=True)


if __name__ == "__main__":
    main()
