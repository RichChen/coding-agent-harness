"""How much of each per-run hard limit finished runs used, and how the stored state grows per step. No model calls.

Reads events.jsonl and session/artifacts of each run directory (taken from a cny_reconcile output or given directly):

    event_bytes        canonical event bodies, the quantity EventStore caps at 16 MiB
    artifact_bytes     content-addressed artifact files, capped at 64 MiB by RunSession.create
    largest_artifact   the biggest single artifact, capped at 2 MiB per item
    last_state_bytes   size of the final state.saved payload (full raw message history)
    run_output_kept    RunOutputMeter bytes_kept, capped at 16 MiB
    elapsed_s, longest_step_s, longest_model_s, longest_tool_s

The linear fit of state.saved size against step number gives the growth per step that the E6 projection uses.

    fix10_limit_usage.py --reconcile runs/fix10/e3-reconcile-cr2-main.json --top 40 --out runs/fix10/e6-usage.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MiB = 1024 * 1024


def payload_bytes(event: dict) -> int:
    payload = event["payload"]
    if set(payload) == {"artifact_ref"}:
        return int(payload["artifact_ref"]["size_bytes"])
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())


def run_usage(run_dir: Path) -> dict:
    lines = (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines]
    artifacts = [p for p in (run_dir / "session" / "artifacts").glob("*") if p.is_file()]
    saved = [e for e in events if e["event_type"] == "state.saved"]
    started = [e["elapsed_ms"] for e in events if e["event_type"] == "model.started"]
    starts = started + [events[-1]["elapsed_ms"]]
    model_ms = [((e["payload"].get("record") or {}).get("elapsed_ms") or 0) for e in events
                if e["event_type"] == "model.settled" and "record" in e["payload"]]
    intents = {e["operation_id"]: e["elapsed_ms"] for e in events if e["event_type"] == "tool.intent"}
    tool_ms = [e["elapsed_ms"] - intents[e["operation_id"]] for e in events
               if e["event_type"] == "tool.result" and e["operation_id"] in intents]
    sizes = [payload_bytes(e) for e in saved]
    last_inline = next((e["payload"] for e in reversed(saved) if "artifact_ref" not in e["payload"]), None)
    kept = None
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    if last_inline is not None:
        kept = (last_inline.get("run_output") or {}).get("bytes_kept")
    else:
        for event in reversed(saved):
            path = run_dir / "session" / "artifacts" / event["payload"]["artifact_ref"]["sha256"]
            if path.is_file():
                kept = (json.loads(path.read_bytes()).get("run_output") or {}).get("bytes_kept")
                break
    return {
        "run_dir": run_dir.relative_to(ROOT).as_posix() if run_dir.is_relative_to(ROOT) else str(run_dir),
        "model_calls": summary.get("model_calls"), "stop_reason": summary.get("stop_reason"),
        "event_bytes": sum(len(line.encode()) for line in lines),
        "artifact_bytes": sum(p.stat().st_size for p in artifacts),
        "largest_artifact": max((p.stat().st_size for p in artifacts), default=0),
        "state_saved_events": len(saved), "last_state_bytes": sizes[-1] if sizes else 0,
        "state_bytes_per_step": slope(list(range(len(sizes))), sizes) * len(sizes) / max(1, len(started)),
        "run_output_kept": kept,
        "elapsed_s": round(events[-1]["elapsed_ms"] / 1000, 1),
        "longest_step_s": round(max((b - a for a, b in zip(starts, starts[1:])), default=0) / 1000, 1),
        "longest_model_s": round(max(model_ms, default=0) / 1000, 1),
        "longest_tool_s": round(max(tool_ms, default=0) / 1000, 1),
    }


def slope(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    den = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else 0.0


def project(base: float, per_step: float, saves_per_step: float, steps: int) -> dict:
    """Stored bytes for a run of `steps` model calls: largest snapshot, and the sum of all snapshots."""
    last = base + per_step * steps
    total = saves_per_step * (base * steps + per_step * steps * steps / 2)
    return {"steps": steps, "last_state_mib": round(last / MiB, 2), "state_artifacts_mib": round(total / MiB, 1)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reconcile", type=Path)
    parser.add_argument("--top", type=int, default=40)
    parser.add_argument("--out", type=Path)
    parser.add_argument("runs", nargs="*", type=Path)
    args = parser.parse_args()
    dirs = [ROOT / p if not p.is_absolute() else p for p in args.runs]
    if args.reconcile:
        rows = json.loads(args.reconcile.read_text(encoding="utf-8"))["runs"]
        rows.sort(key=lambda r: -r["calls"]["settled"])
        dirs += [ROOT / r["run_dir"] for r in rows[:args.top]]
    usage = [run_usage(d) for d in dirs if (d / "events.jsonl").is_file()]
    keys = ("event_bytes", "artifact_bytes", "largest_artifact", "last_state_bytes", "run_output_kept",
            "elapsed_s", "longest_step_s", "longest_model_s", "longest_tool_s", "state_bytes_per_step")
    worst = {k: max((u[k] or 0) for u in usage) for k in keys}
    per_step = sorted(u["state_bytes_per_step"] for u in usage)
    median = per_step[len(per_step) // 2] if per_step else 0
    p90 = per_step[int(len(per_step) * 0.9)] if per_step else 0
    saves = sum(u["state_saved_events"] for u in usage) / max(1, sum(u["model_calls"] or 0 for u in usage))
    result = {"runs": len(usage), "worst": worst, "state_bytes_per_step": {"median": round(median), "p90": round(p90)},
              "state_saved_per_model_call": round(saves, 2),
              "projection_p90": [project(8192, p90, saves, n) for n in (40, 100, 150, 200, 300)],
              "usage": usage}
    text = json.dumps(result, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("runs", "worst", "state_bytes_per_step", "state_saved_per_model_call",
                                             "projection_p90")}, indent=2))


if __name__ == "__main__":
    main()
