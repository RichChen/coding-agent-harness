"""FIX-15 design/preflight entry point.

This script currently prepares and binds the three-arm plan without model or
Docker calls.  The formal 180-slot launcher is intentionally not enabled until
the design and all preflight evidence are reviewed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.rebench import unleash_config_diff as config_diff  # noqa: E402
from scripts.rebench import pilot_ingest  # noqa: E402
from src.harness.factory import load_config  # noqa: E402

GROUPS = ("U-new", "N", "E-new")
PROFILES = {
    "U-new": ROOT / "configs/rebench/fix15-u-new.yaml",
    "N": ROOT / "configs/rebench/fix15-n.yaml",
    "E-new": ROOT / "configs/rebench/fix15-e-new.yaml",
}
VARIANTS = {"U-new": "FIX15-U-NEW", "N": "FIX15-N", "E-new": "FIX15-E-NEW"}
PLAN = ROOT / "scripts/rebench/out/fix15_tasks.json"
INGEST = ROOT / "scripts/rebench/out/fix13_ingest.json"
MANIFEST = ROOT / "configs/rebench/fix13-tasks.yaml"
PREFLIGHT = ROOT / "runs/fix15-preflight"
BINDING = PREFLIGHT / "input-binding.json"
EXPECTED_RUNS = 180


def flat(value, prefix=""):
    result = {}
    for key, item in value.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(item, dict):
            result.update(flat(item, name))
        else:
            result[name] = item
    return result


def config_report() -> dict:
    configs = {group: load_config(path) for group, path in PROFILES.items()}
    flattened = {group: flat(config) for group, config in configs.items()}
    baseline = flattened["U-new"]
    baseline.setdefault("runtime.tool_intervention", "none")
    for values in flattened.values():
        values.setdefault("runtime.tool_intervention", "none")
    differences = {}
    for group in GROUPS:
        differences[group] = {
            key: [baseline.get(key), flattened[group].get(key)]
            for key in sorted(set(baseline) | set(flattened[group]))
            if baseline.get(key) != flattened[group].get(key)
        }
    expected = {
        "U-new": {},
        "N": {"runtime.tool_intervention": ["none", "next_action"]},
        "E-new": {"runtime.tool_intervention": ["none", "result_explanation"]},
    }
    return {"ok": all(differences[group] == expected[group] for group in GROUPS),
            "differences": differences,
            "models": sorted({flattened[group]["llm.model"] for group in GROUPS}),
            "thinking": sorted({flattened[group]["llm.thinking"] for group in GROUPS}),
            "max_tokens": sorted({flattened[group]["llm.max_tokens"] for group in GROUPS})}


def load_slots() -> tuple[list[dict], dict]:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    ingest = json.loads(INGEST.read_text(encoding="utf-8"))
    base_sha = hashlib.sha256((ROOT / plan["source_plan"]).read_bytes()).hexdigest()
    if plan.get("source_plan_sha256") != base_sha:
        raise ValueError("FIX-15 source plan hash changed")
    order = sorted(plan.get("run_order", []), key=lambda row: row["seq"])
    if len(order) != EXPECTED_RUNS or [row["seq"] for row in order] != list(range(1, EXPECTED_RUNS + 1)):
        raise ValueError("FIX-15 requires 180 unique sequential slots")
    if [row["block"] for row in order] != [block for block in range(1, 31) for _ in range(6)]:
        raise ValueError("FIX-15 task blocks must be contiguous")
    expected = {(group, repeat) for group in GROUPS for repeat in (1, 2)}
    for block in range(1, 31):
        rows = [row for row in order if row["block"] == block]
        if len(rows) != 6 or {(row["group"], row["repeat"]) for row in rows} != expected:
            raise ValueError(f"FIX-15 block {block} does not contain all six assigned slots")
        if len({row["instance_id"] for row in rows}) != 1:
            raise ValueError(f"FIX-15 block {block} contains multiple tasks")
    if ingest.get("pending") or ingest.get("unfilled"):
        raise ValueError("FIX-13 ingest still has pending or unfilled positions")
    mapping = pilot_ingest.task_map(ingest)
    slots = []
    for row in order:
        task = mapping.get(row["instance_id"])
        if task is None:
            raise ValueError("FIX-15 task has no accepted ingest: " + row["instance_id"])
        slots.append({**row, "task": task})
    manifest = {row["id"]: row for row in yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))["tasks"]}
    if {row["task"] for row in slots} - manifest.keys():
        raise ValueError("accepted FIX-15 task is missing from manifest")
    return slots, manifest


def prompt_report() -> dict:
    from src.harness.factory import build_agent
    from src.runtime.tool_intervention import NEXT_ACTION, RESULT_EXPLANATION
    import tempfile

    prompts = {}
    with tempfile.TemporaryDirectory() as tmp:
        for group, profile in PROFILES.items():
            config = load_config(profile)
            config["llm"].update(base_url="http://localhost", api_key="fixture")
            config["policy"]["command"]["execution_profile"] = "legacy_host"
            config["tracing"]["output_dir"] = str(Path(tmp) / group / "trace")
            agent = build_agent(config, Path(tmp) / group)
            prompts[group] = {"intervention": agent.tool_intervention, "system_prompt": agent.system_prompt}
    return {
        "ok": prompts["U-new"]["intervention"] == "none"
        and prompts["N"]["intervention"] == "next_action"
        and prompts["E-new"]["intervention"] == "result_explanation"
        and prompts["N"]["system_prompt"].endswith(NEXT_ACTION)
        and prompts["E-new"]["system_prompt"].endswith(RESULT_EXPLANATION),
        "interventions": {group: prompts[group]["intervention"] for group in GROUPS},
        "prompt_suffixes": {group: prompts[group]["system_prompt"][-200:] for group in GROUPS},
    }


def preflight() -> dict:
    report = {"schema": "fix15.preflight.v1", "model_calls": 0,
              "groups": GROUPS, "expected_slots": EXPECTED_RUNS,
              "scope": "configuration, prompt, task slots and hashes only. No model or Docker calls."}
    try:
        report["config"] = config_report()
    except Exception as exc:
        report["config"] = {"ok": False, "error": type(exc).__name__ + ": " + str(exc)}
    try:
        slots, _ = load_slots()
        report.update(slots_ok=True, accepted_slots=len(slots), blocks=len({row["block"] for row in slots}))
    except Exception as exc:
        report.update(slots_ok=False, slots_error=type(exc).__name__ + ": " + str(exc))
    try:
        report["prompt"] = prompt_report()
    except Exception as exc:
        report["prompt"] = {"ok": False, "error": type(exc).__name__ + ": " + str(exc)}
    report["pass"] = bool(report["config"].get("ok") and report.get("slots_ok") and report["prompt"].get("ok"))
    return report


def bind_mock() -> dict:
    report = preflight()
    if not report["pass"]:
        raise SystemExit("FIX-15 local preflight failed")
    from src.runtime.session import source_fingerprint

    PREFLIGHT.mkdir(parents=True, exist_ok=True)
    paths = [PLAN, INGEST, MANIFEST, *PROFILES.values(), Path(__file__),
             ROOT / "prompts/swe_b0.md", ROOT / "scripts/run_swe_task.py",
             ROOT / "scripts/run_cr2.py", ROOT / "scripts/run_a5_main.py",
             ROOT / "src/runtime/tool_intervention.py", ROOT / "src/config/schema.py",
             ROOT / "scripts/rebench/fix15_factorial_plan.py",
             ROOT / "scripts/rebench/fix15_preflight.py",
             ROOT / "docs/roadmap/FIX-15-E-ABLATION-DESIGN.zh-CN.md"]
    e2e_report = PREFLIGHT / "e2e-report.json"
    if e2e_report.is_file():
        paths.append(e2e_report)
        e2e = json.loads(e2e_report.read_text(encoding="utf-8"))
        for case in e2e.get("cases", []):
            directory = ROOT / "runs" / "fix15-preflight" / case["evidence_dir"]
            for name, expected in case.get("evidence_sha256", {}).items():
                evidence = directory / name
                if not evidence.is_file() or hashlib.sha256(evidence.read_bytes()).hexdigest() != expected:
                    raise ValueError("FIX-15 e2e evidence is missing or changed: " + str(evidence))
                paths.append(evidence)
    identity = PREFLIGHT / "identity-smoke" / "identity.json"
    if identity.is_file():
        paths.append(identity)
        ledger = PREFLIGHT / "identity-smoke" / "cny-ledger.json"
        smoke = PREFLIGHT / "identity-smoke" / "smoke.json"
        if not ledger.is_file() or not smoke.is_file():
            raise ValueError("FIX-15 identity smoke evidence is incomplete")
        paths.extend([ledger, smoke])
    binding = {"schema": "fix15.input-binding.v1", "mock": True,
               "groups": list(GROUPS), "expected_runs": EXPECTED_RUNS,
               "sources": {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
               "source_fingerprint": source_fingerprint(ROOT)}
    BINDING.write_text(json.dumps(binding, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"preflight": report, "binding": binding}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--bind-mock", action="store_true")
    parser.add_argument("--authorized")
    args = parser.parse_args()
    if args.authorized:
        raise SystemExit("FIX-15 formal execution is intentionally disabled until design and real preflight review")
    if not PLAN.exists():
        raise SystemExit("FIX-15 plan is missing. Run scripts/rebench/fix15_factorial_plan.py first.")
    if args.bind_mock:
        print(json.dumps(bind_mock(), ensure_ascii=False, indent=2))
        return
    report = preflight()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
