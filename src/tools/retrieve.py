"""retrieve_code: the frozen CR-1 hybrid retriever over the task's base-commit snapshot.

Runs on the host and reads only the CR cache (CR_CACHE_DIR). It never reads the host or
container workspace and runs no command. Every call is appended to ``retrieval-calls.jsonl``
in the run directory.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import traceback
from pathlib import Path

from src.tools.base import SideEffectClass, Tool, ToolContext, ToolResult

MAX_QUERY_CHARS = 2000
DEFAULT_K = 5
MAX_K = 10


class TaskRetrieval:
    """What the tool needs for one run: the task's snapshot index and the call log path."""

    def __init__(self, index, log_path: Path):
        self.index = index
        self.log_path = Path(log_path)
        self._lock = threading.Lock()

    def log(self, row: dict) -> None:
        with self._lock:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")


class RetrieveCodeTool(Tool):
    name = "retrieve_code"
    description = (
        "Search the original repository at the base revision (before any edits made in this session) "
        "for the files most related to a query. Returns up to k file paths, each with at most two "
        "symbols and their line ranges. Does not return code. Read-only."
    )
    side_effect = SideEffectClass.READ_ONLY
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": MAX_QUERY_CHARS,
                      "description": "What to look for: issue text, an error message, identifiers or a description. At most 2000 characters."},
            "k": {"type": "integer", "minimum": 1, "maximum": MAX_K,
                  "description": "Number of files to return, 1 to 10. Default 5."},
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(self, retrieval: TaskRetrieval | None = None):
        self.retrieval = retrieval

    async def execute(self, params: dict, ctx: ToolContext) -> ToolResult:
        call_id = self.resolve_call_id(params, ctx)
        query = params.get("query")
        k = params.get("k", DEFAULT_K)
        row = {"call_id": call_id, "query": query, "k": k}
        error = None
        if self.retrieval is None:
            error = "retrieve_code has no retrieval service"
        elif not isinstance(query, str) or not query.strip():
            error = "query is required"
        elif len(query) > MAX_QUERY_CHARS:
            error = f"query is longer than {MAX_QUERY_CHARS} characters"
        elif isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= MAX_K:
            error = f"k must be an integer from 1 to {MAX_K}"
        if error:
            if self.retrieval is not None:
                self.retrieval.log({**row, "error": error})
            return ToolResult(call_id, self.name, False, "", error=error)
        from src.retrieval.serve import hits_record, render_tool_result

        started = time.monotonic()
        try:
            hits, source = await asyncio.to_thread(self.retrieval.index.search, query, k)
        except Exception as exc:
            trace = traceback.format_exc()
            self.retrieval.log({**row, "error": type(exc).__name__ + ": " + str(exc)[:300], "traceback": trace})
            return ToolResult(call_id, self.name, False, trace, error="retrieve_code failed: " + type(exc).__name__)
        self.retrieval.log({**row, "query_vector": source, "seconds": round(time.monotonic() - started, 3),
                            "files": hits_record(hits)})
        return ToolResult(call_id, self.name, True, render_tool_result(hits))
