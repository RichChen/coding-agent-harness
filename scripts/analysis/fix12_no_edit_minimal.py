"""Historical audit, superseded by docs/analysis/fix12/FIX-12-NO-EDIT-GOLD.zh-CN.md.

Small, read-only candidate audit for FIX-12's ten high-confidence tasks.

This is deliberately weaker than the P4 classifier. It reports a candidate
"found, then idle" signal only when a reference function was read and no
workspace edit tool was seen later in the same run. It does not decide that
the model understood the fix, and it does not inspect gold patches.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "runs" / "unleash-pilot"
OUT = ROOT / "docs" / "analysis" / "fix12" / "fix12-no-edit-minimal.json"

# At most two reference functions per task. These are the high-confidence
# candidates already listed in FIX-12-REFERENCE-MAP; they are not gold answers.
REFERENCES = {
    "agronholm__anyio-935": [("src/anyio/lowlevel.py", ["_current_vars", "get", "set", "reset"])],
    "apache__iceberg-python-3295": [("pyiceberg/expressions/__init__.py", ["__new__"])],
    "conan-io__conan-19604": [("conan/internal/model/cpp_info.py", ["serialize"])],
    "dask__fastparquet-961": [("fastparquet/writer.py", ["convert"])],
    "jsonpickle__jsonpickle-587": [("jsonpickle/pickler.py", ["_flatten_obj_attrs", "_flatten_newstyle_with_slots"])],
    "openai__openai-agents-python-2144": [
        ("src/agents/models/chatcmpl_stream_handler.py", ["handle_stream"]),
        ("src/agents/models/openai_chatcompletions.py", ["stream_response"]),
    ],
    "openatx__adbutils-188": [("adbutils/_adb.py", ["reverse_list"])],
    "optuna__optuna-6428": [
        ("optuna/samplers/_partial_fixed.py", ["sample_relative", "sample_independent"]),
        ("optuna/trial/_trial.py", ["suggest_float"]),
    ],
    "scrapy__scrapy-7351_interface": [("scrapy/pqueues.py", ["pop", "_active_downloads"])],
    "stan-dev__cmdstanpy-801": [
        ("cmdstanpy/utils/stancsv.py", ["csv_bytes_list_to_numpy"]),
        ("cmdstanpy/stanfit/mcmc.py", ["_assemble_draws"]),
    ],
}
EDIT_TOOLS = {"search_replace", "write_file", "edit_file", "apply_patch", "insert_text", "replace_text"}
EDIT_HINT = re.compile(r"(?:\bopen\([^\n]+,['\"]w|write_text\(|sed\s+-i|perl\s+-pi|cat\s+>\s*[^/]*workspace|apply_patch)", re.I)


def load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def output_text(payload: dict) -> str:
    value = payload.get("output")
    if isinstance(value, str):
        return value
    return str(payload.get("_run_output") or "")


def is_edit_intent(name: str | None, args: dict) -> bool:
    if name in EDIT_TOOLS:
        return True
    if name == "run_command":
        command = str(args.get("command") or "")
        return "/workspace" in command and bool(EDIT_HINT.search(command))
    return False


def reference_hashes() -> dict[str, dict[str, str]]:
    result = {}
    for task, refs in REFERENCES.items():
        result[task] = {}
        for ref_path, _ in refs:
            path = ROOT / "runs" / "rebench" / "work" / task / "staging" / "baseline" / ref_path
            try:
                result[task][ref_path] = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                result[task][ref_path] = "missing"
    return result


def run_row(path: Path) -> dict:
    task = path.parts[-3]
    variant = (load(path / "summary.json") or {}).get("variant", "").replace("UNLEASH-", "")
    refs = REFERENCES.get(task, [])
    events = []
    for line in (path / "events.jsonl").read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    request = 0
    located = None
    edits = []
    for event in events:
        kind = event.get("event_type")
        if kind == "model.started":
            request += 1
        if kind != "tool.intent":
            continue
        payload = event.get("payload") or {}
        name = payload.get("name")
        args = payload.get("arguments") or {}
        if is_edit_intent(name, args):
            edits.append(request)
        if name != "read_file":
            continue
        path_arg = str(args.get("path") or "")
        if not any(path_arg == ref_path for ref_path, _ in refs):
            continue
        # The following tool.result is paired by the event sequence in the
        # run log. This is sufficient for this descriptive audit.
        seq = event.get("seq")
        result = next((e for e in events if e.get("event_type") == "tool.result" and e.get("seq") == seq + 1), None)
        text = output_text((result or {}).get("payload") or {})
        functions = [fn for ref_path, fns in refs if ref_path == path_arg for fn in fns]
        if any(re.search(r"def\s+" + re.escape(fn) + r"\s*\(", text) for fn in functions):
            located = located or request
    after = [n for n in edits if located is not None and n > located]
    candidate = located is not None and not after
    return {
        "task": task,
        "variant": variant,
        "run": path.name,
        "located_reference_request": located,
        "edit_requests_after_reference": after,
        "edit_seen": bool(edits),
        "candidate_found_but_idle": candidate,
    }


def main() -> None:
    rows = []
    for path in sorted(RUNS.glob("*/*/r[0-9]*/summary.json")):
        if not re.fullmatch(r"r\d+", path.parent.name):
            continue
        task = path.parts[-4]
        if task in REFERENCES:
            rows.append(run_row(path.parent))
    counts = defaultdict(Counter)
    for row in rows:
        counts[row["variant"]]["runs"] += 1
        counts[row["variant"]]["located"] += int(row["located_reference_request"] is not None)
        counts[row["variant"]]["edit_seen"] += int(row["edit_seen"])
        counts[row["variant"]]["candidate_found_but_idle"] += int(row["candidate_found_but_idle"])
    result = {
        "schema": "fix12.no-edit-minimal.candidate.v1",
        "scope": "10 high-confidence task candidates only",
        "warning": "Candidate found-but-idle signals are descriptive and are not a P4-equivalent final classification.",
        "references": REFERENCES,
        "reference_hashes": reference_hashes(),
        "rows": rows,
        "by_variant": {k: dict(v) for k, v in sorted(counts.items())},
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["by_variant"], ensure_ascii=False))


if __name__ == "__main__":
    main()
