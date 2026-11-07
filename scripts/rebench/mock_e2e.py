"""Zero-model end-to-end check of prepared SWE-rebench tasks through run_swe_task.run_task.

Two mock agents per task, both inside the clean image and graded by ImageGrader with the task's options:

    empty  finishes at once without edits; the grader must report the task unsolved
    gold   applies the gold patch through the agent's own file tools (search_replace per hunk, write_file for
           new files, run_command for deletions), then finishes; the grader must report the task solved

The gold patch is read by this host script from the staging tree, never shown to a model.

    mock_e2e.py [--manifest configs/rebench/tasks.yaml] [--profile configs/sprint/b0.yaml] [ids...]
"""
from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.run_swe_task import _MockLLM, run_task  # noqa: E402
from src.llm.client import LLMResponse, ToolCall  # noqa: E402

OUT = ROOT / "runs" / "rebench" / "mock-e2e"


def gold_edits(original: str, new: str) -> list[tuple[str, str]]:
    """search/replace pairs that turn original into new, applied in order; each search is unique when applied."""
    before, after = original.splitlines(keepends=True), new.splitlines(keepends=True)
    current = list(before)
    edits, offset = [], 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=before, b=after, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        start, end = i1 + offset, i2 + offset
        text = "".join(current)
        up = down = 0
        while True:
            search = "".join(current[start - up:end + down])
            if search and text.count(search) == 1:
                break
            if start - up == 0 and end + down >= len(current):
                raise ValueError("no unique context for an edit")
            up += start - up > 0
            down += end + down < len(current)
        replace = "".join(current[start - up:start] + after[j1:j2] + current[end:end + down])
        edits.append((search, replace))
        current[start - up:end + down] = current[start - up:start] + after[j1:j2] + current[end:end + down]
        offset += (j2 - j1) - (i2 - i1)
    return edits


def gold_tool_calls(task: dict) -> list[ToolCall]:
    receipt = json.loads((ROOT / task["receipt"]).read_text(encoding="utf-8"))
    staging = ROOT / task["original"]
    known_good = staging.parent / "known-good"
    calls = []
    for index, relative in enumerate(receipt["private_source_binding"]["gold_patch_paths"]):
        old, new = staging / relative, known_good / relative
        if not new.exists():
            args = ("run_command", {"command": "rm -f -- " + relative})
            calls.append(ToolCall(id=f"gold-{index}", name=args[0], arguments=args[1]))
            continue
        new_text = new.read_text(encoding="utf-8")
        if not old.exists():
            calls.append(ToolCall(id=f"gold-{index}", name="write_file", arguments={"path": relative, "content": new_text}))
            continue
        for n, (search, replace) in enumerate(gold_edits(old.read_text(encoding="utf-8"), new_text)):
            calls.append(ToolCall(id=f"gold-{index}-{n}", name="search_replace",
                                  arguments={"path": relative, "search": search, "replace": replace}))
    for call in calls:
        call.raw_arguments = json.dumps(call.arguments)
    return calls


class GoldLLM(_MockLLM):
    """First turn: every gold edit as one batch of tool calls. Second turn: finish."""

    def __init__(self, calls: list[ToolCall]):
        super().__init__()
        self.calls = calls
        self.turns = 0

    async def chat(self, messages, tools=None):
        self.turns += 1
        if self.turns == 1:
            return LLMResponse(text=None, tool_calls=list(self.calls), finish_reason="tool_calls",
                               usage={"prompt_tokens": 0, "completion_tokens": 0, "provenance": "unknown"})
        return await super().chat(messages, tools)


def brief(summary: dict) -> dict:
    keys = ("status", "solved", "grader_status", "grader_ids", "model_calls", "tool_calls", "harness_tool_errors",
            "changed_files", "cost_cny", "stop_reason", "run_error", "infra_reason", "answer_isolation_pass",
            "clean_image_digest", "grader_candidate_error")
    return {key: summary.get(key) for key in keys}


async def run_all(manifest: Path, profile: Path, ids: list[str]) -> dict:
    tasks = yaml.safe_load(manifest.read_text(encoding="utf-8"))["tasks"]
    if ids:
        tasks = [task for task in tasks if task["id"] in set(ids)]
    results = {}
    for task in tasks:
        results[task["id"]] = {}
        for variant in ("empty", "gold"):
            output = OUT / task["id"] / variant
            if output.exists():
                shutil.rmtree(output)
            llm = GoldLLM(gold_tool_calls(task)) if variant == "gold" else None
            summary = await run_task(task, profile, output, mock=True, mock_llm=llm)
            results[task["id"]][variant] = brief(summary)
            print(task["id"], variant, summary.get("status"), summary.get("solved"), summary.get("grader_ids"), flush=True)
    passed = all(r["empty"]["solved"] is False and r["empty"]["status"] in ("fail", "partial")
                 and r["gold"]["solved"] is True for r in results.values())
    return {"tasks": len(results), "all_expected": passed, "results": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, default=ROOT / "configs" / "rebench" / "tasks.yaml")
    parser.add_argument("--profile", type=Path, default=ROOT / "configs" / "sprint" / "b0.yaml")
    parser.add_argument("--out", type=Path, default=OUT / "results.json")
    parser.add_argument("ids", nargs="*")
    args = parser.parse_args()
    result = asyncio.run(run_all(args.manifest, args.profile, args.ids))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"tasks": result["tasks"], "all_expected": result["all_expected"]}))


if __name__ == "__main__":
    main()
