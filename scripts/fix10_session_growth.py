"""Wall-clock and storage cost of RunSession bookkeeping alone as a run gets longer. No model, no Docker.

Each simulated step appends what a real step appends: model.started/settled, one tool intent/result pair, a
context event and two state.saved snapshots whose message history grows by --step-bytes (CR-2 p90: ~6.8 KB).
Prints cumulative seconds spent in session.append and the artifact bytes, every --every steps.

    fix10_session_growth.py --steps 200 --every 20 --out runs/fix10/e6-session-growth.json
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.runtime.session import RunSession  # noqa: E402
from tests.fixtures.recovery_worker import expected  # noqa: E402

MiB = 1024 * 1024


def simulate(steps: int, every: int, step_bytes: int, artifact_bytes: int) -> list[dict]:
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "workspace"
        workspace.mkdir()
        with RunSession.create(Path(tmp) / "run", task_id="growth", expected=expected(workspace),
                               timeout_seconds=86400, artifact_limit_bytes=artifact_bytes,
                               artifact_item_limit_bytes=min(artifact_bytes, 64 * MiB)) as session:
            messages = [{"role": "system", "content": "s" * 4000}, {"role": "user", "content": "u" * 4000}]
            spent = 0.0
            for step in range(1, steps + 1):
                messages.append({"role": "assistant", "content": None,
                                 "tool_calls": [{"id": f"c{step}", "type": "function",
                                                 "function": {"name": "read_file", "arguments": "{}"}}]})
                messages.append({"role": "tool", "tool_call_id": f"c{step}", "content": "x" * (step_bytes - 200)})
                started = time.perf_counter()
                session.append("plugin.state", {"name": "model.started", "state": {"step": step}})
                session.append("plugin.state", {"name": "model.settled", "state": {"step": step, "usage": {"p": 1}}})
                session.append("plugin.state", {"name": "context", "state": {"step": step}})
                session.append("plugin.state", {"name": "tool.intent", "state": {"step": step}})
                session.append("plugin.state", {"name": "tool.result", "state": {"step": step}})
                session.save_loop({"messages": messages, "step_number": step})
                session.save_loop({"messages": messages, "step_number": step, "response": None})
                spent += time.perf_counter() - started
                if step % every == 0 or step == steps:
                    rows.append({"steps": step, "append_seconds_total": round(spent, 1),
                                 "artifact_mib": round(session.artifacts.used_bytes() / MiB, 1),
                                 "last_state_mib": round(len(json.dumps(messages)) / MiB, 2)})
                    print(json.dumps(rows[-1]), flush=True)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--steps", type=int, default=200)
    parser.add_argument("--every", type=int, default=20)
    parser.add_argument("--step-bytes", type=int, default=6816)
    parser.add_argument("--artifact-bytes", type=int, default=2048 * MiB)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    rows = simulate(args.steps, args.every, args.step_bytes, args.artifact_bytes)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({"step_bytes": args.step_bytes, "rows": rows}, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
