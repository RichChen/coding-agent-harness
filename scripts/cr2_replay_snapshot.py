"""CR-2 contract 9.1: replay a run's tool calls in a fresh clean container and measure the workspace delta.

No model call. The tool calls recorded as tool.intent in the run's events.jsonl are executed in order through
the same agent tools, sandbox and Docker backend that run_task builds (b0-v2, same image, same timeouts).
After every call the workspace is compared with /opt/repo using the export rules of the candidate snapshot
(counts and sizes only, nothing is exported); at the end the real export_delta() is attempted once.

    cr2_replay_snapshot.py [--run runs/cr2-main/<task>/<group>/r<k>] [--out runs/cr2-logs/replay-....json]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_cr2 import PROFILES, _display_path, _events, pool_tasks  # noqa: E402
from scripts.run_a5_main import materialize_runner_task  # noqa: E402
from scripts.run_swe_task import clean_backend, prerun_check  # noqa: E402
from src.harness.factory import build_agent, load_config  # noqa: E402

DEFAULT_RUN = ROOT / "runs" / "cr2-main" / "scikit-learn__scikit-learn-12585" / "n" / "r4"

# Same file selection as EXPORT_DELTA in src/execution/baseline_image_backend.py, but only counts and sizes.
MEASURE = r'''
import json, pathlib
root = pathlib.Path("/opt/repo")
work = pathlib.Path("/workspace")
skip_dirs = {"__pycache__", ".pytest_cache", "build"}
skip_names = {".coverage"}
skip_suffixes = (".pyc", ".pyo", ".so", ".pyd", ".o", ".dylib")

def skipped(rel):
    if any(part in skip_dirs or part.endswith(".egg-info") for part in rel.parts):
        return True
    return rel.name.endswith(skip_suffixes) or rel.name in skip_names or rel.name.endswith(".egg-info")

changed, seen, skipped_count = {}, set(), 0
for path in work.rglob("*"):
    if not path.is_file() and not path.is_symlink():
        continue
    rel = path.relative_to(work)
    if path.is_symlink() or skipped(rel):
        skipped_count += skipped(rel) or path.suffix in skip_suffixes
        continue
    key = rel.as_posix()
    seen.add(key)
    data = path.read_bytes()
    original = root / rel
    if original.is_file() and not original.is_symlink() and original.read_bytes() == data:
        continue
    changed[key] = [len(data), original.is_file()]
deleted = []
for path in root.rglob("*"):
    if path.is_symlink() or not path.is_file():
        continue
    rel = path.relative_to(root)
    if skipped(rel):
        continue
    if rel.as_posix() not in seen:
        deleted.append(rel.as_posix())
print(json.dumps({"changed": changed, "deleted": sorted(deleted), "skipped": skipped_count}))
'''


def tool_calls(run_dir: Path) -> list[dict]:
    calls, step = [], 0
    for event in _events(run_dir):
        kind = event.get("event_type")
        if kind == "model.started":
            step += 1
        elif kind == "tool.intent":
            payload = event.get("payload") or {}
            calls.append({"step": step, "name": payload.get("name"), "arguments": payload.get("arguments") or {}})
    return calls


def recorded_image(run_dir: Path) -> str | None:
    for event in _events(run_dir):
        if event.get("event_type") == "run.started":
            return ((event.get("payload") or {}).get("backend") or {}).get("image")
    return None


def by_directory(changed: dict, depth: int = 2, top: int = 20) -> list[dict]:
    groups: dict[str, list[int]] = {}
    for path, (size, _existed) in changed.items():
        key = "/".join(path.split("/")[:depth]) if "/" in path else "."
        entry = groups.setdefault(key, [0, 0])
        entry[0] += 1
        entry[1] += size
    rows = [{"directory": key, "files": n, "bytes": size} for key, (n, size) in groups.items()]
    return sorted(rows, key=lambda row: (-row["bytes"], -row["files"], row["directory"]))[:top]


def summarize(state: dict) -> dict:
    changed = state["changed"]
    return {"changed_files": len(changed), "new_files": sum(1 for _, existed in changed.values() if not existed),
            "deleted_files": len(state["deleted"]), "bytes": sum(size for size, _ in changed.values()),
            "base64_bytes_estimate": sum((size + 2) // 3 * 4 for size, _ in changed.values()),
            "skipped_files": state["skipped"]}


async def replay(run_dir: Path, task: dict, profile: Path) -> dict:
    from src.tools.base import ToolContext

    _spec, image, _clean, failure = prerun_check(task, mock=False)
    if failure:
        raise SystemExit("prerun check failed: " + failure)
    expected = recorded_image(run_dir)
    if expected != image:
        raise SystemExit("image differs from the run: " + str(expected) + " vs " + str(image))
    calls = tool_calls(run_dir)
    backend = clean_backend(image)
    config = load_config(profile)
    config["llm"]["api_key"] = config["llm"]["api_key"] or "replay"
    steps, export = [], None
    with tempfile.TemporaryDirectory() as tmp:
        agent = build_agent(config, Path(tmp), execution_backend=backend)

        async def measure() -> dict:
            raw = await backend._ok("exec", backend.name, backend.python_bin, "-I", "-S", "-c", MEASURE,
                                    limit=64 * 1024 * 1024, timeout=600, op="replay measure")
            return json.loads(raw)

        try:
            await backend.start({})
            before = await measure()
            baseline = summarize(before)
            for index, call in enumerate(calls):
                ctx = ToolContext(workspace_root=Path(tmp), sandbox=agent.sandbox,
                                  file_size_limit_bytes=agent.file_size_limit_bytes,
                                  tool_output_max_chars=agent.tool_output_max_chars,
                                  read_file_max_lines=agent.read_file_max_lines, call_id=f"replay-{index}")
                started = time.monotonic()
                result = await agent.tools.get(call["name"]).execute(dict(call["arguments"]), ctx)
                elapsed = round(time.monotonic() - started, 1)
                after = await measure()
                added = sorted(set(after["changed"]) - set(before["changed"]))
                steps.append({
                    "index": index, "step": call["step"], "tool": call["name"],
                    "arguments": {k: (v[:200] if isinstance(v, str) else v) for k, v in call["arguments"].items()},
                    "success": result.success, "error_kind": result.error_kind, "elapsed_s": elapsed,
                    "output_head": (result.output or "")[:300],
                    "delta_after": summarize(after),
                    "files_added_by_this_call": len(added),
                    "bytes_added_by_this_call": sum(after["changed"][p][0] for p in added),
                    "directories_added_by_this_call": by_directory({p: after["changed"][p] for p in added}, top=10),
                })
                print(index, call["step"], call["name"], result.error_kind, elapsed, summarize(after), flush=True)
                before = after
            final = before
            try:
                delta = await backend.export_delta()
                export = {"ok": True, "changed": len(delta.get("changed") or {}), "deleted": len(delta.get("deleted") or [])}
            except Exception as exc:  # noqa: BLE001 - the export error is the result being checked
                export = {"ok": False, "error": type(exc).__name__ + ": " + str(exc)[:300]}
        finally:
            await backend.close()
    largest = sorted(final["changed"].items(), key=lambda item: -item[1][0])[:20]
    return {
        "run": _display_path(run_dir), "task": task["id"], "image": image, "profile": _display_path(profile),
        "tool_calls": len(calls), "baseline_after_start": baseline, "steps": steps,
        "final": summarize(final), "final_by_directory": by_directory(final["changed"]),
        "final_largest_files": [{"path": p, "bytes": size, "existed_in_repo": existed} for p, (size, existed) in largest],
        "final_deleted_sample": final["deleted"][:20],
        "export_delta": export, "cleanup_ok": backend.cleanup_ok,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    run_dir = args.run.resolve()
    task_id, group = run_dir.parts[-3], run_dir.parts[-2]
    task = next((t for t in pool_tasks() if t["id"] == task_id), None)
    if task is None:
        raise SystemExit("not a CR-2 task: " + task_id)
    report = asyncio.run(replay(run_dir, materialize_runner_task(task), Path(PROFILES[group])))
    out = args.out or ROOT / "runs" / "cr2-logs" / ("replay-" + task_id + "-" + group + "-" + run_dir.name + ".json")
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("final", report["final"], "export", report["export_delta"], _display_path(out))


if __name__ == "__main__":
    main()
