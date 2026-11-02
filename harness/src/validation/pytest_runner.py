"""Standalone trusted public pytest observer (also copied into fixture replicas).

Only the host launches this file, with a fixed JSON request. No shell, grader,
JUnit heuristics or printed summary is used as a verdict. Compatible hook API
is intentionally small; actual pytest 8 / 4.5 execution is a separate acceptance.
"""
from __future__ import annotations
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys

PROTOCOL = "public-pytest-v1"


def main(request_path, report_path):
    req = json.loads(Path(request_path).read_bytes())
    workspace = Path(req["workspace"]).resolve()
    sys.dont_write_bytecode = True
    os.chdir(workspace)
    sys.path[:0] = [str(workspace), str(workspace / "src")]
    receipt = dict(protocol=PROTOCOL, operation_id=req["operation_id"],
                   runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   complete=False, collection_finished=False, nodeids=[], phases=[],
                   collection_errors=[], deselected=[], sessionfinish=False, exitstatus=None,
                   returned_exitstatus=None, runner_error=None,
                   python_version=".".join(str(v) for v in sys.version_info[:3]),
                   pytest_version=None, origins={})

    def origin(module):
        location = getattr(module, "__file__", None)
        if not location:
            return "unknown"
        path = Path(location).resolve()
        if workspace == path or workspace in path.parents:
            return "workspace"
        if "site-packages" in path.parts or "dist-packages" in path.parts:
            return "installed"
        return "other"

    try:
        import pytest
        receipt["pytest_version"] = pytest.__version__
        receipt["origins"]["pytest"] = origin(pytest)
        for module in req["imports"]:
            receipt["origins"][module] = origin(importlib.import_module(module))

        class Observer:
            def pytest_collection_finish(self, session):
                receipt["collection_finished"] = True
                receipt["nodeids"] = [item.nodeid for item in session.items]

            def pytest_collectreport(self, report):
                if report.failed:
                    receipt["collection_errors"].append(str(report.longrepr)[:4096])

            def pytest_deselected(self, items):
                receipt["deselected"].extend(item.nodeid for item in items)

            @pytest.hookimpl(hookwrapper=True)
            def pytest_runtest_makereport(self, item, call):
                result = yield
                report = result.get_result()
                report._public_exception = (call.excinfo.type.__module__ + "." + call.excinfo.type.__name__
                                            if call.excinfo is not None else None)

            def pytest_runtest_logreport(self, report):
                xfail = getattr(report, "wasxfail", None)
                if report.failed and str(report.longrepr).startswith("[XPASS(strict)]"):
                    xfail = "strict_xpass"
                receipt["phases"].append(dict(nodeid=report.nodeid, when=report.when,
                    outcome=report.outcome, xfail=xfail, exception=getattr(report, "_public_exception", None),
                    detail=str(report.longrepr)[:4096] if report.longrepr else ""))

            def pytest_sessionfinish(self, session, exitstatus):
                receipt["sessionfinish"] = True
                receipt["exitstatus"] = int(exitstatus)

        receipt["returned_exitstatus"] = int(pytest.main(req["args"], plugins=[Observer()]))
        receipt["complete"] = receipt["sessionfinish"]
    except BaseException as exc:
        receipt["runner_error"] = type(exc).__name__
    # Atomic publication. A killed/aborted interpreter cannot leave a valid partial report.
    target = Path(report_path)
    staging = target.with_suffix(".tmp")
    staging.write_text(json.dumps(receipt, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")
    os.replace(staging, target)
    return receipt["returned_exitstatus"] if receipt["returned_exitstatus"] is not None else 3


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))
