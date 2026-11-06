"""Collect one row per model request from the A5, A6-R and CR-2 event logs. Read-only: no model calls, no Docker.

A row pairs the view in `context.prepared` with the provider `prompt_tokens` of the first successful
`model.settled` after it. A `context.rejected` row has no settled call; its view is rebuilt from the raw
messages in the preceding `state.saved` with the same masking pass, and checked against the recorded
`serialized_bytes`. Writes requests.jsonl (not committed; summarize_admission.py reads it).
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
sys.path.insert(0, str(HERE.parent / "errata_2026_11"))
from _common import HARNESS, payload  # noqa: E402
from src.runtime.compact import micro_compact_openai_messages_with_stats  # noqa: E402
from src.runtime.window import encoded  # noqa: E402

EXPERIMENTS = {"a5": "runs/a5-main", "a6r": "runs/a6-retry", "cr2": "runs/cr2-main"}
WANTED = ('"context.prepared"', '"context.rejected"', '"model.settled"', '"state.saved"')


def split_bytes(messages, tools_extra):
    data = encoded({"messages": messages, "tools": []})
    ascii_bytes = sum(1 for b in data if b < 0x80)
    return ascii_bytes + tools_extra, len(data) - ascii_bytes, len(data) + tools_extra


def tool_count(stats, n_messages):
    rest = stats["admission_input_tokens"] - stats["serialized_bytes"] - 512 - 32 * n_messages
    return rest // 128


def rebuild_view(messages, stats):
    mask = stats.get("masking")
    if stats.get("strategy") in ("masking", "mask-then-summary") and mask:
        view, _ = micro_compact_openai_messages_with_stats(copy.deepcopy(messages), keep_recent=mask["keep_recent"],
                                                           preserve_tools=set())
        return view
    return messages


def run_rows(run_dir: Path, experiment: str):
    model = None
    names = run_dir / "model-names.json"
    if names.exists():
        listed = json.loads(names.read_text(encoding="utf-8"))
        listed = listed.get("models") if isinstance(listed, dict) else listed
        model = ",".join(str(m) for m in listed or []) or None
    rows, pending, last_state, tools_extra = [], None, None, None
    with (run_dir / "events.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            if not any(w in line[:400] for w in WANTED):
                continue
            event = json.loads(line)
            kind = event["event_type"]
            if kind == "state.saved":
                last_state = event
                continue
            data = payload(run_dir, event)
            base = dict(experiment=experiment, run=run_dir.relative_to(HARNESS).as_posix(), model=model)
            if kind == "context.prepared":
                view, stats = data["view"], data["stats"]
                messages_only = len(encoded({"messages": view, "tools": []}))
                tools_extra = stats["serialized_bytes"] - messages_only
                ascii_bytes, non_ascii, total = split_bytes(view, tools_extra)
                pending = dict(base, kind="prepared", step=data.get("step"), n_messages=len(view),
                               n_tools=tool_count(stats, len(view)), ascii_bytes=ascii_bytes,
                               non_ascii_bytes=non_ascii, serialized_bytes=stats["serialized_bytes"],
                               admission_input_tokens=stats["admission_input_tokens"],
                               reserved_output_tokens=stats.get("reserved_output_tokens"),
                               max_tokens=stats.get("max_tokens"))
            elif kind == "model.settled" and pending is not None:
                record = data.get("record") or {}
                usage = record.get("usage") or {}
                if record.get("success") and usage.get("prompt_tokens"):
                    rows.append(dict(pending, prompt_tokens=usage["prompt_tokens"]))
                    pending = None
            elif kind == "context.rejected":
                stats = data
                messages = payload(run_dir, last_state)["messages"]
                view = rebuild_view(messages, stats)
                extra = tools_extra if tools_extra is not None else 0
                ascii_bytes, non_ascii, total = split_bytes(view, extra)
                rows.append(dict(base, kind="rejected", step=stats.get("step"), n_messages=len(view),
                                 n_tools=tool_count(stats, len(view)), ascii_bytes=ascii_bytes,
                                 non_ascii_bytes=non_ascii, serialized_bytes=stats["serialized_bytes"],
                                 rebuilt_matches=total == stats["serialized_bytes"],
                                 admission_input_tokens=stats["admission_input_tokens"],
                                 reserved_output_tokens=stats.get("reserved_output_tokens"),
                                 max_tokens=stats.get("max_tokens"), error_kind=stats.get("error_kind"),
                                 previous_prompt_tokens=rows[-1]["prompt_tokens"] if rows and rows[-1]["run"] == base["run"] else None))
    return rows


def _job(args):
    run_dir, experiment = args
    return run_rows(Path(run_dir), experiment)


def run_dirs(root: Path) -> list[Path]:
    found = []
    for current, dirs, files in os.walk(root):
        if "events.jsonl" in files:
            found.append(Path(current))
            dirs.clear()
        else:
            dirs[:] = [d for d in dirs if d not in ("workspace", "session")]
    return sorted(found)


def main(out: Path, workers: int = 6) -> None:
    from concurrent.futures import ProcessPoolExecutor
    jobs = [(str(run_dir), experiment) for experiment, root in EXPERIMENTS.items()
            for run_dir in run_dirs(HARNESS / root)]
    count = 0
    with out.open("w", encoding="utf-8") as fh, ProcessPoolExecutor(workers) as pool:
        for rows in pool.map(_job, jobs, chunksize=4):
            for row in rows:
                fh.write(json.dumps(row) + "\n")
                count += 1
    print("runs", len(jobs), "rows", count, "->", out)


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "requests.jsonl")
