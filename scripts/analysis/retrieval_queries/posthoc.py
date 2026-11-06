"""Post-hoc checks (not pre-registered), run after run.py score:

1. For the slow CR-2 N tasks (N runs with first_gold_step > 4), the P group's first gold step, split by whether
   the frozen prefetch (full issue, hybrid) had a gold file in its top 5. The tasks are picked by N's outcome, so
   P - N is inflated by regression to the mean; T (no prefetch, tool never called) is the control, and P - T is
   the comparison that does not share that selection. The same split is shown for the other CR-2 tasks.
2. On the slow tasks, task-level wins and losses of each D3 config against (full, hybrid).

Output: docs/analysis/retrieval-queries-posthoc.json
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
RESULTS = ROOT / "docs" / "analysis" / "retrieval-queries-results.json"
PER_QUERY = ROOT / "docs" / "analysis" / "retrieval-queries-per-query.jsonl"
ANALYSIS = ROOT / "docs" / "retrieval" / "cr2-analysis.json"
OUT = ROOT / "docs" / "analysis" / "retrieval-queries-posthoc.json"
SLOW_K = 4


def main() -> None:
    results = json.loads(RESULTS.read_text(encoding="utf-8"))
    analysis = json.loads(ANALYSIS.read_text(encoding="utf-8"))
    hit5 = {}
    for line in PER_QUERY.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["set"] == "test":
            hit5[(row["instance_id"], row["variant"], row["retriever"])] = row["hit@5"] == 1.0

    steps = defaultdict(lambda: defaultdict(list))
    for r in analysis["per_run"]:
        steps[r["task_id"]][r["group"]].append(r["first_gold_step"])
    slow_tasks = results["potential"]["by_k"][str(SLOW_K)]["slow_tasks_list"]

    from src.retrieval.metrics import paired_bootstrap

    def task_row(t: str) -> dict:
        row = {"task": t, "prefetch_top5_has_gold": hit5[(t, "full", "hybrid")]}
        for g in ("n", "p", "t", "o"):
            row[f"{g}_steps"] = steps[t][g]
            row[f"{g}_mean"] = float(np.mean(steps[t][g]))
        return row

    def split_by_prefetch(rows: list[dict]) -> dict:
        out = {}
        for flag in (True, False):
            part = [r for r in rows if r["prefetch_top5_has_gold"] is flag]
            cell = {"tasks": len(part)}
            for g in ("n", "p", "t", "o"):
                cell[f"{g}_mean"] = float(np.mean([r[f"{g}_mean"] for r in part])) if part else None
            if part:
                cell["p_minus_t"] = paired_bootstrap([r["p_mean"] for r in part], [r["t_mean"] for r in part],
                                                     n_boot=10000, seed=20261026)
                cell["p_faster_than_t_tasks"] = sum(r["p_mean"] < r["t_mean"] for r in part)
                cell["p_slower_than_t_tasks"] = sum(r["p_mean"] > r["t_mean"] for r in part)
            out["prefetch_hit" if flag else "prefetch_miss"] = cell
        return out

    rows = [task_row(t) for t in slow_tasks]
    other = [task_row(t) for t in sorted(steps) if t not in set(slow_tasks)]
    split = split_by_prefetch(rows)
    split_other = split_by_prefetch(other)

    vs_ref = {}
    for cfg in results["decision"]["D3"]:
        v, r = cfg.split("|")
        vs_ref[cfg] = {
            "hit_where_ref_misses": sorted(t for t in slow_tasks if hit5[(t, v, r)] and not hit5[(t, "full", "hybrid")]),
            "miss_where_ref_hits": sorted(t for t in slow_tasks if not hit5[(t, v, r)] and hit5[(t, "full", "hybrid")]),
        }

    out = {"slow_k": SLOW_K, "slow_tasks": rows, "groups_on_slow_tasks": split, "groups_on_other_tasks": split_other,
           "d3_configs_vs_full_hybrid": vs_ref}
    OUT.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: v for k, v in out.items() if k != "slow_tasks"}, indent=2))
    for r in rows:
        print(r["task"], r["prefetch_top5_has_gold"], "N", r["n_steps"], "P", r["p_steps"], "T", r["t_steps"], "O", r["o_steps"])


if __name__ == "__main__":
    main()
