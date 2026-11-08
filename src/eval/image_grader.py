"""Grade a candidate delta inside the baseline image. The source tree is not uploaded."""
from __future__ import annotations

import base64
import json
import shlex
import time
from pathlib import Path

from src.execution.base import ExecutionRequest, OutputQuotas
from src.execution.baseline_image_backend import is_export_skipped
from src.execution.docker import DockerExecutionBackend

APPLY = r'''
import base64, json, pathlib, sys
req = json.load(sys.stdin)
root = pathlib.Path("/workspace")
for name in req.get("deleted") or []:
    path = root / name
    if path.is_file() or path.is_symlink():
        path.unlink()
for name, encoded in (req.get("changed") or {}).items():
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(encoded))
for name, encoded in (req.get("overlay") or {}).items():
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(encoded))
'''

# export_keep_original_files: which of the candidate paths (stdin, JSON list) are original source files of
# the image. Same rule as the export script in baseline_image_backend.
ORIGINALS_AMONG = r'''
import json, pathlib, sys
root = pathlib.Path("/opt/repo")
extra = set(json.loads(pathlib.Path("/opt/repo-extra-files.json").read_text()))
found = []
for name in json.load(sys.stdin):
    path = root / name
    if name not in extra and path.is_file() and not path.is_symlink():
        found.append(name)
json.dump(sorted(found), sys.stdout)
'''


def grader_payload(delta: dict | None, overlay: dict | None, original_files=None) -> dict:
    """Files the grader writes into the workspace, filtered by the same rule as the export."""
    return {
        "deleted": [name for name in ((delta or {}).get("deleted") or []) if not is_export_skipped(name, original_files)],
        "changed": {
            name: encoded
            for name, encoded in ((delta or {}).get("changed") or {}).items()
            if not is_export_skipped(name, original_files)
        },
        "overlay": overlay or {},
    }


CONFIG_NAMES = {"conftest.py", "setup.cfg", "setup.py", "pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg"}

AUTOLOAD_OFF = "os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'"
AUTOLOAD_KEPT = "os.environ.pop('PYTEST_DISABLE_PLUGIN_AUTOLOAD',None)"
# Options of a task's test command that take a separate value. --tb and --color are dropped: they only
# change console output, which the receipt does not read.
VALUE_OPTIONS = {"-p", "-W", "-m", "-k", "-o", "-c", "--deselect", "--ignore", "--ignore-glob", "--rootdir",
                 "--import-mode", "--timeout", "--tb", "--color"}
CONSOLE_ONLY = {"-rA", "--no-header", "-v", "--verbose", "-vv", "-q", "--tb", "--color"}


def pytest_options_from_test_cmd(test_cmd: str | None) -> list[str]:
    """Option flags of a task's pytest command. Positional test paths are dropped; node ids replace them."""
    tokens = shlex.split(test_cmd or "")
    if "pytest" not in tokens:
        return []
    tokens = tokens[tokens.index("pytest") + 1:]
    kept, index = [], 0
    while index < len(tokens):
        token = tokens[index]
        if token in VALUE_OPTIONS and index + 1 < len(tokens):
            if token not in CONSOLE_ONLY:
                kept += [token, tokens[index + 1]]
            index += 2
            continue
        if (token.startswith("-") and token not in CONSOLE_ONLY
                and not token.startswith(("--tb=", "--color=", "--verbosity"))):
            kept.append(token)
        index += 1
    return kept


def repair_truncated_nodeids(nodes: list[str], collected: list[str]) -> tuple[list[str], list[dict]]:
    """Map node ids that a log parser cut at the first space back to the collected ids they start."""
    have = set(collected)
    repaired, notes = [], []
    for node in nodes:
        if node in have:
            repaired.append(node)
            continue
        matches = [item for item in collected if item.startswith(node + " ")]
        notes.append({"nodeid": node, "matches": matches[:10], "match_count": len(matches)})
        repaired.extend(matches)
    return list(dict.fromkeys(repaired)), notes


def sandbox_p2p_exclusions(gold_evaluations: list[dict], p2p: list[str]) -> list[str]:
    """P2P node ids that did not pass in every gold-patch evaluation inside the grading sandbox."""
    excluded = []
    for node in p2p:
        for evaluation in gold_evaluations:
            outcomes = {case.get("nodeid"): case.get("outcome") for case in evaluation.get("cases") or []}
            if outcomes.get(node) != "passed":
                excluded.append(node)
                break
    return excluded


def touched_kinds(paths: list[str]) -> dict:
    tests, configs = [], []
    for name in paths:
        base = Path(name).name
        if base in CONFIG_NAMES or base == "conftest.py":
            configs.append(name)
        if "/tests/" in name or "/test/" in name or base.startswith("test_") or base == "conftest.py":
            tests.append(name)
    return {"test_files_touched": tests, "config_files_touched": configs}


def score_receipt(receipt: dict | None, f2p: list[str], p2p: list[str]) -> dict:
    """Judge each node id from the receipt. Exit codes and printed counts are not used."""
    from scripts.preflight_a5_repo_baseline import outcomes

    by_id = outcomes(receipt or {})
    def count(nodes):
        passed = sum(1 for node in nodes if by_id.get(node) == "passed")
        return passed, len(nodes)
    f2p_passed, f2p_total = count(f2p)
    p2p_passed, p2p_total = count(p2p)
    missing = [node for node in f2p + p2p if node not in by_id]
    solved = f2p_total > 0 and f2p_passed == f2p_total and p2p_passed == p2p_total and not missing
    status = "pass" if solved else "partial" if receipt else "invalid_env"
    if receipt and not solved:
        status = "fail" if f2p_passed == 0 else "partial"
    return {
        "status": status,
        "solved": solved,
        "f2p_passed": f2p_passed,
        "f2p_total": f2p_total,
        "p2p_passed": p2p_passed,
        "p2p_total": p2p_total,
        "cases": [{"nodeid": node, "outcome": by_id.get(node, "missing")} for node in f2p + p2p],
        "reason": None if receipt else "missing_receipt",
    }


def receipt_diagnostics(receipt: dict | None) -> dict:
    """Host-only grader notes. These must not be copied into the agent prompt."""
    errors = []
    tails = []
    for item in (receipt or {}).get("collection_errors") or []:
        text = item if isinstance(item, str) else json.dumps(item, default=str)
        errors.append(text[:2048])
        detail = item.get("detail") if isinstance(item, dict) else item
        tails.append(str(detail or "")[-2048:])
        if len(errors) >= 20:
            break
    collected = (receipt or {}).get("collected") or []
    return {
        "collected_count": len(collected) if isinstance(collected, list) else 0,
        "collection_errors": errors,
        "collection_error_tails": tails,
        "runner_error": (receipt or {}).get("runner_error"),
        "exit_code": (receipt or {}).get("exit_code"),
    }


class ImageGrader:
    def __init__(self, *, context: str = "desktop-linux"):
        self.context = context

    async def collect(self, *, image: str, test_overlay: dict, paths: list[str], autoload_plugins: bool = False,
                      pytest_options: list[str] | None = None, timeout: float = 600, repo_root: str = "/opt/repo",
                      python_bin: str = "/usr/local/bin/python", delta: dict | None = None,
                      keep_original_files: bool = False) -> dict:
        """Collect node ids with the test overlay (and delta, if given) applied. Used to repair truncated ids
        before grading; a test module importing a name the gold patch adds only collects with the gold delta."""
        args = list(pytest_options or []) + ["--collect-only", "-q", *paths]
        result = await self._run(image=image, delta=delta, test_overlay=test_overlay, args=args, sympy=False,
                                 django=False, autoload_plugins=autoload_plugins, timeout=timeout,
                                 repo_root=repo_root, python_bin=python_bin, keep_original_files=keep_original_files)
        receipt = result.pop("receipt", None) or {}
        return {"collected": list(receipt.get("collected") or []), "reason": result.get("reason"),
                **receipt_diagnostics(receipt)}

    async def evaluate(self, *, image: str, delta: dict | None, test_overlay: dict, f2p: list[str], p2p: list[str], django: bool = False, sympy: bool = False, timeout: float = 300, repo_root: str = "/opt/repo", python_bin: str = "/usr/local/bin/python", autoload_plugins: bool = False, pytest_options: list[str] | None = None, keep_original_files: bool = False, memory_bytes: int = 2 * 1024**3) -> dict:
        nodeids = list(f2p) + list(p2p)
        result = await self._run(image=image, delta=delta, test_overlay=test_overlay,
                                 args=list(pytest_options or []) + nodeids, sympy=sympy, django=django,
                                 autoload_plugins=autoload_plugins, timeout=timeout, repo_root=repo_root,
                                 python_bin=python_bin, keep_original_files=keep_original_files,
                                 memory_bytes=memory_bytes)
        receipt = result.pop("receipt", None)
        retries = result.pop("docker_retries")
        if receipt is not None:
            result.update(score_receipt(receipt, f2p, p2p))
            result.update(receipt_diagnostics(receipt))
            result["receipt_complete"] = receipt.get("complete")
        result["docker_retries"] = retries
        changed = list(((delta or {}).get("changed") or {}).keys()) + list((delta or {}).get("deleted") or [])
        result.update(touched_kinds(changed))
        return result

    async def _run(self, *, image: str, delta: dict | None, test_overlay: dict, args: list[str], django: bool,
                   sympy: bool, autoload_plugins: bool, timeout: float, repo_root: str, python_bin: str,
                   keep_original_files: bool = False, memory_bytes: int = 2 * 1024**3) -> dict:
        from scripts.preflight_a5_repo_baseline import PYTEST_OBSERVER, prepare_repo_runtime, upload_runner

        backend = DockerExecutionBackend(
            image=image, context=self.context, memory_bytes=memory_bytes,
            workspace_bytes=512 * 1024**2, tmpfs_bytes=256 * 1024**2, network="none",
            python_bin=python_bin,
        )
        result = {"status": "invalid_env", "solved": False, "reason": None}
        try:
            await backend.start({})
            await prepare_repo_runtime(backend, repo_root=repo_root, python_bin=python_bin)
            originals = None
            if keep_original_files:
                names = list(((delta or {}).get("changed") or {}).keys()) + list((delta or {}).get("deleted") or [])
                candidates = [name for name in names if is_export_skipped(name)]
                originals = set()
                if candidates:
                    raw = await backend._ok(
                        "exec", "-i", backend.name, python_bin, "-I", "-S", "-c",
                        ORIGINALS_AMONG.replace('pathlib.Path("/opt/repo")', "pathlib.Path(" + json.dumps(repo_root) + ")"),
                        data=json.dumps(candidates).encode(), limit=1024 * 1024, timeout=120,
                    )
                    originals = set(json.loads(raw))
            payload = grader_payload(delta, test_overlay, originals)
            await backend._ok(
                "exec", "-i", backend.name, python_bin, "-I", "-S", "-c", APPLY,
                data=json.dumps(payload).encode(), limit=64 * 1024, timeout=120,
            )
            if autoload_plugins and not django:
                runner = PYTEST_OBSERVER.replace(AUTOLOAD_OFF, AUTOLOAD_KEPT)
                if runner == PYTEST_OBSERVER:
                    raise RuntimeError("pytest observer no longer disables plugin autoload in the expected line")
                await backend._ok("exec", "-i", backend.name, python_bin, "-I", "-S", "-c",
                                  "import sys;open('/tmp/a5-runner.py','wb').write(sys.stdin.buffer.read())",
                                  data=runner.encode(), limit=256 * 1024)
            else:
                await upload_runner(backend, django=django, python_bin=python_bin)
            request = ExecutionRequest(
                argv=[python_bin, "-I", "-B", "/tmp/a5-runner.py"],
                stdin_bytes=json.dumps({
                    "args": args, "labels": args, "collect_only": False,
                    "sympy_native_helpers": sympy,
                }).encode(),
                env={
                    "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "" if autoload_plugins and not django else "1",
                    "PYTHONPATH": "/workspace:/workspace/src",
                    "A5_REPO_ROOT": "/workspace",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                deadline_mono=time.monotonic() + timeout,
                quotas=OutputQuotas(memory_per_stream_bytes=4 * 1024**2, artifact_per_tool_bytes=16 * 1024**2),
            )
            execution = await backend.execute(request)
            result["execution_status"] = execution.status.value
            result["execution_stderr_tail"] = (execution.stderr.text_preview or "")[-4096:]
            raw = await backend._ok(
                "exec", backend.name, python_bin, "-I", "-S", "-c",
                "from pathlib import Path; import sys; p=Path('/tmp/a5-pytest-receipt.json'); sys.stdout.buffer.write(p.read_bytes())",
                limit=16 * 1024**2,
            )
            result["receipt"] = json.loads(raw)
        except Exception as exc:
            result["reason"] = type(exc).__name__ + ": " + str(exc)[:500]
        finally:
            await backend.close()
            result["docker_retries"] = list(backend.docker_retries)
        return result
