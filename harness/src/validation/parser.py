"""Fail-closed parsing of host-launched pytest receipts, never stdout verdicts."""
from __future__ import annotations
import json
from dataclasses import dataclass
from .contracts import PROTOCOL, PROFILES, freeze


@dataclass(frozen=True)
class ParsedResult:
    status: str
    reason: str
    complete: bool
    counts: object
    nodeids: tuple = ()
    phases: tuple = ()


def classify(raw, *, operation_id, runner_sha256, spec, environment, execution_status,
             exit_code, expected_nodeids=None, input_unchanged=True, cleanup_state="cleaned",
             truncated=False):
    counts = dict(collected=0, passed=0, failed=0, errors=0, skipped=0, xfailed=0, xpassed=0)
    nodeids, phases = (), ()
    def result(status, reason, complete=False):
        return ParsedResult(status, reason, complete, freeze(counts), nodeids, phases)
    if execution_status in ("cancelled", "timed_out"):
        return result("inconclusive", "cancelled" if execution_status == "cancelled" else "timeout")
    if truncated or not isinstance(raw, bytes) or len(raw) > spec.limits.max_report_bytes:
        return result("inconclusive", "evidence_incomplete")
    def unique(pairs):
        obj = {}
        for k, v in pairs:
            if k in obj:
                raise ValueError("duplicate key")
            obj[k] = v
        return obj
    try:
        receipt = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
        required = {"protocol", "operation_id", "runner_sha256", "complete", "collection_finished",
                    "nodeids", "phases", "collection_errors", "deselected", "sessionfinish", "exitstatus",
                    "returned_exitstatus", "runner_error", "python_version", "pytest_version", "origins"}
        if not isinstance(receipt, dict) or set(receipt) != required:
            raise ValueError("schema")
        if any(type(receipt[k]) is not bool for k in ("complete", "collection_finished", "sessionfinish")):
            raise ValueError("flags")
        if any(not isinstance(receipt[k], list) for k in ("nodeids", "phases", "collection_errors", "deselected")):
            raise ValueError("arrays")
        if (receipt["protocol"] != PROTOCOL or receipt["operation_id"] != operation_id
                or receipt["runner_sha256"] != runner_sha256):
            return result("inconclusive", "report_mismatch")
        if receipt["runner_error"]:
            return result("inconclusive", "environment_error")
        if not all(receipt[k] for k in ("complete", "collection_finished", "sessionfinish")):
            return result("inconclusive", "evidence_incomplete")
        if (type(exit_code) is not int or type(receipt["exitstatus"]) is not int
                or type(receipt["returned_exitstatus"]) is not int
                or receipt["exitstatus"] != exit_code or receipt["returned_exitstatus"] != exit_code):
            return result("inconclusive", "report_mismatch")
        if (receipt["python_version"] != environment.python_version
                or receipt["pytest_version"] != environment.pytest_version
                or not environment.pytest_version.startswith(PROFILES[spec.adapter])):
            return result("inconclusive", "environment_mismatch")
        expected_origins = dict(pytest="workspace" if spec.runner_is_candidate else "installed")
        expected_origins.update({name: "workspace" for name in spec.imports})
        if receipt["origins"] != expected_origins:
            return result("inconclusive", "environment_mismatch")
        if receipt["collection_errors"]:
            return result("inconclusive", "collection_error")
        nodes = receipt["nodeids"]
        if len(nodes) > spec.limits.max_nodes or any(not isinstance(n, str) or not n for n in nodes):
            raise ValueError("nodes")
        if len(nodes) != len(set(nodes)):
            raise ValueError("duplicate node")
        nodeids = tuple(sorted(nodes))
        counts["collected"] = len(nodeids)
        if not nodeids:
            return result("inconclusive", "no_effective_tests")
        if receipt["deselected"] or (expected_nodeids is not None and set(nodeids) != set(expected_nodeids)):
            return result("inconclusive", "coverage_changed")
        if len(receipt["phases"]) > spec.limits.max_nodes * 3:
            raise ValueError("too many phases")
        by_node = {n: {} for n in nodeids}
        phases = tuple(freeze(v) for v in receipt["phases"])
        for p in phases:
            if (set(p) != {"nodeid", "when", "outcome", "xfail", "exception", "detail"}
                    or p["nodeid"] not in by_node or p["when"] not in ("setup", "call", "teardown")
                    or p["outcome"] not in ("passed", "failed", "skipped")
                    or not isinstance(p["detail"], str)
                    or any(p[k] is not None and not isinstance(p[k], str) for k in ("exception", "xfail"))):
                raise ValueError("phase schema")
            previous = by_node[p["nodeid"]]
            if p["when"] in previous:
                raise ValueError("duplicate phase")
            # Enforce the normal per-node phase sequence, not just a final set.
            if (p["when"] == "call" and set(previous) != {"setup"}
                    or p["when"] == "teardown" and set(previous) not in ({"setup"}, {"setup", "call"})):
                raise ValueError("phase ordering")
            previous[p["when"]] = p
            if p["outcome"] == "skipped": counts["skipped"] += 1
            if p["xfail"] is not None:
                counts["xfailed" if p["outcome"] == "skipped" else "xpassed"] += 1
        if counts["skipped"] or counts["xfailed"] or counts["xpassed"]:
            return result("inconclusive", "no_effective_tests" if counts["skipped"] == len(nodeids) else "coverage_changed")
        for n, reports in by_node.items():
            if any(p["outcome"] == "failed" for k, p in reports.items() if k != "call"):
                counts["errors"] += 1
        if counts["errors"]:
            return result("inconclusive", "environment_error")
        if any(set(reports) != {"setup", "call", "teardown"} for reports in by_node.values()):
            return result("inconclusive", "evidence_incomplete")
        failed = [v["call"] for v in by_node.values() if v["call"]["outcome"] == "failed"]
        counts["failed"], counts["passed"] = len(failed), len(nodeids) - len(failed)
        if any(p["exception"] not in ("builtins.AssertionError", "_pytest.outcomes.Failed") for p in failed):
            return result("inconclusive", "environment_error")
        if exit_code != (1 if failed else 0) or execution_status != ("failed" if failed else "succeeded"):
            return result("inconclusive", "report_mismatch")
        if not input_unchanged:
            return result("inconclusive", "input_mutated")
        if cleanup_state != "cleaned":
            return result("inconclusive", "cleanup_failed")
        if failed:
            return result("behavior_failed", "test_failure", True)
        if counts["passed"] < 1:
            return result("inconclusive", "no_effective_tests")
        return result("passed", "public_checks_passed", True)
    except (ValueError, TypeError, KeyError, UnicodeError, AttributeError, RecursionError):
        return result("inconclusive", "evidence_incomplete")
