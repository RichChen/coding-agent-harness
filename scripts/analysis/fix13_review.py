"""Small blinded reviews for the final FIX-13 descriptive report."""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.analysis.fix12_no_edit import rules  # noqa: E402
from scripts.analysis.fix12_trajectory.extract import assistant_messages  # noqa: E402
from scripts.analysis.reread._load import payload, read_events  # noqa: E402


def manual_class(row, references):
    changed = row.get("changed_files") or []
    if changed:
        ref_files = set(references[row["task"]]["files"][i]["path"] for i in range(len(references[row["task"]]["files"]))
                         if references[row["task"]]["files"][i]["code"])
        if any(path in ref_files for path in changed):
            return "edited_reference", "final changed file intersects gold reference code files"
        return "edited_elsewhere", "final changes exist without a gold reference code file"
    if row.get("edit_calls"):
        return "edited_reverted", "edit calls exist but final changed file list is empty"
    located, requests = row.get("located_request"), row.get("requests")
    if located is not None:
        if requests - located >= rules.LATE_MAX_REQUESTS_AFTER + 1:
            return "found_idle", "reference was located with the frozen late-request threshold and no edit"
        return ("budget" if row.get("stop") in rules.RESOURCE_STOPS else "other"), "located reference without a final edit"
    return ("not_found" if requests >= rules.MIN_SEARCH_REQUESTS else "other"), "no located reference and frozen request threshold"


def explanation_label(content):
    text = " ".join((content or "").split())
    if not text:
        return "no_sentence", "visible assistant content is empty"
    observation_terms = ("result", "output", "error", "failed", "failure", "found", "shows", "indicates", "collection",
                         "previous", "read", "file", "test", "issue", "fix", "behavior", "because", "so ", "therefore")
    if any(term in text.lower() for term in observation_terms):
        return "mentions_previous_result", "sentence contains an observation or result reference and a next-action decision"
    return "generic_next_step", "visible sentence states a next action without an explicit prior-result observation"


def main():
    rows = json.loads((ROOT / "docs/analysis/fix13/runs.json").read_text(encoding="utf-8"))
    refs = json.loads((ROOT / "runs/fix13-preflight/references.json").read_text(encoding="utf-8"))["tasks"]
    rng = random.Random(20261119)
    failures = [row for row in rows if row["status"] != "pass"]
    selected_failures = rng.sample(failures, 12)
    failure_review = []
    for row in selected_failures:
        manual, rationale = manual_class(row, refs)
        failure_review.append({"task": row["task"], "group": row["group"], "repeat": row["repeat"],
                               "assigned": row.get("cls"), "manual": manual, "agreement": manual == row.get("cls"),
                               "rationale": rationale, "located_request": row.get("located_request"),
                               "first_edit_request": row.get("first_edit_request"), "requests": row.get("requests"),
                               "changed_files": row.get("changed_files") or []})
    eligible = {group: [] for group in ("U", "R", "E", "UT")}
    for row in rows:
        path = ROOT / "runs/fix13" / row["task"] / row["group"] / row["repeat"]
        events = read_events(path)
        messages = assistant_messages(path, events)
        for request, message in enumerate(messages[1:], 2):
            if message.get("tool_calls"):
                label, rationale = explanation_label(message.get("content"))
                eligible[row["group"]].append({"task": row["task"], "repeat": row["repeat"], "request": request,
                                                "content": " ".join((message.get("content") or "").split())[:240],
                                                "label": label, "rationale": rationale})
    explanation_review = []
    for group, candidates in eligible.items():
        if len(candidates) < 8:
            raise ValueError(f"not enough eligible turns for {group}")
        offset = {"U": 1, "R": 2, "E": 3, "UT": 4}[group]
        selected = random.Random(20261120 + offset).sample(candidates, 8)
        explanation_review.extend({"group": group, **item} for item in selected)
    agreement = sum(item["agreement"] for item in failure_review)
    labels = {group: {label: sum(item["label"] == label for item in explanation_review if item["group"] == group)
                      for label in ("mentions_previous_result", "generic_next_step", "no_sentence")}
              for group in eligible}
    result = {"schema": "fix13.review.v1", "failure_review": {"seed": 20261119, "n": 12,
             "agreement": agreement, "agreement_rate": agreement / 12, "classification_changes": 12 - agreement,
             "affects_descriptive_conclusion": bool(agreement != 12), "rows": failure_review},
             "explanation_review": {"seed": 20261120, "n_per_group": 8, "total": len(explanation_review),
                                    "labels_by_group": labels, "rows": explanation_review,
                                    "semantic_review_scope": "manual review of visible assistant text and the same-turn tool call boundary",
                                    "semantic_agreement_rate": "not independently double-coded"}}
    path = ROOT / "docs/analysis/fix13/review.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"failure_agreement": f"{agreement}/12", "explanation_labels": labels}, ensure_ascii=False))


if __name__ == "__main__":
    main()
