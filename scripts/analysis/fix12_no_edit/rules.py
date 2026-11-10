"""Rules for the FIX-12 question-2 failure classification (design UNLEASH-PILOT-DESIGN §5.3, decision §7.5).

Committed together with references.py and out/references.json before any per-run class was computed. The design
asked for this adaptation to be committed before the first model run; that did not happen, so these rules were
written after the pilot finished. Already seen at that point: per-arm pass counts and stop-reason counts, the
issue-only 10-task minimal count (A 5, U 4, U+T 0 runs that read a candidate function and never edited), and the
call log of one U run while checking the journal format. The rules below follow the design text; every departure is
listed under "Adaptations".

Scope: the 180 formal runs, 30 tasks x A/U/UT x r1, r2 (retired attempts such as r2.infra1 are excluded).
Failed means status != "pass"; partial and the delta_too_large infra_error runs count as failures, as in the pilot.

Classes, P4's four for failed runs without any edit:
  budget      found the reference late and was stopped by a resource stop
  not_found   never read a reference although it made at least MIN_SEARCH_REQUESTS requests
  found_idle  read a reference with more than LATE_MAX_REQUESTS_AFTER requests left, never edited
  other       everything else

A reference counts as read when a line-range read (reads.py unit kind != "search") on a reference code file returned
output containing "def <name>(" for a def reference or "class <Name>" followed by "(" or ":" for a class reference.
For a task whose gold patch only changes module-level lines (granularity "file"), a line-range read of a reference
code file counts; such runs are flagged and reported separately.

Adaptations:
  1. References come from the gold patch (references.py), as the design says; granularity is recorded per task.
  2. RESOURCE_STOPS covers every resource stop the three arms can produce (call, token and cost caps, admission
     rejection, step limit, run and step deadlines, run timeout). Thresholds are P4's.
  3. plan_text reads only assistant content, never reasoning_content.
  4. P4's scope was failed runs that never edited; FIX-12 failed runs may edit. A failed run with an edit call or a
     non-empty final changed_files is classed "edited", split by whether the final candidate touches a reference
     code file (edited_reference), touches only other files (edited_elsewhere), or is empty after edits
     (edited_reverted). Only the other failed runs go through classify(). The §7.5 shares use all failed runs of
     an arm as the denominator; shares among no-edit failed runs are reported next to them.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

_p4 = importlib.util.spec_from_file_location("p4_rules", Path(__file__).resolve().parent.parent / "p4_no_edit" / "rules.py")
p4 = importlib.util.module_from_spec(_p4)
_p4.loader.exec_module(p4)

GROUPS = ("A", "U", "UT")
REPEATS = ("r1", "r2")
INTERFACE_SUFFIX = "_interface"

RESOURCE_STOPS = {"max_model_calls", "max_tokens", "max_cost_usd", "budget_exhausted", "insufficient_reserve",
                  "step_limit", "run_deadline", "step_deadline", "timeout"}
LATE_MAX_REQUESTS_AFTER = p4.LATE_MAX_REQUESTS_AFTER
MIN_SEARCH_REQUESTS = p4.MIN_SEARCH_REQUESTS
plan_text = p4.plan_text

TEST_PATH = re.compile(r"(^|/)(tests?|testing)/|(^|/)test_[^/]*\.py$|_tests?\.py$|(^|/)conftest\.py$")


def is_test_path(path: str) -> bool:
    return bool(TEST_PATH.search(path))


def ref_pattern(ref: dict) -> re.Pattern:
    if ref["kind"] == "def":
        return re.compile(r"def\s+" + re.escape(ref["name"]) + r"\s*\(")
    return re.compile(r"class\s+" + re.escape(ref["name"]) + r"\s*[(:]")


def classify(located_request: int | None, requests: int, stop: str | None) -> str:
    if located_request is not None:
        if requests - located_request >= LATE_MAX_REQUESTS_AFTER + 1:
            return "found_idle"
        return "budget" if stop in RESOURCE_STOPS else "other"
    return "not_found" if requests >= MIN_SEARCH_REQUESTS else "other"


def edited_class(edit_calls: int, changed_files: list[str], reference_code_files: list[str]) -> str | None:
    if changed_files:
        return "edited_reference" if set(changed_files) & set(reference_code_files) else "edited_elsewhere"
    return "edited_reverted" if edit_calls else None


# §7.5: the classifier becomes a pre-registered secondary endpoint for the scale-up only when both hold.
A_FOUND_IDLE_MIN_SHARE = 0.30
U_FOUND_IDLE_DROP = 0.15


def decide(found_idle_share: dict[str, float | None]) -> str:
    a, u = found_idle_share.get("A"), found_idle_share.get("U")
    if a is not None and u is not None and a >= A_FOUND_IDLE_MIN_SHARE and a - u >= U_FOUND_IDLE_DROP:
        return "secondary_endpoint"
    return "report_only"


# Manual check (§5.3): 12 failed runs drawn with random.Random(SAMPLE_SEED) from all failed runs of the three arms
# sorted by (task, group, repeat), shown without group or class. Judged by hand from the call log and assistant text:
# the located request (same request or both none) and the plan flag; the edited flag is judged too. Bar: at least
# 10/12 agreement on located and on plan; below it the classes are marked unreliable and only descriptive counts
# are reported.
SAMPLE_SEED = 11
SAMPLE_SIZE = 12
AGREEMENT_BAR = 10
