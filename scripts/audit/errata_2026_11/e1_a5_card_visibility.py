"""Erratum E1: in A5-main B2, memory cards are visible only in the step-0 model view.

Reads the local runs/a5-main/<task>/{b0,b2}/r1..r3 (not committed), docs/sprint/b2-predicted-injection.json
and coding-agent/benchmarks/a5/memory-cards-v6.jsonl. For contrast it also reads the A6-R R1 runs listed in
docs/sprint/a6-retry-runs.json and counts views that still carry the <previous_attempt> record. No model calls, no Docker.
Writes e1_a5_card_visibility.json next to this script.

Definitions used in the output:
- a card is "in the view" at step k when the context.prepared payload for step k lists it in
  stats.plugin.actually_injected (reason memory_card) or a view message starts with CARD_PREFIX;
- the step-0 reply is the assistant message(s) in the step-1 view (masking only rewrites tool results);
- it "mentions the card" when its text matches MENTION (card wording) or names an injected source id/number;
  ("type hint" is excluded because it is task wording, e.g. sphinx-9258);
- a run "touches a card file" when a tool.intent argument contains one of the files listed by the
  task's predicted cards. Two matchers are reported: "path" (relevant_files without line ranges, plus
  dependency paths, matched as full relative paths) and "basename" (file names of relevant_files only,
  which also catches `find -name` / directory-scoped grep).
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import AGENT, HARNESS, load_json, payload, read_events, sha256_file, write_output  # noqa: E402

RUNS = HARNESS / "runs" / "a5-main"
PREDICTED = HARNESS / "docs" / "sprint" / "b2-predicted-injection.json"
CARDS = AGENT / "benchmarks" / "a5" / "memory-cards-v6.jsonl"
A6R_RUNS = HARNESS / "docs" / "sprint" / "a6-retry-runs.json"
A6R_RECORD = "<previous_attempt"
CARD_PREFIX = "[Untrusted policy candidate; source="
MENTION = re.compile(
    r"historical attempt|memory card|\bcards?\b|navigation hint|policy candidate|previous (?:attempt|fix)|"
    r"earlier (?:issue|fix|attempt)|validated_fix|(?<!type )\bhints?\b",
    re.IGNORECASE,
)
REPEATS = ("r1", "r2", "r3")


def text_of(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


def card_files(card: dict, matcher: str) -> set[str]:
    relevant = {re.sub(r":\d+(?:-\d+)?$", "", item).replace("\\", "/") for item in card.get("relevant_files") or []}
    if matcher == "basename":
        return {item.rsplit("/", 1)[-1] for item in relevant if item}
    files = relevant | {dep["path"].replace("\\", "/") for dep in card.get("dependencies") or []}
    return {item for item in files if item}


def scan_run(run_dir: Path) -> dict:
    events = read_events(run_dir)
    step = None
    views: dict[int, list] = {}
    plugin_ids: dict[int, list[str]] = {}
    intents: list[tuple[int | None, str]] = []
    for event in events:
        kind = event["event_type"]
        if kind == "context.prepared":
            data = payload(run_dir, event)
            step = data["step"]
            views[step] = data["view"]
            items = ((data.get("stats") or {}).get("plugin") or {}).get("actually_injected") or []
            plugin_ids[step] = [item["id"] for item in items if item.get("reason") == "memory_card"]
        elif kind == "tool.intent":
            args = json.dumps(event["payload"].get("arguments"), ensure_ascii=False).replace("\\\\", "/")
            intents.append((step, args))
    text_ids = {
        k: [text_of(m)[len(CARD_PREFIX):].split("]", 1)[0] for m in view if text_of(m).startswith(CARD_PREFIX)]
        for k, view in views.items()
    }
    card_steps = sorted(k for k in views if plugin_ids.get(k) or text_ids.get(k))
    reply = None
    if 1 in views:
        reply = "\n".join(text_of(m) for m in views[1] if m.get("role") == "assistant")
    return {
        "steps": len(views),
        "card_steps": card_steps,
        "injected": sorted({i for ids in plugin_ids.values() for i in ids}),
        "step0_text_ids": sorted(text_ids.get(0, [])),
        "step1_has_card_text": bool(text_ids.get(1)),
        "reply": reply,
        "intents": intents,
    }


def a6r_record_visibility() -> dict:
    """A6-R R1 puts the previous-attempt record into the task prompt; count views that still carry it."""
    rows = load_json(A6R_RUNS)["runs"]
    runs = views = with_record = runs_every_step = 0
    for row in rows:
        if row["arm"] != "r1":
            continue
        run_dir = HARNESS / row["run_dir"]
        flags = []
        for event in read_events(run_dir):
            if event["event_type"] == "context.prepared":
                view = payload(run_dir, event)["view"]
                flags.append(any(A6R_RECORD in text_of(m) for m in view if m.get("role") == "user"))
        runs += 1
        views += len(flags)
        with_record += sum(flags)
        runs_every_step += bool(flags) and all(flags)
    return {"r1_runs": runs, "views": views, "views_with_record": with_record, "runs_record_in_every_view": runs_every_step}


def touches(intents, files: set[str], *, step0_only: bool) -> int:
    return sum(
        1 for step, args in intents
        if (not step0_only or step == 0) and any(path in args for path in files)
    )


def main() -> None:
    predicted = load_json(PREDICTED)
    cards_sha = sha256_file(CARDS)
    if cards_sha != predicted["cards_sha256"]:
        raise SystemExit("card file sha differs from the predicted table")
    cards = {}
    for line in CARDS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            card = json.loads(line)
            cards[card["source_id"]] = card
    tasks = {row["id"]: [item["id"] for item in row["injected"]] for row in predicted["runs"]}

    b2_rows, consistent, mismatches = [], 0, []
    step_hist = Counter()
    for task, expected in sorted(tasks.items()):
        for rep in REPEATS:
            run_dir = RUNS / task / "b2" / rep
            scan = scan_run(run_dir)
            summary = load_json(run_dir / "summary.json")
            same = scan["injected"] == sorted(expected)
            consistent += same
            if not same:
                mismatches.append({"run": f"{task}/b2/{rep}", "actual": scan["injected"], "predicted": sorted(expected)})
            step_hist[json.dumps(scan["card_steps"])] += 1
            b2_rows.append((task, rep, expected, scan, summary))

    injected_rows = [row for row in b2_rows if row[3]["injected"]]
    mention_rows = []
    for task, rep, expected, scan, _ in injected_rows:
        reply = scan["reply"] or ""
        ids = {i for i in expected} | {i.rsplit("-", 1)[-1] for i in expected}
        hits = sorted({m.group(0) for m in MENTION.finditer(reply)} | {i for i in ids if re.search(r"\b" + re.escape(i) + r"\b", reply)})
        if hits:
            mention_rows.append({"run": f"{task}/b2/{rep}", "hits": hits, "reply": reply[:400]})

    injected_tasks = sorted({row[0] for row in injected_rows})
    intents_by_run = {(task, "b2", rep): scan["intents"] for task, rep, _, scan, _ in b2_rows}
    for task in injected_tasks:
        for rep in REPEATS:
            intents_by_run[(task, "b0", rep)] = scan_run(RUNS / task / "b0" / rep)["intents"]
    touch = {}
    for matcher in ("path", "basename"):
        files_by_task = {task: set().union(*(card_files(cards[i], matcher) for i in tasks[task])) for task in injected_tasks}
        touch[matcher] = {}
        for variant in ("b0", "b2"):
            runs_step0 = runs_any = events_step0 = events_any = 0
            for task in injected_tasks:
                for rep in REPEATS:
                    intents = intents_by_run[(task, variant, rep)]
                    s0 = touches(intents, files_by_task[task], step0_only=True)
                    anyk = touches(intents, files_by_task[task], step0_only=False)
                    runs_step0 += s0 > 0
                    runs_any += anyk > 0
                    events_step0 += s0
                    events_any += anyk
            touch[matcher][variant] = {"runs": 3 * len(injected_tasks), "runs_touching_at_step0": runs_step0,
                                       "runs_touching_any_step": runs_any, "tool_intents_at_step0": events_step0,
                                       "tool_intents_any_step": events_any}

    result = {
        "schema": "errata-2026-11.e1.v1",
        "inputs": {
            "runs": "runs/a5-main/<task>/{b0,b2}/r1..r3",
            "predicted_injection": "docs/sprint/b2-predicted-injection.json",
            "predicted_injection_sha256": sha256_file(PREDICTED),
            "cards": "coding-agent/benchmarks/a5/memory-cards-v6.jsonl",
            "cards_sha256": cards_sha,
            "mention_regex": MENTION.pattern,
        },
        "b2_runs": len(b2_rows),
        "injection_matches_prediction": consistent,
        "injection_mismatches": mismatches,
        "b2_runs_with_injection": len(injected_rows),
        "tasks_with_injection": len(injected_tasks),
        "card_steps_histogram": dict(step_hist),
        "injected_runs_cards_only_at_step0": sum(1 for r in injected_rows if r[3]["card_steps"] == [0]),
        "injected_runs_step1_view_has_card_text": sum(1 for r in injected_rows if r[3]["step1_has_card_text"]),
        "injected_runs_with_step1": sum(1 for r in injected_rows if r[3]["reply"] is not None),
        "step0_reply_mentions_card": len(mention_rows),
        "step0_reply_mention_details": mention_rows,
        "card_file_touches": touch,
        "parse_error_check": sorted(f"{r[0]}/b2/{r[1]}" for r in b2_rows if r[4].get("stop_reason") == "model_parse_error"),
        "a6r_r1_record_visibility": a6r_record_visibility(),
    }
    path = write_output("e1_a5_card_visibility.json", result)
    print(json.dumps({k: v for k, v in result.items() if k not in ("step0_reply_mention_details", "inputs")}, ensure_ascii=False, indent=2))
    print("wrote", path)


if __name__ == "__main__":
    main()
