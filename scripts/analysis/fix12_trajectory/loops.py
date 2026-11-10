"""Cache hits, stop reasons and loop content over out/runs.json. Descriptive; written after the results were known.

- cache_hit_share: prompt_cache_hit_tokens / prompt_tokens summed over a group's requests.
- For loop runs (tail_repeat >= 0.5, as analyze.py): the most repeated key in the last TAIL calls, its tool kind,
  whether its calls failed, whether its output was identical across repeats, and the cycle length (number of distinct
  keys in the last TAIL calls; 1 = one call repeated back to back).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from analyze import GROUPS, TAIL, features

OUT = Path(__file__).resolve().parent / "out"


def main() -> None:
    rows = json.loads((OUT / "runs.json").read_text(encoding="utf-8"))
    result = {}
    for g in GROUPS:
        rs = [r for r in rows if r["group"] == g]
        reqs = [q for r in rs for q in r["requests"]]
        prompt = sum(q.get("prompt") or 0 for q in reqs)
        hit = sum(q.get("cache_hit") or 0 for q in reqs)
        loops = []
        for r in rs:
            f = features(r)
            if not f["loop"]:
                continue
            tail = r["calls"][-TAIL:]
            key, n = Counter(c["key"] for c in tail).most_common(1)[0]
            same = [c for c in r["calls"] if c["key"] == key]
            loops.append(dict(task=r["task"], repeat=r["repeat"], passed=r["passed"], stop=r["stop"],
                              cycle=len({c["key"] for c in tail}), top_share=n / len(tail),
                              kind=same[0]["kind"], name=same[0]["name"], times=len(same),
                              failed=sum(c["failed"] for c in same), identical_output=len({c["out_head"] for c in same}) == 1,
                              key=key[:160], out_head=same[0]["out_head"][:160]))
        result[g] = dict(cache_hit_share=round(hit / prompt, 3) if prompt else None,
                         stops=dict(Counter(f"{r['stop']}:{'pass' if r['passed'] else 'fail'}" for r in rs).most_common()),
                         loop_kinds=dict(Counter(x["kind"] for x in loops)),
                         loop_cycle=dict(Counter(x["cycle"] for x in loops)),
                         loop_failed=sum(1 for x in loops if x["failed"]),
                         loop_identical_output=sum(1 for x in loops if x["identical_output"]),
                         loops=loops)
    (OUT / "loops.json").write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for g in GROUPS:
        x = result[g]
        print(g, {k: v for k, v in x.items() if k != "loops"})
        for l in x["loops"]:
            print("  ", l["task"][:28], l["repeat"], l["passed"], l["stop"], "cycle", l["cycle"], l["kind"],
                  "x", l["times"], "failed", l["failed"], "same_out", l["identical_output"], "|", l["key"][:110])


if __name__ == "__main__":
    main()
