"""Read-only access to run journals for the re-read analysis. No model calls, no Docker.

A run is a directory with either events.jsonl (A5, A6-R, CR-2) or session/events.sqlite3 (P4). Payloads over
the inline limit are stored as session/artifacts/<sha256> and referenced by artifact_ref.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[3]
RUNS = HARNESS / "runs"
ARTIFACTS = Path("D:/crq/artifacts")
OUT_DIR = Path(__file__).resolve().parent / "out"

P4_BATCHES = (
    ("v2", ARTIFACTS / "p4-prompt-timing-scale54-v2-20261031-01"),
    ("v3", ARTIFACTS / "p4-prompt-timing-scale54-v3-recovery46-20261101-01"),
    ("v4", ARTIFACTS / "p4-prompt-timing-scale54-v4-single-row-20261101-01"),
)
P4_FIRST = ARTIFACTS / "p4-sphinx8721-20261030-01"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_events(run_dir: Path) -> list[dict]:
    jsonl = run_dir / "events.jsonl"
    if jsonl.is_file():
        with jsonl.open(encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]
    db = run_dir / "session" / "events.sqlite3"
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro&immutable=1", uri=True)
    try:
        return [json.loads(body) for (body,) in con.execute("SELECT body FROM events ORDER BY seq")]
    finally:
        con.close()


def payload(run_dir: Path, event: dict) -> dict:
    data = event["payload"]
    ref = data.get("artifact_ref") if isinstance(data, dict) else None
    if not ref:
        return data
    blob = (run_dir / "session" / "artifacts" / ref["path"]).read_bytes()
    if hashlib.sha256(blob).hexdigest() != ref["sha256"]:
        raise ValueError(f"artifact sha mismatch: {run_dir} {ref['path']}")
    return json.loads(blob)


def artifact_text(run_dir: Path, ref: dict) -> str | None:
    path = run_dir / "session" / "artifacts" / ref["path"]
    return path.read_bytes().decode("utf-8", "replace") if path.is_file() else None


def step_number(event: dict) -> int | None:
    step = event.get("step_id")
    return int(step.split("-", 1)[1]) if isinstance(step, str) and step.startswith("step-") else None


def write_json(name: str, result) -> Path:
    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / name
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
