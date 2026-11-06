"""Shared readers for the 2026-11 errata scripts. Read-only: no model calls, no Docker."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[3]
AGENT = HARNESS.parent / "coding-agent"
OUT_DIR = Path(__file__).resolve().parent


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_events(run_dir: Path) -> list[dict]:
    with (run_dir / "events.jsonl").open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def payload(run_dir: Path, event: dict) -> dict:
    """Event payload; payloads over the inline limit live in session/artifacts/<sha256>."""
    data = event["payload"]
    ref = data.get("artifact_ref") if isinstance(data, dict) else None
    if not ref:
        return data
    blob = (run_dir / "session" / "artifacts" / ref["path"]).read_bytes()
    if hashlib.sha256(blob).hexdigest() != ref["sha256"]:
        raise ValueError(f"artifact sha mismatch: {run_dir} {ref['path']}")
    return json.loads(blob)


def write_output(name: str, result: dict) -> Path:
    path = OUT_DIR / name
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
