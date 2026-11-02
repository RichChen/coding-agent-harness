"""CR-2 compatibility snapshot of the b0-v2 default path.

    cr2_compat.py --write   record tests/fixtures/cr2-b0v2-compat.json (run once, before any CR-2 change)
    cr2_compat.py           compare the current code against that file

The snapshot holds, for b0-v2: the sha256 and length of the first user message of every pool task
(build_prompt without a suffix), the tool schema list build_agent sends to the model (exact JSON bytes),
and runtime_fingerprint of the assembled loop. No model calls, no container starts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "tests" / "fixtures" / "cr2-b0v2-compat.json"
PROFILE = ROOT / "configs" / "sprint" / "b0-v2.yaml"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def agent_snapshot(profile: Path = PROFILE) -> dict:
    from src.harness.factory import build_agent, load_config
    from src.runtime.session import runtime_fingerprint

    config = load_config(profile)
    config["llm"]["api_key"] = "compat-snapshot"
    with tempfile.TemporaryDirectory() as tmp:
        agent = build_agent(config, Path(tmp))
        schemas = agent.tools.schemas(agent.enabled_tools)
        schemas_json = json.dumps(schemas, ensure_ascii=False)
        return {
            "tool_names": [s["function"]["name"] for s in schemas],
            "schemas_json": schemas_json,
            "schemas_sha256": _sha(schemas_json.encode("utf-8")),
            "runtime_fingerprint": runtime_fingerprint(agent),
            "system_prompt_sha256": _sha((agent.system_prompt or "").encode("utf-8")),
        }


def prompt_snapshot() -> dict:
    from scripts.run_a5_main import accepted_tasks, load_targets, materialize_runner_task
    from scripts.run_swe_task import build_prompt

    out = {}
    for task in accepted_tasks(load_targets()):
        text = build_prompt(materialize_runner_task(task))
        out[task["id"]] = {"sha256": _sha(text.encode("utf-8")), "chars": len(text)}
    return dict(sorted(out.items()))


def snapshot() -> dict:
    return {"profile": PROFILE.relative_to(ROOT).as_posix(), "agent": agent_snapshot(), "prompts": prompt_snapshot()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true", help="record the snapshot (refuses to overwrite)")
    args = parser.parse_args()
    current = snapshot()
    if args.write:
        if FIXTURE.exists():
            raise SystemExit(f"{FIXTURE} exists; the baseline is recorded once")
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {FIXTURE.relative_to(ROOT)}: {len(current['prompts'])} prompts, tools {current['agent']['tool_names']}")
        return 0
    baseline = json.loads(FIXTURE.read_text(encoding="utf-8"))
    same = current == baseline
    print(json.dumps({"identical": same, "prompts": len(current["prompts"]),
                      "schemas_sha256": current["agent"]["schemas_sha256"],
                      "runtime_fingerprint": current["agent"]["runtime_fingerprint"]}, indent=2))
    return 0 if same else 1


if __name__ == "__main__":
    raise SystemExit(main())
