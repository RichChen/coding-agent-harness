"""Offline replay of masking rules on recorded runs: how many re-reads would have found the earlier result still
verbatim in the view, and how much larger each request would have been. No model calls, no Docker.

Runs and exclusions, read units, writes and the re-read test come from scripts/analysis/reread (discover,
outcome, call_io, overlaps, coverage, touches, view_states). Run journals are opened read-only.

    sim.py run [--workers 6] [--limit N] [--rules A,B,C,BC,N]   per-run results: out/sim_runs.json
    sim.py summary                                              tables and decision: out/sim_summary.json

Views. The raw history of step s is the prefix of the last state.saved message list with the length of the view
recorded at s (masking never adds or drops messages); its hash must equal the recorded raw_message_hash. Where it
does not, the recorded view itself (equal to raw when nothing was masked) or an earlier saved list is used if its
hash matches. Step 0 of the A5 b2 runs carries injected policy-candidate messages that are not in the raw history;
that view has no tool message, so it is used as is under every rule once a shorter prefix of the saved list
matches the hash. A step with no match drops the run (status excluded:sim_error).
- A (as run): src.runtime.window.WindowPolicyHooks, strategy masking, keep_recent=4, protection rules v1,
  preserve_tools empty. The simulated view must equal the recorded context.prepared view.
- B: the same with keep_recent=8.
- C: A, then every tool result whose call read a file (a lines/tail read unit) is put back verbatim until a later
  write touches one of its files (write test of the re-read analysis, including "ran code" and unknown writes).
  Search results and other results follow A. Implemented here, src/ is unchanged.
- BC: B plus the C protection.
- N: no masking (strategy none), the ceiling for both savings and size.

Metrics, per run and rule, on the recorded trajectory (the model is assumed to make the same calls):
- avoidable re-read: a re-read call (re-read analysis definition) whose repeated units are all "raw" in the
  simulated view of its step (earlier results still verbatim cover >= 80% of each unit); "raw or partial" too.
- input size: calibrated_v1 admission tokens of every request (src.runtime.window.request_size with the
  recorded tool count and tool bytes), reserved output from the recorded stats; rejected = above 96,000.
- cost: (a) upper bound, all input at the cache-miss price; (b) prefix-cache estimate: the leading messages equal
  to the previous request's view, plus tools, count as cache hits (floored to 64 tokens). Tokens are scaled to
  provider units with the run's ratio of provider prompt_tokens to calibrated_v1 tokens of the recorded views.
  Prices: scripts/cr2_cost.py (hit 0.04, miss 2.00, output 8.00 CNY per million). Output tokens are the
  recorded completion tokens under every rule.
- steps that only hold avoidable re-reads: an upper bound on model calls a rule could save; the input total
  without those steps is a rough net figure (it ignores that later views would change too).

Pre-registered decision (written before any B, C, BC or N result was computed):
- Decision population: CR-2 n (the prompt and tasks of the planned paid run). Other groups are reported only.
- Candidates: B, C, BC. A and N are references.
- T1: avoidable (raw) re-reads >= 50% of all re-reads, pooled over calls.
- T2: per-run input tokens (calibrated_v1, all requests) relative to A: median increase <= 30%.
- T3: per-run estimated cost with the prefix-cache estimate relative to A: median increase <= 30%.
- T4: no request above 96,000 calibrated_v1 tokens (including the output reserve).
- Recommend the candidate passing T1-T4 with the highest avoidable share; within 5 points, the one with the
  smaller T2 increase. If none passes, report which test failed and do not recommend paying for that rule.
- Primary metric of the paid run: let U = avoidable re-reads / tool calls of the recommended rule in CR-2 n (the
  most its re-read share could fall). U >= 15 points: keep the re-read share of tool calls and the thresholds of
  REREAD-AND-PROMPT-TIMING section 6 (B - A <= -10 points, upper bound < -5). U < 15: the -10 threshold is out
  of reach even with an unchanged trajectory; do not run that design as written.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import math
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
HARNESS = HERE.parents[2]
sys.path.insert(0, str(HARNESS))
sys.path.insert(0, str(HARNESS / "scripts" / "analysis" / "reread"))

import reread  # noqa: E402
from _load import payload, read_events, step_number  # noqa: E402
from reads import call_io  # noqa: E402

from src.runtime.hooks import HookContext  # noqa: E402
from src.runtime.window import WindowPolicy, WindowPolicyHooks, encoded, request_size, view_hash  # noqa: E402

OUT_DIR = HERE / "out"
LIMIT = 96_000
CACHE_BLOCK = 64
RULES = {
    "A": dict(keep_recent=4, content=False, mask=True),
    "B": dict(keep_recent=8, content=False, mask=True),
    "C": dict(keep_recent=4, content=True, mask=True),
    "BC": dict(keep_recent=8, content=True, mask=True),
    "N": dict(keep_recent=0, content=False, mask=False),
}
CANDIDATES = ("B", "C", "BC")
DECISION_GROUP = "CR-2 n"
T1_AVOIDABLE = 50.0
T2_INPUT = 30.0
T3_COST = 30.0
TIE_POINTS = 5.0
U_KEEP = 15.0
PRICE_HIT, PRICE_MISS, PRICE_OUT = 0.04, 2.00, 8.00  # CNY per million, scripts/cr2_cost.py


# --- views -----------------------------------------------------------------------------------------------------

class ToolStub:
    """ASCII stand-in for the run's tool list with the recorded count and serialized length."""

    def __init__(self, view0: list[dict], stats0: dict):
        if stats0.get("admission_method") != "utf8_bytes_plus_protocol_allowance":
            raise ValueError(f"unexpected admission method {stats0.get('admission_method')}")
        size, admission = stats0["serialized_bytes"], stats0["admission_input_tokens"]
        n = (admission - size - 512 - 32 * len(view0)) // 128
        body = size - len(encoded(view0)) - len(b'{"messages":') - len(b',"tools":') - 1
        letters = body - 3 * n - 1
        if n <= 0 or letters < 0:
            raise ValueError("cannot derive tools from recorded stats")
        self.tools = ["a" * (letters // n + (1 if i < letters % n else 0)) for i in range(n)]

    def size(self, view: list[dict], output: int, admission: str) -> dict:
        return request_size(view, self.tools, output, admission)


def _hooks(rule: dict) -> WindowPolicyHooks:
    strategy = "masking" if rule["mask"] else "none"
    return WindowPolicyHooks(WindowPolicy(strategy=strategy, max_tokens=10**9, keep_recent=rule["keep_recent"]))


def make_view(hooks: WindowPolicyHooks, raw: list[dict], protected_ids: set[str] | None) -> list[dict]:
    ctx = HookContext(extra={"tools": [], "max_output_tokens": 0})
    view = asyncio.run(hooks.before_llm(copy.deepcopy(raw), ctx))
    if protected_ids:
        for i, msg in enumerate(raw):
            if msg.get("role") == "tool" and msg.get("tool_call_id") in protected_ids:
                view[i] = copy.deepcopy(msg)
    return view


def _first_diff(a: list[dict], b: list[dict]) -> dict | None:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return dict(index=i, role=x.get("role"), recorded=str(x.get("content"))[:160],
                        simulated=str(y.get("content"))[:160])
    return None if len(a) == len(b) else dict(index=min(len(a), len(b)), length=[len(a), len(b)])


# --- per run ---------------------------------------------------------------------------------------------------

def _usage_by_step(run_dir: Path, events: list[dict]) -> dict[int, dict]:
    out = {}
    for e in events:
        if e["event_type"] != "model.settled":
            continue
        rec = (payload(run_dir, e) or {}).get("record") or {}
        usage = rec.get("usage") or {}
        if rec.get("success") and usage.get("prompt_tokens") is not None:
            out[step_number(e)] = usage
    return out


def analyze_run(args: tuple[dict, tuple[str, ...]]) -> dict:
    spec, rule_names = args
    run_dir = Path(spec["run_dir"])
    solved, status = reread.outcome(spec)
    row = dict(spec, run_dir=spec["run_dir"].replace("\\", "/"), solved=solved, status=status)
    if status.startswith("excluded"):
        return row
    try:
        return _analyze(run_dir, row, rule_names)
    except Exception as exc:  # recorded per run, reported in the summary
        return dict(row, status="excluded:sim_error", error=f"{type(exc).__name__}: {exc}")


def _raw_history(run_dir: Path, prepared: dict[int, dict], saved: list[dict]) -> tuple[dict[int, list], Counter]:
    """Raw history per step: the first candidate whose hash equals the recorded raw_message_hash. Candidates: prefix
    of the last saved message list, the recorded view itself (equal to raw when nothing was masked), prefixes of
    earlier saved lists. A view without tool messages that is longer than the raw history (messages injected into
    that request only) is used as is once a shorter prefix of the last saved list matches the hash: nothing in it
    can be masked, so it is the same under every rule."""
    loaded: list[list[dict]] = []
    sources = Counter()
    out = {}

    def snapshot(i: int) -> list[dict]:
        while len(loaded) <= i:
            loaded.append(payload(run_dir, saved[-1 - len(loaded)])["messages"])
        return loaded[i]

    for s, p in prepared.items():
        n, want = len(p["view"]), p["raw_message_hash"]
        found = None
        if saved and view_hash(snapshot(0)[:n]) == want:
            found, src = snapshot(0)[:n], "last_saved"
        elif view_hash(p["view"]) == want:
            found, src = p["view"], "recorded_view"
        else:
            for i in range(1, len(saved)):
                if len(snapshot(i)) >= n and view_hash(snapshot(i)[:n]) == want:
                    found, src = snapshot(i)[:n], "earlier_saved"
                    break
        if found is None and saved and not any(m.get("role") == "tool" for m in p["view"]):
            if any(view_hash(snapshot(0)[:k]) == want for k in range(1, n)):
                found, src = p["view"], "injected_view"
        if found is None:
            raise ValueError(f"no raw history matches step {s}")
        out[s] = found
        sources[src] += 1
    return out, sources


def _analyze(run_dir: Path, row: dict, rule_names: tuple[str, ...]) -> dict:
    events = read_events(run_dir)
    results = {e["tool_call_id"]: e for e in events if e["event_type"] == "tool.result"}
    prepared: dict[int, dict] = {}
    calls = []
    saved = []
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
    steps = sorted(prepared)
    usage = _usage_by_step(run_dir, events)
    raw_by_step, raw_sources = _raw_history(run_dir, prepared, saved)
    stub = ToolStub(prepared[steps[0]]["view"], prepared[steps[0]]["stats"])
    stub_mismatch = 0
    for s in steps:
        st = prepared[s]["stats"]
        got = stub.size(prepared[s]["view"], st["reserved_output_tokens"], "utf8_bytes")
        if (got["serialized_bytes"], got["admission_input_tokens"]) != (st["serialized_bytes"], st["admission_input_tokens"]):
            stub_mismatch += 1

    # writes, and for rule C which file reads are still unwritten at each step
    writes = []  # (call index, step, target)
    for c in calls:
        io = c["io"]
        writes += [(c["index"], c["step"], w) for w in io.writes]
        if io.unknown_write:
            writes.append((c["index"], c["step"], "*"))
        if io.ran:
            writes.append((c["index"], c["step"], reread.RAN))
    file_reads = [(c, [u for u in c["io"].units if u.kind != "search"]) for c in calls]
    file_reads = [(c, us) for c, us in file_reads if us]

    def content_protected(s: int) -> set[str]:
        keep = set()
        for c, units in file_reads:
            if c["step"] is None or c["step"] >= s:
                continue
            if not any(wi > c["index"] and ws is not None and ws < s and reread.touches(w, u)
                       for wi, ws, w in writes for u in units):
                keep.add(c["call_id"])
        return keep

    # simulated views per rule and step
    rec_states = {s: reread.view_states(prepared[s]["view"]) for s in steps}
    states = {r: {} for r in rule_names}
    per_rule = {}
    replication = dict(steps=len(steps), view_equal=0, states_equal=0, diffs=[])
    for r in rule_names:
        rule = RULES[r]
        hooks = _hooks(rule)
        prev_hashes: list[str] = []
        tokens, hits, rejected, max_tokens = [], [], 0, 0
        for s in steps:
            raw = raw_by_step[s]
            view = make_view(hooks, raw, content_protected(s) if rule["content"] else None)
            states[r][s] = reread.view_states(view)
            out_tokens = prepared[s]["stats"]["reserved_output_tokens"]
            t = stub.size(view, out_tokens, "calibrated_v1")["admission_input_tokens"]
            if t + out_tokens > LIMIT:
                rejected += 1
            max_tokens = max(max_tokens, t)
            hashes = [view_hash(m) for m in view]
            k = 0
            while k < min(len(hashes), len(prev_hashes)) and hashes[k] == prev_hashes[k]:
                k += 1
            hit = 0
            if prev_hashes:
                hit = stub.size(view[:k], 0, "calibrated_v1")["admission_input_tokens"]
                hit = min(t, hit // CACHE_BLOCK * CACHE_BLOCK)
            prev_hashes = hashes
            tokens.append(t)
            hits.append(hit)
            if r == "A":
                if view == prepared[s]["view"]:
                    replication["view_equal"] += 1
                elif len(replication["diffs"]) < 3:
                    replication["diffs"].append(dict(step=s, **(_first_diff(prepared[s]["view"], view) or {})))
                if states[r][s] == rec_states[s]:
                    replication["states_equal"] += 1
        per_rule[r] = dict(input_tokens=tokens, hit_tokens=hits, rejected=rejected, max_request_tokens=max_tokens)

    # recorded views: calibrated tokens and the same cache estimate, next to provider usage
    rec = dict(tokens=[], hits=[], prompt=[], prompt_hit=[], completion=[], steps_with_usage=[])
    prev_hashes = []
    for s in steps:
        view = prepared[s]["view"]
        t = stub.size(view, prepared[s]["stats"]["reserved_output_tokens"], "calibrated_v1")["admission_input_tokens"]
        hashes = [view_hash(m) for m in view]
        k = 0
        while k < min(len(hashes), len(prev_hashes)) and hashes[k] == prev_hashes[k]:
            k += 1
        hit = min(t, stub.size(view[:k], 0, "calibrated_v1")["admission_input_tokens"] // CACHE_BLOCK * CACHE_BLOCK) \
            if prev_hashes else 0
        prev_hashes = hashes
        rec["tokens"].append(t)
        rec["hits"].append(hit)
        u = usage.get(s)
        rec["prompt"].append(u.get("prompt_tokens") if u else None)
        rec["prompt_hit"].append(u.get("prompt_cache_hit_tokens") if u else None)
        rec["completion"].append(u.get("completion_tokens") if u else None)

    # re-reads (same test as the re-read analysis) and their view state under each rule
    lengths: dict[str, int] = {}
    reads = []
    write_list = []
    rr_rows = []
    calls_by_step = defaultdict(list)
    for c in calls:
        io = c["io"]
        lengths.update(io.file_lengths)
        repeated = []
        for u in io.units:
            overlapping = [(i, s, p, cid) for i, s, p, cid in reads if s < c["step"] and reread.overlaps(u, p, lengths)]
            fresh = [(i, s, p, cid) for i, s, p, cid in overlapping
                     if not any(i < wi < c["index"] and reread.touches(w, u) for wi, w in write_list)]
            if reread.coverage(u, [p for _, _, p, _ in fresh], lengths) >= reread.MIN_OVERLAP:
                repeated.append((u, [(p, cid) for _, _, p, cid in fresh]))
        rec_row = dict(call_id=c["call_id"], step=c["step"], reread=bool(repeated))
        if repeated:
            for r, st_by_step in list(states.items()) + [("REC", rec_states)]:
                st = st_by_step.get(c["step"])
                if st is None:
                    rec_row[r] = "no_view"
                    continue
                unit_states = []
                for u, priors in repeated:
                    raw_cover = reread.coverage(u, [p for p, cid in priors if st.get(cid) == "raw"], lengths)
                    if raw_cover >= reread.COVER:
                        unit_states.append("raw")
                    elif raw_cover >= reread.MIN_OVERLAP:
                        unit_states.append("partial")
                    elif any(st.get(cid) == "elided" for _, cid in priors):
                        unit_states.append("elided")
                    else:
                        unit_states.append("absent")
                rec_row[r] = ("raw" if all(x == "raw" for x in unit_states)
                              else "partial" if {"raw", "partial"} & set(unit_states)
                              else "elided" if "elided" in unit_states else "absent")
        rr_rows.append(rec_row)
        calls_by_step[c["step"]].append(rec_row)
        for u in io.units:
            reads.append((c["index"], c["step"], u, c["call_id"]))
        write_list += [(c["index"], w) for w in io.writes]
        if io.unknown_write:
            write_list.append((c["index"], "*"))
        if io.ran:
            write_list.append((c["index"], reread.RAN))

    rereads = [x for x in rr_rows if x["reread"]]
    for r in rule_names:
        d = per_rule[r]
        d["avoidable_raw"] = sum(x[r] == "raw" for x in rereads)
        d["avoidable_raw_or_partial"] = sum(x[r] in ("raw", "partial") for x in rereads)
        skip = {s for s, cs in calls_by_step.items() if cs and all(x["reread"] and x[r] == "raw" for x in cs)}
        d["avoidable_only_steps"] = len(skip)
        d["input_tokens_without_avoidable_steps"] = sum(t for s, t in zip(steps, d["input_tokens"]) if s not in skip)
    row.update(
        model_steps=len(steps), tool_calls=len(calls), reread_calls=len(rereads),
        recorded_states=dict(Counter(x["REC"] for x in rereads)),
        reread_call_ids=[x["call_id"] for x in rereads],
        recorded_state_by_call={x["call_id"]: x["REC"] for x in rereads},
        raw_sources=dict(raw_sources), tool_stub_mismatch=stub_mismatch, tool_count=len(stub.tools),
        replication=replication, recorded=rec, rules=per_rule,
    )
    return row


# --- commands --------------------------------------------------------------------------------------------------

def cmd_run(args) -> None:
    rule_names = tuple(args.rules.split(","))
    specs = reread.discover()
    if args.limit:
        seen = Counter()
        specs = [s for s in specs if seen.update([s["experiment"]]) or seen[s["experiment"]] <= args.limit]
    print("runs", dict(Counter(s["experiment"] for s in specs)), "rules", rule_names, flush=True)
    OUT_DIR.mkdir(exist_ok=True)
    name = args.out or "sim_runs.json"
    partial = OUT_DIR / (name + ".partial.jsonl")
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
                print("simulated", i + 1, flush=True)
    rows = [done[s["run_dir"].replace("\\", "/")] for s in specs]
    (OUT_DIR / name).write_text(json.dumps(rows, ensure_ascii=False) + "\n", encoding="utf-8")
    partial.unlink()
    print("wrote", OUT_DIR / name, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--workers", type=int, default=6)
    run.add_argument("--limit", type=int)
    run.add_argument("--rules", default=",".join(RULES))
    run.add_argument("--out")
    run.set_defaults(fn=cmd_run)
    summ = sub.add_parser("summary")
    summ.set_defaults(fn=lambda a: __import__("summarize").main())
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
