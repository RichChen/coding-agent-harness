"""Manual check of the locate and plan rules (rules.py: SAMPLE_SEED, SAMPLE_SIZE, AGREEMENT_BAR).

  python sample.py draw    -> out/manual_sample.json: per sampled run, every call with its request, arguments,
                              an output excerpt around the first reference definition (or the output head), every
                              assistant text with its request, and commands; the rule results are left out.
  python sample.py score   -> reads out/manual_review.json (hand judgments: located_request, plan) and
                              out/no_edit_runs.json, writes out/manual_review_score.json.
"""

from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "reread"))
import rules  # noqa: E402
from _load import payload, read_events, step_number  # noqa: E402
from classify import OUT, assistant_texts  # noqa: E402
from reread import discover  # noqa: E402


def excerpt(output: str | None, functions: list[str]) -> str:
    if not output:
        return ""
    for fn in functions:
        m = re.search(r"def\s+" + re.escape(fn) + r"\s*\(", output)
        if m:
            start = output.rfind("\n", 0, max(0, m.start() - 200)) + 1
            return output[start:start + 900]
    return output[:300]


def draw() -> None:
    specs = sorted((s for s in discover() if s["experiment"] == "P4" and s["p4_outcome"] == "behavior_failed"),
                   key=lambda s: (s["p4_batch"], s["p4_run_index"]))
    picked = random.Random(rules.SAMPLE_SEED).sample(specs, rules.SAMPLE_SIZE)
    sample = []
    for s in picked:
        run_dir = Path(s["run_dir"])
        ref = rules.REFERENCES[s["task"]]
        events = read_events(run_dir)
        results = {e["tool_call_id"]: e for e in events if e["event_type"] == "tool.result"}
        request_of_step, requests, calls = {}, 0, []
        for e in events:
            if e["event_type"] == "model.started":
                requests += 1
                request_of_step.setdefault(step_number(e), requests)
            elif e["event_type"] == "tool.intent":
                p = payload(run_dir, e)
                res = results.get(e["tool_call_id"])
                rp = payload(run_dir, res) if res else {}
                out = rp.get("output") if isinstance(rp.get("output"), str) else None
                calls.append(dict(request=request_of_step.get(step_number(e)), name=p["name"],
                                  args=json.dumps(p.get("arguments") or {}, ensure_ascii=False)[:400],
                                  output=excerpt(out, ref["functions"])))
        texts = assistant_texts(run_dir, events)
        sample.append(dict(batch=s["p4_batch"], run_index=s["p4_run_index"], task=s["task"], condition=s["config"],
                           repeat=s["repeat"], requests=requests, reference=ref, calls=calls,
                           texts=[dict(request=i + 1, text=t) for i, t in enumerate(texts) if t.strip()]))
    OUT.mkdir(exist_ok=True)
    path = OUT / "manual_sample.json"
    path.write_text(json.dumps(sample, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", path, [(r["task"][-4:], r["condition"], r["repeat"]) for r in sample])


def score() -> None:
    review = json.loads((OUT / "manual_review.json").read_text(encoding="utf-8"))
    rows = {(r["batch"], r["run_index"]): r for r in json.loads((OUT / "no_edit_runs.json").read_text(encoding="utf-8"))}
    items, loc_ok, plan_ok = [], 0, 0
    for h in review["runs"]:
        r = rows[(h["batch"], h["run_index"])]
        lo = h["located_request"] == r["located_request"]
        pl = h["plan"] == (r["plan_text"] or r["rehearsal"])
        loc_ok += lo
        plan_ok += pl
        items.append(dict(batch=h["batch"], run_index=h["run_index"], task=r["task"][-4:], condition=r["condition"],
                          rule_located=r["located_request"], hand_located=h["located_request"], located_agree=lo,
                          rule_plan=r["plan_text"] or r["rehearsal"], hand_plan=h["plan"], plan_agree=pl,
                          note=h.get("note", "")))
    n = len(items)
    result = dict(n=n, located_agree=loc_ok, plan_agree=plan_ok, bar=rules.AGREEMENT_BAR,
                  passes=loc_ok >= rules.AGREEMENT_BAR and plan_ok >= rules.AGREEMENT_BAR, items=items)
    (OUT / "manual_review_score.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                                  encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "items"}))


if __name__ == "__main__":
    {"draw": draw, "score": score}[sys.argv[1]]()
