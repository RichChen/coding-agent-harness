"""Print the per-group feature table from out/summary.json (mean/median(n) per cell)."""

import json
from pathlib import Path

KEYS = ["runs", "loop_runs", "requests", "calls", "calls_per_request", "repeat_share", "tail_repeat", "streak",
        "loop_onset", "failed_calls", "retry_after_failure", "masked_rereads", "visible_rereads", "edits", "tests",
        "tested_before_first_edit", "tested_after_last_edit", "located_to_edit", "silent_share", "silent_share_tail",
        "content_chars", "reasoning_tokens", "reasoning_share", "reasoning_before_edit", "reasoning_after_edit",
        "completion_tokens", "max_prompt", "masked_requests", "elapsed_s", "cost"]


def cell(v) -> str:
    return f"{v['mean']}/{v['median']}(n{v['n']})" if isinstance(v, dict) else str(v)


groups = json.loads((Path(__file__).resolve().parent / "out" / "summary.json").read_text(encoding="utf-8"))["groups"]
for label in ("all", "pass", "fail"):
    print("==", label)
    for k in KEYS:
        print(k.ljust(26), " | ".join(cell(groups[g][label].get(k)).ljust(28) for g in ("A", "U", "UT")))
    for g in ("A", "U", "UT"):
        print("kinds", g, groups[g][label]["kinds"])
