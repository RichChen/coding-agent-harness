"""Tables and the pre-registered decision from out/sim_runs.json (see sim.py). Writes out/sim_summary.json."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np

import sim

REREAD_OUT = sim.HARNESS / "scripts" / "analysis" / "reread" / "out" / "reread_runs.json"
REREAD_CALLS = sim.HARNESS / ".tmp" / "reread" / "calls.jsonl"


def pct(a: float, b: float) -> float | None:
    return round(100 * a / b, 1) if b else None


def q(values, p):
    return round(float(np.percentile(values, p)), 1) if len(values) else None


def run_cost(row: dict, rule: str | None) -> dict:
    """CNY per run: upper bound (all input at the miss price) and with the prefix-cache estimate, in provider
    token units (calibrated_v1 tokens times the run's provider / calibrated ratio on the recorded views)."""
    rec = row["recorded"]
    pairs = [(t, p) for t, p in zip(rec["tokens"], rec["prompt"]) if p is not None]
    scale = sum(p for _, p in pairs) / sum(t for t, _ in pairs) if pairs else 1.0
    completion = sum(c or 0 for c in rec["completion"])
    if rule is None:
        tokens, hits = rec["tokens"], rec["hits"]
    else:
        tokens, hits = row["rules"][rule]["input_tokens"], row["rules"][rule]["hit_tokens"]
    total, hit = scale * sum(tokens), scale * sum(hits)
    out = completion * sim.PRICE_OUT
    return dict(upper=(total * sim.PRICE_MISS + out) / 1e6,
                cached=(hit * sim.PRICE_HIT + (total - hit) * sim.PRICE_MISS + out) / 1e6,
                input_tokens=sum(tokens))


def actual_cost(row: dict) -> float:
    rec = row["recorded"]
    total = 0.0
    for p, h, c in zip(rec["prompt"], rec["prompt_hit"], rec["completion"]):
        if p is None:
            continue
        h = h or 0
        total += h * sim.PRICE_HIT + (p - h) * sim.PRICE_MISS + (c or 0) * sim.PRICE_OUT
    return total / 1e6


def validation(rows: list[dict]) -> dict:
    steps = sum(r["replication"]["steps"] for r in rows)
    out = dict(
        runs=len(rows), steps=steps,
        view_equal=sum(r["replication"]["view_equal"] for r in rows),
        states_equal=sum(r["replication"]["states_equal"] for r in rows),
        runs_with_view_difference=sum(r["replication"]["view_equal"] != r["replication"]["steps"] for r in rows),
        raw_history_source_steps={k: sum(r["raw_sources"].get(k, 0) for r in rows)
                                  for k in ("last_saved", "recorded_view", "earlier_saved", "injected_view")},
        tool_stub_mismatch_steps=sum(r["tool_stub_mismatch"] for r in rows),
        difference_examples=[dict(run_dir=r["run_dir"], **d) for r in rows for d in r["replication"]["diffs"]][:10],
    )
    # re-read test against the re-read analysis
    ref_runs = {r["run_dir"]: r for r in json.loads(REREAD_OUT.read_text(encoding="utf-8"))}
    out["reread_count_equal_runs"] = sum(ref_runs[r["run_dir"]]["reread_calls"] == r["reread_calls"] for r in rows)
    if REREAD_CALLS.is_file():
        kept_dirs = {r["run_dir"] for r in rows}
        ref_state = {}
        with REREAD_CALLS.open(encoding="utf-8") as fh:
            for line in fh:
                c = json.loads(line)
                run_dir = c["run_dir"].replace("\\", "/")
                if c["reread"] and run_dir in kept_dirs:
                    ref_state[(run_dir, c["call_id"])] = c["view_state"]
        mine = {(r["run_dir"], cid): s for r in rows for cid, s in r["recorded_state_by_call"].items()}
        out["reread_calls_reference"] = len(ref_state)
        out["reread_calls_here"] = len(mine)
        out["reread_calls_same_set"] = set(ref_state) == set(mine)
        out["view_state_equal"] = sum(ref_state.get(k) == v for k, v in mine.items())
    # token estimate and cache estimate against provider usage, recorded views
    ratios, est_hit, est_tot, act_hit, act_tot = [], 0, 0, 0, 0
    per_exp = defaultdict(lambda: [0, 0, 0, 0])
    for r in rows:
        rec = r["recorded"]
        for i, (t, h, p, ph) in enumerate(zip(rec["tokens"], rec["hits"], rec["prompt"], rec["prompt_hit"])):
            if p is None:
                continue
            ratios.append(p / t)
            if i == 0 or ph is None:
                continue
            est_hit += h
            est_tot += t
            act_hit += ph
            act_tot += p
            e = per_exp[r["experiment"]]
            e[0] += h
            e[1] += t
            e[2] += ph
            e[3] += p
    out["provider_over_calibrated"] = dict(n=len(ratios), median=round(float(np.median(ratios)), 3),
                                           p10=round(float(np.percentile(ratios, 10)), 3),
                                           p90=round(float(np.percentile(ratios, 90)), 3),
                                           max=round(float(max(ratios)), 3))
    out["cache_hit_share_from_second_request"] = dict(
        estimated=pct(est_hit, est_tot), provider=pct(act_hit, act_tot),
        by_experiment={k: dict(estimated=pct(v[0], v[1]), provider=pct(v[2], v[3])) for k, v in sorted(per_exp.items())})
    est_a = sum(run_cost(r, None)["cached"] for r in rows)
    act = sum(actual_cost(r) for r in rows)
    out["cost_recorded_views"] = dict(estimated_cny=round(est_a, 2), provider_usage_cny=round(act, 2),
                                      ratio=round(est_a / act, 3) if act else None)
    return out


def group_table(rows: list[dict], rules: list[str]) -> dict:
    tool = sum(r["tool_calls"] for r in rows)
    rr = sum(r["reread_calls"] for r in rows)
    steps = sum(r["model_steps"] for r in rows)
    base = {id(r): run_cost(r, "A") for r in rows}
    table = dict(runs=len(rows), tool_calls=tool, reread_calls=rr, reread_pct_of_tool_calls=pct(rr, tool),
                 requests=steps, rules={})
    for rule in rules:
        d = [r["rules"][rule] for r in rows]
        raw = sum(x["avoidable_raw"] for x in d)
        rawp = sum(x["avoidable_raw_or_partial"] for x in d)
        costs = [run_cost(r, rule) for r in rows]
        inc_tokens = [100 * (c["input_tokens"] / base[id(r)]["input_tokens"] - 1) for r, c in zip(rows, costs)]
        inc_upper = [100 * (c["upper"] / base[id(r)]["upper"] - 1) for r, c in zip(rows, costs)]
        inc_cached = [100 * (c["cached"] / base[id(r)]["cached"] - 1) for r, c in zip(rows, costs)]
        net = [100 * (x["input_tokens_without_avoidable_steps"] / base[id(r)]["input_tokens"] - 1)
               for r, x in zip(rows, d)]
        table["rules"][rule] = dict(
            avoidable_raw=raw, avoidable_raw_pct_of_rereads=pct(raw, rr), avoidable_raw_pct_of_tool_calls=pct(raw, tool),
            avoidable_raw_or_partial_pct_of_rereads=pct(rawp, rr),
            run_input_tokens_increase_pct=dict(median=q(inc_tokens, 50), p90=q(inc_tokens, 90)),
            pooled_input_tokens_increase_pct=round(100 * (sum(c["input_tokens"] for c in costs)
                                                          / sum(b["input_tokens"] for b in base.values()) - 1), 1),
            run_cost_upper_increase_pct=dict(median=q(inc_upper, 50), p90=q(inc_upper, 90)),
            run_cost_cached_increase_pct=dict(median=q(inc_cached, 50), p90=q(inc_cached, 90)),
            cost_cny=dict(upper=round(sum(c["upper"] for c in costs), 2), cached=round(sum(c["cached"] for c in costs), 2)),
            rejected_requests=sum(x["rejected"] for x in d), runs_with_rejection=sum(x["rejected"] > 0 for x in d),
            max_request_tokens=max(x["max_request_tokens"] for x in d),
            avoidable_only_steps=sum(x["avoidable_only_steps"] for x in d),
            avoidable_only_steps_pct_of_requests=pct(sum(x["avoidable_only_steps"] for x in d), steps),
            rough_net_input_increase_pct=dict(median=q(net, 50), p90=q(net, 90)),
        )
    return table


def decide(groups: dict) -> dict:
    g = groups[sim.DECISION_GROUP]["rules"]
    tests = {}
    for rule in sim.CANDIDATES:
        x = g[rule]
        tests[rule] = dict(
            T1=x["avoidable_raw_pct_of_rereads"] >= sim.T1_AVOIDABLE,
            T2=x["run_input_tokens_increase_pct"]["median"] <= sim.T2_INPUT,
            T3=x["run_cost_cached_increase_pct"]["median"] <= sim.T3_COST,
            T4=x["rejected_requests"] == 0)
    passing = [r for r in sim.CANDIDATES if all(tests[r].values())]
    chosen = None
    if passing:
        best = max(g[r]["avoidable_raw_pct_of_rereads"] for r in passing)
        near = [r for r in passing if g[r]["avoidable_raw_pct_of_rereads"] >= best - sim.TIE_POINTS]
        chosen = min(near, key=lambda r: g[r]["run_input_tokens_increase_pct"]["median"])
    out = dict(group=sim.DECISION_GROUP, tests=tests, passing=passing, recommended=chosen)
    if chosen:
        u = g[chosen]["avoidable_raw_pct_of_tool_calls"]
        out["U_points"] = u
        out["keep_section6_metric"] = u >= sim.U_KEEP
    return out


def main() -> None:
    rows = json.loads((sim.OUT_DIR / "sim_runs.json").read_text(encoding="utf-8"))
    kept = [r for r in rows if not r["status"].startswith("excluded")]
    rules = [r for r in sim.RULES if r in kept[0]["rules"]]
    groups = defaultdict(list)
    for r in kept:
        groups[f"{r['experiment']} {r['config']}"].append(r)
        groups[f"{r['experiment']} all"].append(r)
    tables = {k: group_table(v, rules) for k, v in sorted(groups.items())}
    summary = dict(schema="context-sim.v1", rules=sim.RULES, limit=sim.LIMIT, validation=validation(kept),
                   excluded={s: sum(r["status"] == s for r in rows)
                             for s in sorted({r["status"] for r in rows if r["status"].startswith("excluded")})},
                   sim_errors=[dict(run_dir=r["run_dir"], error=r["error"]) for r in rows if r.get("error")],
                   groups=tables,
                   decision=decide(tables))
    path = sim.OUT_DIR / "sim_summary.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary["validation"], ensure_ascii=False, indent=2))
    print(json.dumps(summary["decision"], indent=2))
    print("wrote", path)


if __name__ == "__main__":
    main()
