"""Found-but-idle runs (question 2) against repetition loops, for A and U failures. Descriptive; post-hoc.

For each failed run: question-2 class against loop / no loop. For found_idle runs: loop onset relative to the located
request, share of silent assistant turns (no visible text) before and after the located request, and the share of
calls after the located request whose key had never been issued before in the run.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from statistics import median

from analyze import features

OUT = Path(__file__).resolve().parent / "out"


def silent(turns: list[dict]) -> float:
    return sum(1 for q in turns if q["content_chars"] == 0) / max(len(turns), 1)


def main() -> None:
    rows = json.loads((OUT / "runs.json").read_text(encoding="utf-8"))
    result = {}
    for g in ("A", "U"):
        failed = [r for r in rows if r["group"] == g and not r["passed"]]
        loop = {id(r): features(r)["loop"] for r in failed}
        idle = [r for r in failed if r["cls"] == "found_idle"]
        gaps, before, silent_before, silent_after, fresh_share, other_tail = [], 0, [], [], [], []
        for r in idle:
            f = features(r)
            loc = r["located"] or 1
            if f["loop"]:
                gaps.append(f["loop_onset"] - loc)
                before += f["loop_onset"] < loc
            else:
                other_tail.append(round(f["tail_repeat"], 2))
            turns = [q for q in r["requests"] if "content_chars" in q]
            silent_before.append(silent(turns[:loc - 1]))
            silent_after.append(silent(turns[loc - 1:]))
            seen = {c["key"] for c in r["calls"] if c["request"] < loc}
            after = [c for c in r["calls"] if c["request"] >= loc]
            fresh = 0
            for c in after:
                fresh += c["key"] not in seen
                seen.add(c["key"])
            fresh_share.append(fresh / max(len(after), 1))
        result[g] = dict(
            failed=len(failed),
            loop_by_class=dict(Counter(r["cls"] for r in failed if loop[id(r)])),
            no_loop_by_class=dict(Counter(r["cls"] for r in failed if not loop[id(r)])),
            found_idle=len(idle), found_idle_loop=len(gaps), loop_before_located=before,
            onset_after_located_median=median(gaps) if gaps else None, onset_after_located_min=min(gaps, default=None),
            silent_before_located_median=round(median(silent_before), 2),
            silent_after_located_median=round(median(silent_after), 2),
            fresh_calls_after_located_median=round(median(fresh_share), 2),
            no_loop_found_idle_tail_repeat=other_tail)
    (OUT / "idle.json").write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
