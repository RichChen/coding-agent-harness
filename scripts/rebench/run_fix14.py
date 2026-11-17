"""FIX-14 2x2 launcher built on FIX-13's recovery and accounting path.

The default entry point is read-only preflight. Real execution requires the
separately reviewed ``--authorized 120,60`` flag.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.rebench import run_fix13 as base
from scripts.rebench import run_unleash as legacy
from src.harness.factory import load_config

GROUPS = ("T8", "O32")
PROFILES = {"T8": ROOT / "configs/rebench/fix14-t8.yaml",
            "O32": ROOT / "configs/rebench/fix14-o32.yaml"}
VARIANTS = {group: "FIX14-" + group for group in GROUPS}
PLAN = ROOT / "scripts/rebench/out/fix14_factorial_tasks.json"
INGEST = ROOT / "scripts/rebench/out/fix13_ingest.json"
MANIFEST = ROOT / "configs/rebench/fix13-tasks.yaml"
OUT = ROOT / "runs/fix14"
PREFLIGHT = ROOT / "runs/fix14-preflight"
READY = PREFLIGHT / "ready.json"
EXPECTED_RUNS = 120
# Raised from 60 to 80 on 2026-11-13 with the user's approval: the first six T8 slots
# included three expensive vyper runs, so the 2x conservative forecast projected 74.18 CNY
# while the same 30 tasks cost 15.80/16.87 CNY per arm in FIX-13. The extra 20 CNY is
# headroom for the forecast rule, not a licence to exceed the stopping rules.
BATCH_CAP = Decimal("80")
PARALLEL = 3
RUN_CAP = Decimal("4")
BUFFER = PARALLEL * RUN_CAP
START_THRESHOLD = BATCH_CAP - BUFFER
HISTORICAL_MEAN = {group: Decimal("0.24141805") for group in GROUPS}


def config_report() -> dict:
    configs = {group: base.config_diff.load(path) for group, path in PROFILES.items()}
    baseline = base.config_diff.load(ROOT / "configs/rebench/fix13-u.yaml")
    baseline["llm.model"] = legacy.EXPECTED_MODEL
    differences = {group: base.config_diff.compare(baseline, config) for group, config in configs.items()}
    declared = {
        "T8": {"llm.thinking": ("disabled", "enabled"),
               "llm.reasoning_effort": (None, "high")},
        "O32": {"llm.max_tokens": (8192, 32768)},
    }
    return {"ok": all(differences[group] == declared[group] for group in GROUPS),
            "differences": differences,
            "models": sorted({config["llm.model"] for config in configs.values()})}


def load_slots(plan, record, *, complete=True):
    if record.get("plan_sha256") != __import__("hashlib").sha256(
            (ROOT / "scripts/rebench/out/fix13_tasks.json").read_bytes()).hexdigest():
        raise ValueError("FIX-14 source plan hash differs from ingestion")
    mapping = legacy.task_map(record)
    order = sorted(plan["run_order"], key=lambda row: row["seq"])
    if len(order) != EXPECTED_RUNS or [row["seq"] for row in order] != list(range(1, EXPECTED_RUNS + 1)):
        raise ValueError("FIX-14 requires 120 unique sequential slots")
    if len({row["instance_id"] for row in order}) != 30:
        raise ValueError("FIX-14 requires 30 task blocks")
    expected = {(group, repeat) for group in GROUPS for repeat in (1, 2)}
    for block in range(1, 31):
        rows = [row for row in order if row["block"] == block]
        if (len(rows) != 4 or {(row["group"], row["repeat"]) for row in rows} != expected
                or len({row["instance_id"] for row in rows}) != 1):
            raise ValueError("each FIX-14 block requires T8/O32 with two repeats")
    if complete and ({row["instance_id"] for row in order} - mapping.keys()
                     or record.get("pending") or record.get("unfilled")):
        raise ValueError("all FIX-13 positions must pass ingestion before FIX-14")
    return [{**row, "task": mapping[row["instance_id"]]} for row in order]


def load_inputs(*, complete=True):
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    record = json.loads(INGEST.read_text(encoding="utf-8"))
    slots = load_slots(plan, record, complete=complete)
    tasks = {row["id"]: row for row in __import__("yaml").safe_load(
        MANIFEST.read_text(encoding="utf-8"))["tasks"]}
    return slots, tasks


def inspect_run(path: Path, group: str) -> dict:
    original = legacy.THINKING
    legacy.THINKING = {"T8": ("enabled", "high"), "O32": ("disabled", None)}
    try:
        row = legacy.inspect_run(path, group)
    finally:
        legacy.THINKING = original
    summary = row["summary"]
    if summary.get("profile") != PROFILES[group].stem or summary.get("variant") != VARIANTS[group]:
        row["problems"].append("profile/variant differs from FIX-14 assignment")
    ledger = json.loads((path / "cny-ledger.json").read_text(encoding="utf-8"))
    cost = Decimal(ledger["cost_committed"])
    if not cost.is_finite() or cost < 0 or cost > RUN_CAP:
        raise ValueError("invalid FIX-14 per-run settlement")
    if cost != row["cost"]:
        row["problems"].append("summary and CNY ledger cost differ")
    if (Decimal(ledger["hard_caps"]["total"]) != RUN_CAP or ledger.get("outstanding") != 0
            or ledger.get("settlement") != {"mode": "provider_usage", "time_of_day": False}):
        row["problems"].append("CNY ledger cap, settlement or outstanding reservations differ")
    row["cost"] = cost
    records = legacy.settled_records(path)
    expected_output = 8192 if group == "T8" else 32768
    for record in records:
        request = (record.get("telemetry") or {}).get("request") or {}
        if request and (request.get("model") != legacy.EXPECTED_MODEL
                        or request.get("max_tokens") != expected_output):
            row["problems"].append("request model/output limit differs from FIX-14 assignment")
        if group == "O32" and (record.get("telemetry") or {}).get("reasoning_tokens", 0):
            row["problems"].append("reasoning tokens appeared with thinking disabled")
    return {**row, "group": group,
            "compressed": any((record.get("telemetry") or {}).get("masked") for record in records)}


def preflight() -> dict:
    report = {"model_calls": 0, "groups": GROUPS, "expected_slots": EXPECTED_RUNS,
              "batch_cap_cny": str(BATCH_CAP), "start_threshold_cny": str(START_THRESHOLD),
              "inflight_buffer_cny": str(BUFFER)}
    report["config"] = config_report()
    try:
        slots, _ = load_inputs()
        report.update(slots_ok=True, accepted_slots=len(slots))
    except (OSError, ValueError, KeyError) as exc:
        report.update(slots_ok=False, slots_error=str(exc))
    report["pass"] = bool(report["config"]["ok"] and report["slots_ok"])
    return report


def check_authorized(value):
    if value != "120,80":
        raise SystemExit("real FIX-14 execution requires separately reviewed --authorized 120,80 "
                         "(cap raised from 60 to 80 on 2026-11-13)")


def rebind(reason: str):
    """Archive the current input binding and write a new one under the approved revision."""
    import shutil

    target = OUT / "input-binding.json"
    log = OUT / "rebind-log.jsonl"
    if target.exists():
        history = OUT / "binding-history"
        history.mkdir(parents=True, exist_ok=True)
        stamp = __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(target, history / f"{stamp}-input-binding.json")
        target.unlink()
    identity = json.loads((PREFLIGHT / "identity.json").read_text(encoding="utf-8"))
    prior = freeze_inputs(OUT, mock=False, prior_ledgers=[ROOT / identity["ledger_path"]],
                          allow_existing=True)
    record = {"schema": "fix14.rebind.v1",
              "at": __import__("datetime").datetime.now(
                  __import__("datetime").timezone.utc).isoformat(),
              "reason": reason, "cap_cny": str(BATCH_CAP),
              "start_threshold_cny": str(START_THRESHOLD), "prior_cost_cny": str(prior)}
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def readiness():
    from src.runtime.session import source_fingerprint

    ready = json.loads(READY.read_text(encoding="utf-8"))
    if (ready.get("ok") is not True or ready.get("tasks") != 30
            or ready.get("slots") != EXPECTED_RUNS or ready.get("e2e_cases") != 2):
        raise ValueError("complete FIX-14 readiness evidence is required")
    if ready.get("source_fingerprint") != source_fingerprint(ROOT):
        raise ValueError("runtime source changed after FIX-14 readiness checks")
    for name, expected in ready.get("sources", {}).items():
        if __import__("hashlib").sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError("FIX-14 readiness source changed: " + name)
    identity_path = PREFLIGHT / "identity.json"
    if not identity_path.is_file():
        raise ValueError("FIX-14 identity smoke is required before paid execution")
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    if (identity.get("ok") is not True or identity.get("model") != legacy.EXPECTED_MODEL
            or not identity.get("system_fingerprint")):
        raise ValueError("verified FIX-14 provider identity and fingerprint are required")
    return ready, identity


def freeze_inputs(out_root=None, *, mock, prior_ledgers, allow_existing=False):
    """FIX-14 binding. Reuses the frozen FIX-13 offline references for the same 30 tasks."""
    from src.runtime.session import source_fingerprint

    out_root = Path(out_root) if out_root is not None else OUT
    offline = ROOT / "runs/fix13-preflight"
    paths = [PLAN, INGEST, MANIFEST, *PROFILES.values(), ROOT / "prompts/swe_b0.md",
             Path(__file__), Path(base.__file__), Path(legacy.__file__),
             Path(base.config_diff.__file__), ROOT / "scripts/run_swe_task.py",
             ROOT / "scripts/run_cr2.py", ROOT / "scripts/run_a5_main.py",
             ROOT / "scripts/rebench/fix14_preflight.py", offline / "references.json",
             offline / "analysis-binding.json"]
    if not mock:
        _, identity = readiness()
        if (ROOT / identity["ledger_path"]).resolve() not in {path.resolve() for path in prior_ledgers}:
            raise ValueError("the FIX-14 identity smoke ledger must be included in --prior-ledger")
        paths += [READY, PREFLIGHT / "identity.json", PREFLIGHT / "e2e-report.json"]
    if len({path.resolve() for path in prior_ledgers}) != len(prior_ledgers):
        raise ValueError("duplicate prior ledger")
    prior = Decimal(0)
    for path in prior_ledgers:
        ledger = json.loads(path.read_text(encoding="utf-8"))
        amount = Decimal(ledger["cost_committed"])
        if (not amount.is_finite() or amount < 0 or ledger.get("outstanding") != 0
                or any(row.get("status") == "unknown_keeps_reserve"
                       for row in ledger.get("settlements", []))):
            raise ValueError("invalid prior CNY ledger")
        prior += amount
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise ValueError("FIX-14 binding input is missing: " + ", ".join(missing))
    binding = {"schema": "fix14.input-binding.v1", "mock": mock, "groups": list(GROUPS),
               "expected_runs": EXPECTED_RUNS, "cap_cny": str(BATCH_CAP), "prior_cost_cny": str(prior),
               "sources": {str(path.resolve()): __import__("hashlib").sha256(path.read_bytes()).hexdigest()
                           for path in paths + prior_ledgers},
               "source_fingerprint": source_fingerprint(ROOT),
               "offline_references": "reuses runs/fix13-preflight/references.json: same 30 tasks, same work root"}
    binding = json.loads(json.dumps(binding))
    out_root.mkdir(parents=True, exist_ok=True)
    target = out_root / "input-binding.json"
    if target.exists():
        if json.loads(target.read_text(encoding="utf-8")) != binding:
            raise ValueError("frozen inputs or prior spending changed, review before resuming")
    else:
        if not allow_existing and any(out_root.rglob("cny-ledger.json")):
            raise ValueError("unbound paid attempts exist, review before creating a binding")
        target.write_text(json.dumps(binding, indent=2) + "\n", encoding="utf-8")
    return prior


async def run_one(task, group, output, *, mock):
    from scripts.run_swe_task import run_task
    return await run_task(task, PROFILES[group], output, mock=mock, variant=VARIANTS[group],
                          on_agent=legacy.tool_recorder(output, tap_model=not mock),
                          ledger_options=legacy.ledger_options(load_config(PROFILES[group])))


def child_spec(task, group, output, mock):
    if group not in GROUPS:
        raise ValueError("unknown FIX-14 group")
    return {"task": task, "group": group, "output": str(output), "mock": mock}


async def run_in_child(task, group, output, *, mock):
    spec = json.dumps(child_spec(task, group, output, mock)).encode()
    proc = await asyncio.create_subprocess_exec(sys.executable, str(Path(__file__).resolve()), "--child",
                                                stdin=asyncio.subprocess.PIPE, cwd=ROOT)
    process_path = output / "child-process.json"
    process_path.write_text(json.dumps({"pid": proc.pid, "finished": False}) + "\n", encoding="utf-8")
    await proc.communicate(spec)
    process_path.write_text(json.dumps({"pid": proc.pid, "finished": True,
                                        "returncode": proc.returncode}) + "\n", encoding="utf-8")
    if proc.returncode:
        raise RuntimeError(f"FIX-14 run process exited with code {proc.returncode}")


# Reuse FIX-13's safe scheduler after replacing only the frozen experiment bindings.
base.GROUPS = GROUPS
base.PROFILES = PROFILES
base.VARIANTS = VARIANTS
base.PLAN = PLAN
base.INGEST = INGEST
base.MANIFEST = MANIFEST
base.OUT = OUT
base.PREFLIGHT = PREFLIGHT
base.READY = READY
base.EXPECTED_RUNS = EXPECTED_RUNS
base.BATCH_CAP = BATCH_CAP
base.PARALLEL = PARALLEL
base.RUN_CAP = RUN_CAP
base.BUFFER = BUFFER
base.START_THRESHOLD = START_THRESHOLD
base.HISTORICAL_MEAN = HISTORICAL_MEAN
base.config_report = config_report
base.load_slots = load_slots
base.load_inputs = load_inputs
base.inspect_run = inspect_run
base.check_authorized = check_authorized
base.readiness = readiness
base.run_in_child = run_in_child
base.freeze_inputs = freeze_inputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--authorized")
    parser.add_argument("--prior-ledger", type=Path, action="append", default=[])
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--rebind", metavar="REASON",
                        help="archive the old input binding and write one under the approved revision")
    args = parser.parse_args()
    if args.child:
        spec = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        asyncio.run(run_one(spec["task"], spec["group"], Path(spec["output"]), mock=spec["mock"]))
        return
    if args.rebind:
        check_authorized(args.authorized)
        print(json.dumps(rebind(args.rebind), indent=2))
        return
    if args.authorized is None or args.preflight:
        result = preflight()
        print(json.dumps(result, indent=2))
        raise SystemExit(0 if result["pass"] else 1)
    check_authorized(args.authorized)
    slots, tasks = load_inputs()
    print(json.dumps(asyncio.run(base.run_batch(
        slots, tasks, out_root=OUT, mock=False, run=run_in_child,
        prior_ledgers=args.prior_ledger)), indent=2))


if __name__ == "__main__":
    main()
