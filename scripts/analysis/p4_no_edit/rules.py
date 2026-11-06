"""Pre-registered rules for the P4 no-edit analysis. Written and committed before any per-run result was computed.

Scope: the 44 behavior_failed runs among the 53 valid P4 prompt-timing runs (v2+v3+v4 roots). None of them edited.
Question: why no edit. Each failed run gets exactly one class:

  budget      found the reference function late and was stopped by a resource cap before it could act
  not_found   never read a reference function although it had room to search
  found_idle  read a reference function and still had requests left, but never edited
  other       everything else (short runs that ended for non-resource reasons, late finds without a resource stop)

References ("where to change"). The task packages carry no gold patch or PR diff (preparation report: no gold / PR
diff / answer read), so every reference is an approximation, with a reliability label:

  high        files/functions changed by the passing runs of that task (9030: 7 passes, 8945: 2 passes)
  medium      the module that implements the affected behaviour named in the public issue and the task's
              integration notes, where that behaviour lives in one extension module (8756 viewcode, 8637 autosummary)
  medium-low  module from the base-revision failure trace recorded by the preparation preflight plus the module
              the trace's missing case depends on (8775), or a two-module autodoc path (8800)

A function counts as read when a call with a line-range unit (reads.py kind "lines") on a reference file returned
output that contains "def <function>(". A search hit alone does not count; it is reported separately.
"""

from __future__ import annotations

import re

REFERENCES: dict[str, dict] = {
    "public-sphinx-issue-9030": dict(
        files=["sphinx/builders/gettext.py"],
        functions=["_extract_from_template"],
        reliability="high",
        source="passing runs: all 7 changed MessageCatalogBuilder._extract_from_template in sphinx/builders/gettext.py",
    ),
    "public-sphinx-issue-8945": dict(
        files=["sphinx/domains/c.py"],
        functions=["_resolve_xref_inner", "parse_xref_object"],
        reliability="high",
        source="passing runs: one changed CDomain._resolve_xref_inner, one DefinitionParser.parse_xref_object",
    ),
    "public-sphinx-issue-8756": dict(
        files=["sphinx/ext/viewcode.py"],
        functions=["doctree_read", "env_merge_info", "collect_pages"],
        reliability="medium",
        source="issue and integration notes: viewcode incremental reference cleanup; the module-page bookkeeping is "
               "written in doctree_read/env_merge_info and consumed in collect_pages (no env-purge-doc handler at base)",
    ),
    "public-sphinx-issue-8637": dict(
        files=["sphinx/ext/autosummary/generate.py"],
        functions=["generate_autosummary_docs", "generate_autosummary_content"],
        reliability="medium",
        source="issue and integration notes: autosummary :recursive: with :template: applies to first entry only; "
               "recursive stub generation lives in generate.py",
    ),
    "public-sphinx-issue-8800": dict(
        files=["sphinx/ext/autodoc/importer.py", "sphinx/ext/autodoc/__init__.py"],
        functions=["get_class_members", "get_object_members", "filter_members"],
        reliability="medium-low",
        source="issue: annotation-only member of a superclass treated as undocumented under :inherited-members:; "
               "class member collection (importer.get_class_members, ClassDocumenter.get_object_members) and the "
               "documented/undocumented decision (Documenter.filter_members)",
    ),
    "public-sphinx-issue-8775": dict(
        files=["sphinx/util/typing.py", "sphinx/ext/autodoc/__init__.py"],
        functions=["get_type_hints", "is_uninitialized_instance_attribute", "isinstanceattribute"],
        reliability="medium-low",
        source="preflight base trace: importer.import_object -> autodoc_attrgetter -> AttributeError for an "
               "annotation-only 'name: str | int'; the annotation lookup that should rescue it goes through "
               "sphinx.util.typing.get_type_hints and the uninitialized/instance attribute checks in autodoc",
    ),
}

RESOURCE_STOPS = {"insufficient_reserve", "step_limit"}
LATE_MAX_REQUESTS_AFTER = 3   # found with at most this many requests left -> "late"
MIN_SEARCH_REQUESTS = 12      # a run that never found the reference but made at least this many requests had room

CHANGE_WORDS = re.compile(
    r"\b(fix|fixes|fixing|change|modify|patch|replace|add|remove|return|instead)\b|修改|改为|改成|修复|添加|替换|删除",
    re.IGNORECASE)
CODE_BLOCK = re.compile(r"```[^\n]*\n(?:[^\n]*\n){2,}?```")


def plan_text(text: str, functions: list[str], files: list[str]) -> bool:
    """Assistant text that names a reference function or file and states a change, or carries a code block."""
    if not text:
        return False
    if CODE_BLOCK.search(text):
        return True
    names = functions + [f.rsplit("/", 1)[-1] for f in files]
    return any(n in text for n in names) and bool(CHANGE_WORDS.search(text))


def rehearsal(command: str, functions: list[str], files: list[str]) -> bool:
    """A command that tries a change to a reference function without writing the workspace file: redefines or
    monkeypatches it, rewrites its source text in memory, or copies the reference file elsewhere to modify it."""
    if not command:
        return False
    for fn in functions:
        if fn not in command:
            continue
        if re.search(r"def\s+" + re.escape(fn) + r"\s*\(", command):
            return True
        if re.search(r"\." + re.escape(fn) + r"\s*=(?!=)", command) or "setattr(" in command:
            return True
        if ".replace(" in command:
            return True
    return any(re.search(r"\bcp\s+\S*" + re.escape(f) + r"\s+/tmp", command) for f in files)


def classify(located_request: int | None, requests: int, stop: str | None) -> str:
    if located_request is not None:
        after = requests - located_request
        if after >= LATE_MAX_REQUESTS_AFTER + 1:
            return "found_idle"
        return "budget" if stop in RESOURCE_STOPS else "other"
    return "not_found" if requests >= MIN_SEARCH_REQUESTS else "other"


# Budget counterfactual: per-request cost and admission reserve are extrapolated from the run's own last requests
# (slope = median of the last 5 successive differences, floored at 0; level = last observed value, for the reserve
# the rejected request's reserve when present). A further request is admitted while settled + its reserve <= budget.
# Reported with and without the 24-request cap.
COUNTERFACTUAL_BUDGETS = ("0.9", "1.35")
SLOPE_WINDOW = 5
STEP_CAP = 24

# Decision, applied in order to the 44 failed runs.
#   1. budget share >= 50% and median extra requests at 0.9 CNY (uncapped) >= 4        -> rerun with more budget
#   2. found_idle share >= 50%                                                          -> redesign (action step)
#   3. not_found share >= 50% and at least 4 of 6 tasks failed every valid run          -> close
#   4. not_found share >= 50%                                                           -> redesign (tasks/localization)
#   5. otherwise                                                                        -> close
# The decision is marked unreliable if the manual check below misses its bar.
DOMINANT_SHARE = 0.5
MIN_EXTRA_REQUESTS = 4
ALL_FAIL_TASKS_FOR_CLOSE = 4


def decide(counts: dict[str, int], median_extra_09: float | None, all_fail_tasks: int) -> str:
    n = sum(counts.values())
    share = {k: counts.get(k, 0) / n for k in ("budget", "found_idle", "not_found", "other")} if n else {}
    if share.get("budget", 0) >= DOMINANT_SHARE and (median_extra_09 or 0) >= MIN_EXTRA_REQUESTS:
        return "rerun_more_budget"
    if share.get("found_idle", 0) >= DOMINANT_SHARE:
        return "redesign_action"
    if share.get("not_found", 0) >= DOMINANT_SHARE:
        return "close" if all_fail_tasks >= ALL_FAIL_TASKS_FOR_CLOSE else "redesign_tasks"
    return "close"


# Manual check: 12 failed runs drawn with random.Random(SAMPLE_SEED) from the 44 sorted by (batch, run_index).
# For each, the located request and the plan flag are judged by hand from the call log and assistant text.
# Bar: at least 10/12 agreement on located (same request or both none) and at least 10/12 on plan.
SAMPLE_SEED = 6
SAMPLE_SIZE = 12
AGREEMENT_BAR = 10
