"""Rebench variant of ImageGrader: pytest plugins installed in the image are auto-loaded, and the
option flags of the task's test_cmd are passed through. Also collects node ids to repair the ones
SWE-rebench's log parser truncated at the first space. No model calls.
"""

from __future__ import annotations

import json
import shlex
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.preflight_a5_repo_baseline import PYTEST_OBSERVER, prepare_repo_runtime  # noqa: E402
from src.eval.image_grader import APPLY, receipt_diagnostics, score_receipt  # noqa: E402
from src.execution.base import ExecutionRequest, OutputQuotas  # noqa: E402
from src.execution.docker import DockerExecutionBackend  # noqa: E402

AUTOLOAD_LINE = "os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'"
assert AUTOLOAD_LINE in PYTEST_OBSERVER
OBSERVER = PYTEST_OBSERVER.replace(AUTOLOAD_LINE, "os.environ.pop('PYTEST_DISABLE_PLUGIN_AUTOLOAD',None)")
VALUE_OPTIONS = {"-p", "-W", "-m", "-k", "-o", "-c", "--deselect", "--ignore", "--ignore-glob", "--rootdir",
                 "--tb", "--color", "--import-mode", "--timeout"}
DROPPED = {"-rA", "--no-header", "-v", "--verbose", "-vv", "-q"}


def test_cmd_options(test_cmd: str | None) -> list[str]:
    """Option flags of the official test command; positional test paths are dropped (node ids replace them)."""
    tokens = shlex.split(test_cmd or "")
    if "pytest" not in tokens:
        return []
    tokens = tokens[tokens.index("pytest") + 1:]
    kept, index = [], 0
    while index < len(tokens):
        token = tokens[index]
        if token in VALUE_OPTIONS and index + 1 < len(tokens):
            if token not in {"--tb", "--color"}:
                kept += [token, tokens[index + 1]]
            index += 2
            continue
        if token.startswith("-") and token not in DROPPED and not token.startswith(("--tb=", "--color=", "--verbosity")):
            kept.append(token)
        index += 1
    return kept


def repair_nodeids(nodes: list[str], collected: list[str]) -> tuple[list[str], list[dict]]:
    """Map node ids cut at a space back to collected ids that start with the cut text plus a space."""
    have = set(collected)
    repaired, notes = [], []
    for node in nodes:
        if node in have:
            repaired.append(node)
            continue
        matches = [c for c in collected if c.startswith(node + " ")]
        notes.append({"nodeid": node, "matches": matches[:10], "match_count": len(matches)})
        repaired.extend(matches)
    return list(dict.fromkeys(repaired)), notes


class RebenchGrader:
    def __init__(self, *, context: str = "desktop-linux", options: list[str] | None = None):
        self.context = context
        self.options = list(options or [])

    async def _run(self, image: str, delta: dict | None, overlay: dict, args: list[str], timeout: float) -> dict:
        backend = DockerExecutionBackend(
            image=image, context=self.context, memory_bytes=2 * 1024**3,
            workspace_bytes=512 * 1024**2, tmpfs_bytes=256 * 1024**2, network="none",
        )
        result: dict = {"reason": None}
        try:
            await backend.start({})
            await prepare_repo_runtime(backend)
            payload = {"deleted": (delta or {}).get("deleted") or [], "changed": (delta or {}).get("changed") or {},
                       "overlay": overlay or {}}
            await backend._ok("exec", "-i", backend.name, "/usr/local/bin/python", "-I", "-S", "-c", APPLY,
                              data=json.dumps(payload).encode(), limit=64 * 1024, timeout=120)
            await backend._ok("exec", "-i", backend.name, "/usr/local/bin/python", "-I", "-S", "-c",
                              "import sys;open('/tmp/a5-runner.py','wb').write(sys.stdin.buffer.read())",
                              data=OBSERVER.encode(), limit=256 * 1024)
            request = ExecutionRequest(
                argv=["/usr/local/bin/python", "-I", "-B", "/tmp/a5-runner.py"],
                stdin_bytes=json.dumps({"args": args, "labels": args, "collect_only": False}).encode(),
                env={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "", "PYTHONPATH": "/workspace:/workspace/src",
                     "A5_REPO_ROOT": "/workspace", "PYTHONDONTWRITEBYTECODE": "1", "HOME": "/tmp"},
                deadline_mono=time.monotonic() + timeout,
                quotas=OutputQuotas(memory_per_stream_bytes=4 * 1024**2, artifact_per_tool_bytes=16 * 1024**2),
            )
            execution = await backend.execute(request)
            result["execution_status"] = execution.status.value
            result["execution_stderr_tail"] = (execution.stderr.text_preview or "")[-2048:]
            raw = await backend._ok("exec", backend.name, "/usr/local/bin/python", "-I", "-S", "-c",
                                    "import sys;sys.stdout.buffer.write(open('/tmp/a5-pytest-receipt.json','rb').read())",
                                    limit=16 * 1024**2)
            result["receipt"] = json.loads(raw)
        except Exception as exc:
            result["reason"] = type(exc).__name__ + ": " + str(exc)[:500]
        finally:
            await backend.close()
        return result

    async def collect(self, *, image: str, overlay: dict, paths: list[str], timeout: float = 600) -> dict:
        run = await self._run(image, None, overlay, self.options + ["--collect-only", "-q", *paths], timeout)
        receipt = run.get("receipt") or {}
        return {"collected": receipt.get("collected") or [], "reason": run.get("reason"),
                **receipt_diagnostics(receipt)}

    async def evaluate(self, *, image: str, delta: dict | None, test_overlay: dict, f2p: list[str], p2p: list[str],
                       timeout: float = 600) -> dict:
        run = await self._run(image, delta, test_overlay, self.options + list(f2p) + list(p2p), timeout)
        receipt = run.get("receipt")
        result = {"status": "invalid_env", "solved": False, "reason": run.get("reason"),
                  "execution_status": run.get("execution_status")}
        if receipt is not None:
            result.update(score_receipt(receipt, f2p, p2p))
            result.update(receipt_diagnostics(receipt))
        return result
