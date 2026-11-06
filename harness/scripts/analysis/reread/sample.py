"""Draw manual-check samples of re-read decisions and score the manual verdicts.

  python sample.py draw <name>    -> out/manual_sample_<name>.json from .tmp/reread/calls.jsonl
  python sample.py score <name>   -> reads out/manual_review_<name>.json, writes out/manual_review_<name>_score.json

Samples:
- tuning: drawn from the first version of the classifier and used to find its errors; the rules were then
  changed (glob paths, directory writes, runs that regenerate output, 20% overlap floor). Its score under the
  current rules is optimistic because the rules were fitted to it.
- holdout: drawn with a different seed after the rule changes and reviewed once; this is the error estimate.

Per experiment group (A5, A6-R, CR-2, P4 incl. the first P4 run): calls flagged as re-reads, read calls not
flagged, and calls flagged as read-after-write. Each item shows the call, its read units, the earlier calls it
overlaps with (and their state in the view), and the writes in between.

Verdicts in manual_review_<name>.json: {"<id>": {"reread": true|false, "view_state": "raw"|"partial"|"elided"|
"absent"|null, "note": "..."}}. reread is the reviewer's judgement of "this call reads content already read at
an earlier step, unchanged since"; view_state is the reviewer's reading of how much of that earlier content was
still verbatim in the view (null when not a re-read).
"""

from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _load import OUT_DIR, write_json  # noqa: E402
from reread import CALLS_OUT  # noqa: E402

SAMPLES = {
    "tuning": dict(seed=20261106, per_group={"reread": 12, "not_reread": 6, "after_write": 3}),
    "holdout": dict(seed=20261107, per_group={"reread": 8, "not_reread": 4, "after_write": 2}),
}
GROUPS = {"A5": "A5", "A6-R": "A6-R", "CR-2": "CR-2", "P4": "P4", "P4-first": "P4"}


def what(c: dict) -> str:
    return c.get("command") or json.dumps(c.get("args"), ensure_ascii=False) if c.get("command") or c.get("args") else c["name"]


def load_calls() -> dict[str, list[dict]]:
    by_run = defaultdict(list)
    with CALLS_OUT.open(encoding="utf-8") as fh:
        for line in fh:
            c = json.loads(line)
            by_run[c["run_dir"]].append(c)
    return by_run


def draw(name: str) -> None:
    cfg = SAMPLES[name]
    by_run = load_calls()
    pools = defaultdict(list)
    for run, calls in by_run.items():
        for c in calls:
            group = GROUPS[c["experiment"]]
            if c["reread"]:
                pools[(group, "reread")].append((run, c["index"]))
            elif c["reread_after_write"]:
                pools[(group, "after_write")].append((run, c["index"]))
            elif c["units"]:
                pools[(group, "not_reread")].append((run, c["index"]))
    rng = random.Random(cfg["seed"])
    items = []
    for (group, label), pool in sorted(pools.items()):
        pool.sort()
        for run, index in rng.sample(pool, min(cfg["per_group"][label], len(pool))):
            calls = by_run[run]
            c = calls[index]
            paths = {u["path"] for u in c["units"]}
            priors = set(c.get("prior_call_ids") or [])
            same = [p for p in calls[:index] if p["units"] and ({u["path"] for u in p["units"]} & paths
                                                                or p["call_id"] in priors)]
            others = {p["call_id"] for p in [p for p in same if p["call_id"] not in priors][-5:]}
            earlier = [dict(step=p["step"], call_id=p["call_id"], what=what(p)[:400], units=p["units"],
                            state_in_view=(c.get("prior_states") or {}).get(p["call_id"]))
                       for p in same if p["call_id"] in priors or p["call_id"] in others]
            writes = [dict(step=p["step"], what=what(p)[:300], writes=p.get("writes"), ran=p.get("ran", False))
                      for p in calls[:index] if p.get("writes") or p.get("ran") or p["kind"] == "edit"]
            items.append(dict(id=f"{group}-{label}-{len(items):03d}", group=group, label=label,
                              run_dir=run, step=c["step"], call_id=c["call_id"], call=what(c)[:600],
                              units=c["units"], flagged_reread=c["reread"], covered=c["covered"],
                              view_state=c.get("view_state"), prior_call_ids=c.get("prior_call_ids"),
                              earlier_same_paths=earlier, writes_before=writes[-6:]))
    path = write_json(f"manual_sample_{name}.json", items)
    print("wrote", path, len(items))


def current_flags(items: list[dict]) -> dict[str, dict]:
    """The current classifier's decision for each sampled call (matched by call id, else by step and text)."""
    by_run = load_calls()
    out = {}
    for item in items:
        calls = by_run[item["run_dir"].replace("\\", "/")]
        match = [c for c in calls if c["call_id"] == item.get("call_id")] or \
                [c for c in calls if c["step"] == item["step"] and what(c)[:600] == item["call"]]
        if len(match) != 1:
            raise SystemExit(f"cannot match {item['id']}: {len(match)} candidates")
        c = match[0]
        out[item["id"]] = dict(flagged_reread=c["reread"], view_state=c.get("view_state"))
    return out


def tally(items: list[dict], review: dict, flags: dict[str, dict]) -> dict:
    """Error rates of one classifier version against the manual verdicts."""
    flagged = [i for i in items if flags[i["id"]]["flagged_reread"]]
    unflagged = [i for i in items if not flags[i["id"]]["flagged_reread"]]
    true_rr = [i for i in flagged if review[i["id"]]["reread"]]
    fp = [i["id"] for i in flagged if not review[i["id"]]["reread"]]
    fn = [i["id"] for i in unflagged if review[i["id"]]["reread"]]
    view_wrong = [i["id"] for i in true_rr if review[i["id"]]["view_state"] != flags[i["id"]]["view_state"]]
    masked = {"elided", "absent"}
    masked_wrong = [i["id"] for i in true_rr
                    if (review[i["id"]]["view_state"] in masked) != (flags[i["id"]]["view_state"] in masked)]
    return dict(
        flagged=len(flagged), false_positives=len(fp), false_positive_rate=round(len(fp) / len(flagged), 3)
        if flagged else None,
        unflagged=len(unflagged), false_negatives=len(fn), false_negative_rate=round(len(fn) / len(unflagged), 3)
        if unflagged else None,
        view_state_checked=len(true_rr), view_state_wrong=len(view_wrong),
        view_state_accuracy=round(1 - len(view_wrong) / len(true_rr), 3) if true_rr else None,
        masked_vs_visible_wrong=len(masked_wrong),
        false_positive_ids=fp, false_negative_ids=fn, view_state_wrong_ids=view_wrong,
    )


def score(name: str) -> None:
    items = json.loads((OUT_DIR / f"manual_sample_{name}.json").read_text(encoding="utf-8"))
    review = json.loads((OUT_DIR / f"manual_review_{name}.json").read_text(encoding="utf-8"))
    missing = sorted({i["id"] for i in items} - set(review))
    if missing:
        raise SystemExit(f"unreviewed items: {missing}")
    frozen = {i["id"]: dict(flagged_reread=i["flagged_reread"], view_state=i["view_state"]) for i in items}
    current = current_flags(items)
    by_stratum = defaultdict(lambda: defaultdict(int))
    for i in items:
        by_stratum[f"{i['group']} {i['label']}"]["n"] += 1
        by_stratum[f"{i['group']} {i['label']}"]["manual_reread"] += review[i["id"]]["reread"]
    result = dict(
        sample=name,
        reviewer_note="Verdicts were written by the analyst reading each item against the raw call list; "
                      "one reviewer, no blinding. Strata are drawn at fixed sizes, so the rates are per stratum "
                      "of the classifier's own labels, not population rates.",
        items=len(items),
        strata={k: dict(v) for k, v in sorted(by_stratum.items())},
        classifier_at_draw=tally(items, review, frozen),
        classifier_current=tally(items, review, current),
        notes={k: v["note"] for k, v in review.items() if v.get("note")},
    )
    path = write_json(f"manual_review_{name}_score.json", result)
    print(json.dumps({k: result[k] for k in ("classifier_at_draw", "classifier_current")}, ensure_ascii=False,
                     indent=1))
    print("wrote", path)


if __name__ == "__main__":
    {"draw": draw, "score": score}[sys.argv[1]](sys.argv[2])
