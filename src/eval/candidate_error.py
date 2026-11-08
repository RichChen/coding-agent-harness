"""Missing grader ids caused by the candidate's own edit (e.g. an IndentationError it introduced).

Rule: take the grader receipt's runner_error and the tail of each collection-error traceback. If the innermost
frame that belongs to the project is in a file the candidate changed, the run is an ordinary
failure. Otherwise the same image is graded once more with an empty delta (missing_regrade); if that has no
missing id and matches calibration, the run is an ordinary failure too ("execution"). A run whose test
execution timed out and left a grader reason gets the same empty-delta regrade; a consistent one makes it an
ordinary failure ("execution_timeout"). Anything else stays a stop condition.
"""
from __future__ import annotations

import re

REPO_ROOT = "/workspace/"
FRAME = re.compile(r'File "([^"]+)", line \d+|^(?:E\s+)?([^\s"\']+\.py):\d+: in ', re.MULTILINE)


def project_path(path: str) -> str | None:
    """Repo-relative path, or None for stdlib, site-packages, runner scripts and frozen modules."""
    if path.startswith(REPO_ROOT):
        return path[len(REPO_ROOT):]
    if path.startswith(("/", "<")) or re.match(r"^[A-Za-z]:[\\/]", path):
        return None
    return path[2:] if path.startswith("./") else path


def innermost_project_frame(text: str) -> str | None:
    frames = [project_path(match.group(1) or match.group(2)) for match in FRAME.finditer(text or "")]
    frames = [frame for frame in frames if frame]
    return frames[-1] if frames else None


def tracebacks(evaluation: dict) -> list[tuple[str, str]]:
    sources = []
    if evaluation.get("runner_error"):
        sources.append(("runner_error", str(evaluation["runner_error"])))
    for index, item in enumerate(evaluation.get("collection_error_tails") or []):
        sources.append(("collection_errors[" + str(index) + "]", str(item)))
    return sources


def grader_candidate_error(evaluation: dict, changed: list[str]) -> dict | None:
    """Where the candidate broke the test run, or None. An empty candidate never qualifies."""
    changed_set = set(changed or [])
    if not changed_set:
        return None
    for source, text in tracebacks(evaluation):
        frame = innermost_project_frame(text)
        if frame in changed_set:
            return {"source": source, "file": frame}
    return None


def missing_count(evaluation: dict) -> int:
    return sum(1 for case in evaluation.get("cases") or [] if case.get("outcome") == "missing")


def expected_empty(calibration: dict | None) -> dict | None:
    """Empty-delta counts the runtime grader should reproduce, as [passed, total].

    P2P follows the approved p2p_excluded reading: excluded ids leave the runtime set, and in calibration they
    all failed under the empty delta, so the total drops by their count and the passed count stays.
    A calibration with empty_missing lists ids every empty-delta calibration run reported missing, e.g. a test
    module that imports a name only the gold patch adds; the regrade must then report exactly those as missing.
    """
    if not calibration or calibration.get("empty_f2p") is None or calibration.get("empty_p2p") is None:
        return None
    excluded = len(calibration.get("p2p_excluded") or [])
    passed, total = calibration["empty_p2p"]
    expected = {"f2p": list(calibration["empty_f2p"]), "p2p": [passed, total - excluded]}
    if calibration.get("empty_missing"):
        expected["missing"] = sorted(calibration["empty_missing"])
    return expected


def missing_ids(evaluation: dict) -> list[str]:
    return sorted(case.get("nodeid") for case in evaluation.get("cases") or [] if case.get("outcome") == "missing")


def regrade_verdict(regrade: dict, expected: dict | None) -> dict:
    """missing_regrade: the empty-delta regrade matches calibration, missing ids included (none unless the
    calibration recorded some)."""
    got = {
        "f2p": [regrade.get("f2p_passed"), regrade.get("f2p_total")],
        "p2p": [regrade.get("p2p_passed"), regrade.get("p2p_total")],
    }
    missing = missing_count(regrade)
    consistent = (expected is not None and not regrade.get("reason")
                  and got == {"f2p": expected["f2p"], "p2p": expected["p2p"]}
                  and missing_ids(regrade) == expected.get("missing", []))
    return {
        "status": regrade.get("status"),
        "reason": regrade.get("reason"),
        "missing": missing,
        **got,
        "expected": expected,
        "consistent": consistent,
    }


def classify_missing(evaluation: dict, changed: list[str], verdict: dict | None):
    """grader_candidate_error for a run whose grader reported missing ids, or None to stop.

    The traceback rule comes first. Otherwise a consistent empty-delta regrade means the candidate broke the
    run during execution: an ordinary failure recorded as "execution".
    """
    if not missing_count(evaluation):
        return None
    found = grader_candidate_error(evaluation, changed)
    if found is not None:
        return found
    if verdict is not None and verdict["consistent"]:
        return "execution"
    return None


def timed_out_with_reason(evaluation: dict) -> bool:
    """Test execution ran past its timeout and the grader then failed (no receipt), leaving a reason."""
    return bool(evaluation.get("reason")) and evaluation.get("execution_status") == "timed_out"


def classify_timeout(evaluation: dict, verdict: dict | None):
    """grader_candidate_error for a timed-out run with a grader reason, or None to stop.

    A consistent empty-delta regrade means the image and grader are fine and the candidate made the tests run
    past the timeout: an ordinary failure recorded as "execution_timeout", with every F2P/P2P id failed.
    """
    if not timed_out_with_reason(evaluation):
        return None
    if verdict is not None and verdict["consistent"]:
        return "execution_timeout"
    return None
