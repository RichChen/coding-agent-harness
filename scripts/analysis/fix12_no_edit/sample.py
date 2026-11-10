"""Manual check for the FIX-12 question-2 classification (rules.py, design §5.3).

  draw   pick SAMPLE_SIZE failed runs with random.Random(SAMPLE_SEED) from all failed runs sorted by
         (task, group, repeat), using summary.json status only; write out/manual_sample.json and one blind sheet per
         run under out/sheets/ (call log and assistant content, no group, no class)
  score  compare out/manual_review.json (hand judgments: located request, plan, edited) with out/runs.json
"""

from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rules  # noqa: E402
from classify import OUT, assistant_contents, references, run_calls, run_specs  # noqa: E402

DEF_LINE = re.compile(r"^\s*(?:\d+\s*[:|]?\s*)?(?:async\s+)?(?:def|class)\s+\w+")


def digest(call: dict) -> str:
    args = call["args"]
    if call["name"] in ("write_file", "search_replace", "edit_file"):
        body = json.dumps({k: v for k, v in args.items() if k != "path"}, ensure_ascii=False)[:300]
        return f"path={args.get('path')} {body}"
    head = json.dumps(args, ensure_ascii=False)[:400]
    out = call["output"] or ""
    lines = out.splitlines()
    defs = [ln.strip()[:120] for ln in lines if DEF_LINE.match(ln)][:20]
    tail = "\n      ".join(defs) if defs else out[:300].replace("\n", " | ")
    return f"{head}\n    -> {len(lines)} lines; def/class lines:\n      {tail}" if defs else f"{head}\n    -> {tail}"


def sheet(label: str, spec: dict, ref: dict) -> str:
    calls, requests, events = run_calls(spec["run_dir"], ref)
    texts = assistant_contents(spec["run_dir"], events)
    out = [f"# {label}  task={spec['task']}  requests={requests}",
           "references: " + ", ".join(f"{x['kind']} {x['qualname']} ({x['file']})" for x in ref["refs"]), "",
           "## calls"]
    out += [f"[req {c['request']}] {c['name']} {digest(c)}" for c in calls]
    out += ["", "## assistant content (message i follows request i)"]
    out += [f"[msg {i + 1}] {t.strip()[:800]}" for i, t in enumerate(texts) if t.strip()]
    return "\n".join(out) + "\n"


def draw() -> None:
    refs = references()
    failed = []
    for s in run_specs(refs):
        status = json.loads((s["run_dir"] / "summary.json").read_text(encoding="utf-8"))["status"]
        if status != "pass":
            failed.append(s)
    picked = random.Random(rules.SAMPLE_SEED).sample(failed, rules.SAMPLE_SIZE)
    (OUT / "sheets").mkdir(parents=True, exist_ok=True)
    items = []
    for i, s in enumerate(picked, 1):
        label = f"S{i:02d}"
        (OUT / "sheets" / f"{label}.md").write_text(sheet(label, s, refs[s["task"]]), encoding="utf-8")
        items.append(dict(label=label, task=s["task"], group=s["group"], repeat=s["repeat"]))
    (OUT / "manual_sample.json").write_text(json.dumps(dict(seed=rules.SAMPLE_SEED, failed=len(failed), items=items),
                                                       indent=2) + "\n", encoding="utf-8")
    print(f"{len(failed)} failed runs; sheets:", ", ".join(f"{it['label']} {it['task']}" for it in items))


def score() -> None:
    sample = json.loads((OUT / "manual_sample.json").read_text(encoding="utf-8"))["items"]
    review = json.loads((OUT / "manual_review.json").read_text(encoding="utf-8"))["items"]
    rows = {(r["task"], r["group"], r["repeat"]): r for r in json.loads((OUT / "runs.json").read_text(encoding="utf-8"))}
    detail, agree = [], dict(located=0, plan=0, edited=0)
    for it in sample:
        r, h = rows[(it["task"], it["group"], it["repeat"])], review[it["label"]]
        auto = dict(located=r["located_request"], plan=r["plan_text"], edited=r["cls"].startswith("edited"))
        hits = {k: auto[k] == h[k] for k in agree}
        for k, v in hits.items():
            agree[k] += v
        detail.append(dict(label=it["label"], auto=auto, manual={k: h[k] for k in agree}, agree=hits,
                           note=h.get("note", "")))
    result = dict(agreement=agree, bar=rules.AGREEMENT_BAR,
                  reliable=agree["located"] >= rules.AGREEMENT_BAR and agree["plan"] >= rules.AGREEMENT_BAR,
                  detail=detail)
    (OUT / "manual_review_score.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                                  encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    {"draw": draw, "score": score}[sys.argv[1]]()
