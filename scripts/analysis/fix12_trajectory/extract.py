"""Trajectory features for the 180 FIX-12 pilot runs (A, U, U+T). Read-only, no model calls, no Docker.

Reads runs/unleash-pilot/<task>/<group>/<repeat>/ (events.jsonl, session/artifacts, summary.json, cny-ledger.json)
and the question-2 rows (scripts/analysis/fix12_no_edit/out/runs.json) for located/class. Writes out/runs.json
(one row per run, with a per-request series) for analyze.py.

Per call: kind (read, search, edit, test, run, list, other), failed (tool error or nonzero exit), key (tool name and
arguments, for exact-repeat checks). Per request: prompt/completion/reasoning tokens, finish reason, tool calls,
masked tool results in the prepared context, visible content and reasoning_content lengths.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HARNESS = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / "reread"))
from _load import payload, read_events, step_number  # noqa: E402
from reads import call_io  # noqa: E402
from reread import call_kind  # noqa: E402

RUNS = HARNESS / "runs" / "unleash-pilot"
NO_EDIT = HERE.parent / "fix12_no_edit" / "out" / "runs.json"
OUT = HERE / "out"
EXIT = re.compile(r"\[exit_code=(-?\d+)")
TEST = re.compile(r"\bpytest\b|\bunittest\b|\btox\b|\bnox\b")
PY_RUN = re.compile(r"\bpython[\d.]*\b")


def kind_of(name: str, io, command: str) -> str:
    k = call_kind(name, io, command)
    if k == "edit":
        return "edit"
    if name == "run_command" and TEST.search(command):
        return "test"
    if name == "search_code" or (io.units and all(u.kind == "search" for u in io.units)):
        return "search"
    if k == "read":
        return "read"
    if k == "list":
        return "list"
    if name == "run_command" and PY_RUN.search(command):
        return "run"
    return "other"


def assistant_messages(run_dir: Path, events: list[dict]) -> list[dict]:
    last = next((e for e in reversed(events) if e["event_type"] == "state.saved"), None)
    return [m for m in payload(run_dir, last)["messages"] if m.get("role") == "assistant"] if last else []


def text_of(content) -> str:
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content or ""


def run_row(task: str, group: str, repeat: str, no_edit: dict) -> dict:
    run_dir = RUNS / task / group / repeat
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    ledger = json.loads((run_dir / "cny-ledger.json").read_text(encoding="utf-8"))
    events = read_events(run_dir)
    results = {e["tool_call_id"]: e for e in events if e["event_type"] == "tool.result"}
    requests, calls, masked = [], [], {}
    request_of_step: dict[int, int] = {}
    for e in events:
        t = e["event_type"]
        if t == "context.prepared":
            m = (payload(run_dir, e).get("stats") or {}).get("masking") or {}
            masked[step_number(e)] = m.get("compacted_tool_count", 0)
        elif t == "model.started":
            requests.append(dict(step=step_number(e), masked=masked.get(step_number(e), 0)))
            request_of_step.setdefault(step_number(e), len(requests))
        elif t == "model.settled":
            rec = payload(run_dir, e).get("record") or {}
            meta = rec.get("provider_metadata") or {}
            usage = meta.get("raw_usage") or {}
            req = requests[-1]
            req.update(prompt=usage.get("prompt_tokens"), completion=usage.get("completion_tokens"),
                       reasoning=(usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0,
                       cache_hit=usage.get("prompt_cache_hit_tokens") or 0,
                       finish=meta.get("finish_reason"), ok=rec.get("error_kind") is None,
                       elapsed_ms=rec.get("elapsed_ms"))
        elif t == "tool.intent":
            p = payload(run_dir, e)
            res = results.get(e["tool_call_id"])
            rp = payload(run_dir, res) if res else {}
            args = p.get("arguments") or {}
            output = rp.get("output") if isinstance(rp.get("output"), str) else None
            io = call_io(p["name"], args, output, bool(rp.get("success")))
            command = args.get("command") if isinstance(args.get("command"), str) else ""
            m = EXIT.search(output or "")
            calls.append(dict(request=request_of_step.get(step_number(e), (step_number(e) or 0) + 1),
                              name=p["name"], kind=kind_of(p["name"], io, command),
                              failed=not rp.get("success") or bool(m and m.group(1) != "0"),
                              key=json.dumps([p["name"], args], sort_keys=True),
                              out_chars=len(output or ""), out_head=(output or "")[:300]))
    msgs = assistant_messages(run_dir, events)
    ok_requests = [r for r in requests if r.get("ok")]
    for r, m in zip(ok_requests, msgs):
        r["content_chars"] = len(text_of(m.get("content")).strip())
        r["reasoning_chars"] = len(m.get("reasoning_content") or "")
        r["tool_calls"] = len(m.get("tool_calls") or [])
    q2 = no_edit[(task, group, repeat)]
    return dict(task=task, group=group, repeat=repeat, status=summary["status"], stop=summary.get("stop_reason"),
                passed=summary["status"] == "pass", cost=float(ledger.get("cost_committed") or 0),
                changed_files=summary.get("changed_files") or [], located=q2["located_request"],
                first_edit=q2["first_edit_request"], cls=q2.get("cls"), source_change=q2["source_change"],
                requests=requests, calls=calls)


def main() -> None:
    rows = json.loads(NO_EDIT.read_text(encoding="utf-8"))
    no_edit = {(r["task"], r["group"], r["repeat"]): r for r in rows}
    out = [run_row(*k, no_edit) for k in sorted(no_edit)]
    OUT.mkdir(exist_ok=True)
    (OUT / "runs.json").write_text(json.dumps(out, ensure_ascii=False) + "\n", encoding="utf-8")
    print(len(out), "runs")


if __name__ == "__main__":
    main()
