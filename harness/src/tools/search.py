"""search_code: read-only grep. Host walk for tests; rg or grep inside Docker."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from src.execution.base import ExecutionRequest
from src.tools.base import SideEffectClass, Tool, ToolContext, ToolResult
from src.tools.command import RunCommandTool

MAX_RESULTS_CAP = 200
SEARCH_CODE = r'''
import json, shutil, subprocess, sys, pathlib
req = json.load(sys.stdin)
pattern = req["pattern"]
rel = req.get("path") or "."
glob = req.get("glob") or ""
cap = max(1, min(int(req.get("max_results") or 50), 200))
root = pathlib.Path("/workspace")
target = (root / rel).resolve()
try:
    target.relative_to(root)
except ValueError:
    raise SystemExit("path denied") from None
if shutil.which("rg"):
    cmd = ["rg", "-n", "--no-heading", "--color", "never"]
    if glob:
        cmd += ["--glob", glob]
    cmd += [pattern, str(target)]
else:
    cmd = ["grep", "-R", "-n", "-I"]
    if glob:
        cmd += ["--include", glob]
    cmd += ["-e", pattern, str(target)]
proc = subprocess.run(cmd, cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
lines = [ln for ln in proc.stdout.splitlines() if ln]
shown = lines[:cap]
sys.stdout.write("\n".join(shown))
if len(lines) > cap:
    sys.stdout.write(f"\n[{len(lines) - cap} more matches omitted]")
'''


def _format_match(path: str, line_no: int, text: str) -> str:
    return f"{path}:{line_no}: {text}"


def search_workspace(root: Path, *, pattern: str, path: str, glob: str, max_results: int) -> str:
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"invalid pattern: {exc}") from exc
    start = (root / (path or ".")).resolve()
    if not start.is_relative_to(root.resolve()):
        raise ValueError("path denied")
    if not start.exists():
        raise ValueError(f"path not found: {path}")
    files = [start] if start.is_file() else sorted(p for p in start.rglob("*") if p.is_file() and ".git" not in p.parts)
    if glob:
        files = [p for p in files if p.match(glob)]
    matches: list[str] = []
    for file in files:
        try:
            text = file.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        rel = file.relative_to(root.resolve()).as_posix()
        for number, line in enumerate(text.splitlines(), start=1):
            if compiled.search(line):
                matches.append(_format_match(rel, number, line))
                if len(matches) > max_results:
                    break
        if len(matches) > max_results:
            break
    shown = matches[:max_results]
    if len(matches) > max_results:
        shown.append(f"[{len(matches) - max_results} more matches omitted]")
    return "\n".join(shown)


class SearchCodeTool(Tool):
    name = "search_code"
    description = "Search the workspace. Returns path:line: text. Read-only."
    side_effect = SideEffectClass.READ_ONLY
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regular expression to search for."},
            "path": {"type": "string", "description": "File or directory relative to the workspace. Default is the workspace root."},
            "glob": {"type": "string", "description": "Optional glob, for example *.py."},
            "max_results": {"type": "integer", "description": "Maximum matches to return. Default 50."},
        },
        "required": ["pattern"],
        "additionalProperties": False,
    }

    def __init__(self, execution_backend=None, observation_bytes_kept="legacy"):
        self.backend = execution_backend
        self.observation_bytes_kept = observation_bytes_kept

    async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
        call_id = self.resolve_call_id(params, ctx)
        pattern = params.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return ToolResult(call_id, self.name, False, "", error="pattern is required")
        path = str(params.get("path") or ".")
        glob = str(params.get("glob") or "")
        raw_max = params.get("max_results", 50)
        max_results = raw_max if isinstance(raw_max, int) and raw_max > 0 else 50
        max_results = min(max_results, MAX_RESULTS_CAP)
        if self.backend is None:
            try:
                output = search_workspace(ctx.workspace_root, pattern=pattern, path=path, glob=glob, max_results=max_results)
            except ValueError as exc:
                return ToolResult(call_id, self.name, False, "", error=str(exc))
            return ToolResult(call_id, self.name, True, output)
        deadline = ctx.extra.get("deadline")
        request = ExecutionRequest(
            argv=["/usr/local/bin/python", "-I", "-S", "-c", SEARCH_CODE],
            stdin_bytes=json.dumps({"pattern": pattern, "path": path, "glob": glob, "max_results": max_results}).encode(),
            env={},
            cwd=ctx.workspace_root,
            cancel_event=ctx.extra.get("cancel_event"),
            deadline_mono=deadline.effective_deadline_mono() if deadline else time.monotonic() + 30,
            run_meter=ctx.extra.get("run_output_meter"),
        )
        result = await self.backend.execute(request)
        return ToolResult(
            call_id, self.name, result.status.value == "succeeded", result.observation_text(self.observation_bytes_kept),
            error=result.error_message, status=RunCommandTool._map_status(result.status),
            error_kind=result.error_kind, exit_code=result.exit_code,
        )
