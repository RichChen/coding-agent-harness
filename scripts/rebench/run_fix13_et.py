"""FIX-13 ET addendum launcher: 60 slots on the same 30 tasks. No model calls in --preflight.

ET is UT plus the fixed result-explanation sentence. The four original arms are not
rerun; their frozen results are compared task by task. Real execution requires
--authorized 60,60 after the ET design and preflight are reviewed.

The 60 CNY cap includes a 12 CNY in-flight buffer. New attempts stop at 48 CNY
settled, with at most three in-flight attempts capped at 4 CNY each.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import deque
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.rebench import run_fix13 as base  # noqa: E402
from scripts.rebench import run_unleash as legacy  # noqa: E402
from scripts.rebench.pilot_ingest import task_map  # noqa: E402
from src.harness.factory import load_config  # noqa: E402

GROUP = "ET"
PROFILE = ROOT / "configs/rebench/fix13-et.yaml"
VARIANT = "FIX13-ET"
PLAN = ROOT / "scripts/rebench/out/fix13_et_tasks.json"
INGEST = ROOT / "scripts/rebench/out/fix13_ingest.json"
MANIFEST = ROOT / "configs/rebench/fix13-tasks.yaml"
BASE_PLAN = ROOT / "scripts/rebench/out/fix13_tasks.json"
BASE_READY = ROOT / "runs/fix13-preflight/ready.json"
BASE_IDENTITY = ROOT / "runs/fix13-preflight/identity.json"
OUT = ROOT / "runs/fix13-et"
PREFLIGHT = ROOT / "runs/fix13-et-preflight"
READY = PREFLIGHT / "ready.json"
IDENTITY = PREFLIGHT / "identity.json"
EXPECTED_RUNS = 60
BATCH_CAP = Decimal("60")
PARALLEL = 3
RUN_CAP = Decimal("4")
BUFFER = PARALLEL * RUN_CAP
START_THRESHOLD = BATCH_CAP - BUFFER
MEAN = Decimal("0.26818")
ORIGINAL_FINGERPRINT = "bc19ba97cbe016479903222bb1578db6"
ORIGINAL_GROUPS = ("U", "R", "E", "UT")
ORIGINAL_PROFILES = {g: ROOT / "configs/rebench" / f"fix13-{g.lower()}.yaml" for g in ORIGINAL_GROUPS}


def fetch_price_page():
    import httpx

    response = httpx.get(legacy.PRICE_URL, timeout=60, follow_redirects=True, trust_env=False)
    response.raise_for_status()
    return response.text


def load_slots():
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    record = json.loads(INGEST.read_text(encoding="utf-8"))
    order = sorted(plan["run_order"], key=lambda row: row["seq"])
    if len(order) != EXPECTED_RUNS or [r["seq"] for r in order] != list(range(1, EXPECTED_RUNS + 1)):
        raise ValueError("ET requires 60 unique sequential slots")
    if [r["block"] for r in order] != [block for block in range(1, 31) for _ in range(2)]:
        raise ValueError("ET task blocks must be contiguous in the frozen run order")
    for block in range(1, 31):
        rows = [r for r in order if r["block"] == block]
        if (len(rows) != 2 or {r["group"] for r in rows} != {GROUP}
                or {r["repeat"] for r in rows} != {1, 2}
                or len({r["instance_id"] for r in rows}) != 1):
            raise ValueError(f"ET block {block} must be one task with two ET slots")
    originals = [r["instance_id"] for r in order if r["repeat"] == 1]
    if len(set(originals)) != 30:
        raise ValueError("ET requires 30 distinct task blocks")
    base_plan = json.loads(BASE_PLAN.read_text(encoding="utf-8"))
    base_order = sorted(base_plan["run_order"], key=lambda row: row["seq"])
    base_blocks = [next(r["instance_id"] for r in base_order if r["block"] == b) for b in range(1, 31)]
    if originals != base_blocks:
        raise ValueError("ET block order must match the frozen FIX-13 task order")
    if record.get("plan_sha256") != sha256(BASE_PLAN.read_bytes()).hexdigest():
        raise ValueError("ingest does not match the frozen FIX-13 plan")
    if record.get("pending") or record.get("unfilled"):
        raise ValueError("ingest still has pending or unfilled positions")
    mapping = task_map(record)
    slots = []
    for row in order:
        task = mapping.get(row["instance_id"])
        if task is None:
            raise ValueError("ET task has no accepted ingest: " + row["instance_id"])
        slots.append({**row, "task": task})
    tasks = {row["id"]: row for row in yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))["tasks"]}
    if {slot["task"] for slot in slots} - tasks.keys():
        raise ValueError("accepted ET task is missing from the manifest")
    return slots, tasks


def config_report() -> dict:
    ut = load_config(ORIGINAL_PROFILES["UT"])
    et = load_config(PROFILE)

    def flat(value, prefix=""):
        out = {}
        for key, item in value.items():
            name = f"{prefix}.{key}" if prefix else key
            if isinstance(item, dict):
                out.update(flat(item, name))
            else:
                out[name] = item
        return out

    before, after = flat(ut), flat(et)
    added = {key: after[key] for key in after.keys() - before.keys()}
    removed = sorted(before.keys() - after.keys())
    changed = {key: [before[key], after[key]] for key in before.keys() & after.keys() if before[key] != after[key]}
    expected = {"runtime.tool_intervention": "result_explanation"}
    ready = json.loads(BASE_READY.read_text(encoding="utf-8"))
    frozen = {name: ready["sources"].get(name) for name in
              [f"configs/rebench/fix13-{g.lower()}.yaml" for g in ORIGINAL_GROUPS]}
    original_ok = all(frozen[name] and sha256((ROOT / name).read_bytes()).hexdigest() == frozen[name]
                      for name in frozen)
    return {"ok": added == expected and not removed and not changed and original_ok,
            "added_vs_ut": added, "removed_vs_ut": removed, "changed_vs_ut": changed,
            "original_configs_unchanged": original_ok,
            "model": et["llm"]["model"], "thinking": et["llm"].get("thinking"),
            "reasoning_effort": et["llm"].get("reasoning_effort"),
            "max_tokens": et["llm"]["max_tokens"]}


def prompt_report() -> dict:
    from src.harness.factory import build_agent
    from src.runtime.tool_intervention import RESULT_EXPLANATION

    import tempfile

    prompts = {}
    with tempfile.TemporaryDirectory() as tmp:
        for name, profile in (("UT", ORIGINAL_PROFILES["UT"]), (GROUP, PROFILE)):
            config = load_config(profile)
            config["llm"].update(base_url="http://localhost", api_key="fixture")
            config["policy"]["command"]["execution_profile"] = "legacy_host"
            config["tracing"]["output_dir"] = str(Path(tmp) / name / "trace")
            agent = build_agent(config, Path(tmp) / name)
            prompts[name] = (agent.system_prompt, agent.tool_intervention)
            # No request is made, so the client has no open connection to close here.
            # Closing it would need asyncio.run, which is unsafe when this report is
            # built inside the preflight event loop.
    expected = prompts["UT"][0] + "\n\n" + RESULT_EXPLANATION
    return {"ok": prompts[GROUP][0] == expected and prompts[GROUP][0].count(RESULT_EXPLANATION) == 1
            and prompts[GROUP][1] == "result_explanation" and prompts["UT"][1] == "none",
            "et_is_ut_plus_suffix": prompts[GROUP][0] == expected,
            "suffix_occurrences": prompts[GROUP][0].count(RESULT_EXPLANATION),
            "interventions": {name: value[1] for name, value in prompts.items()}}


def preflight_report() -> dict:
    from src.runtime.session import source_fingerprint

    report = {"model_calls": 0, "group": GROUP, "expected_slots": EXPECTED_RUNS,
              "batch_cap_cny": str(BATCH_CAP), "start_threshold_cny": str(START_THRESHOLD),
              "inflight_buffer_cny": str(BUFFER)}
    report["config"] = config_report()
    report["prompt"] = prompt_report()
    ready = json.loads(BASE_READY.read_text(encoding="utf-8"))
    report["runtime_unchanged"] = ready.get("source_fingerprint") == source_fingerprint(ROOT)
    try:
        slots, _ = load_slots()
        report.update(slots_ok=True, accepted_slots=len(slots),
                      blocks=len({slot["block"] for slot in slots}))
    except (OSError, ValueError, KeyError) as exc:
        report.update(slots_ok=False, slots_error=str(exc))
    report["et_ready"] = READY.is_file()
    report["pass"] = bool(report["config"]["ok"] and report["prompt"]["ok"] and report["runtime_unchanged"]
                          and report.get("slots_ok"))
    return report


def readiness():
    from src.runtime.session import source_fingerprint

    ready = json.loads(READY.read_text(encoding="utf-8"))
    if ready.get("ok") is not True or ready.get("tasks") != 30 or ready.get("slots") != EXPECTED_RUNS:
        raise ValueError("complete ET readiness evidence is required")
    if ready.get("e2e_cases") != 2:
        raise ValueError("ET requires two reviewed no-model cases")
    if ready.get("source_fingerprint") != source_fingerprint(ROOT):
        raise ValueError("runtime source changed after ET readiness checks")
    for name, expected in ready["sources"].items():
        if sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError("ET readiness source changed: " + name)
    identity = json.loads(IDENTITY.read_text(encoding="utf-8"))
    if (identity.get("ok") is not True or identity.get("model") != legacy.EXPECTED_MODEL
            or identity.get("system_fingerprint") != ORIGINAL_FINGERPRINT):
        raise ValueError("ET identity must match the original FIX-13 fingerprint window")
    price = identity.get("price_snapshot") or {}
    for key, want in legacy.EXPECTED_PRICE.items():
        if price.get(key) != want:
            raise ValueError(f"ET identity price {key} differs from the original window")
    return ready, identity


def inspect_et(path: Path) -> dict:
    row = legacy.inspect_run(path, "UT")
    summary = row["summary"]
    if summary.get("profile") != PROFILE.stem or summary.get("variant") != VARIANT:
        row["problems"].append("profile/variant differs from the ET arm")
    ledger_path = path / "cny-ledger.json"
    if not ledger_path.is_file():
        row["problems"].append("CNY ledger is missing")
    else:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        cost = Decimal(ledger["cost_committed"])
        if not cost.is_finite() or cost < 0 or cost > RUN_CAP:
            raise ValueError("invalid per-run CNY settlement")
        if cost != row["cost"]:
            row["problems"].append("summary and CNY ledger cost differ")
        if (Decimal(ledger["hard_caps"]["total"]) != RUN_CAP or ledger.get("outstanding") != 0
                or ledger.get("settlement") != {"mode": "provider_usage", "time_of_day": False}):
            row["problems"].append("CNY ledger cap, settlement or outstanding reservations differ")
        row["cost"] = cost
    for record in legacy.settled_records(path):
        request = (record.get("telemetry") or {}).get("request") or {}
        if request and (request.get("model") != legacy.EXPECTED_MODEL or request.get("max_tokens") != 32768):
            row["problems"].append("request model/output limit differs from the ET arm")
        if record.get("billing_stop_reason") == "billing_usage_unknown":
            row["problems"].append("billing usage unknown")
    return {**row, "group": GROUP,
            "compressed": any((record.get("telemetry") or {}).get("masked")
                              for record in legacy.settled_records(path))}


def spent(rows, prior=Decimal(0)):
    return prior + sum((row["cost"] for row in rows.values()), Decimal(0))


def forecast(rows, slots, prior=Decimal(0)):
    completed = sum(legacy.slot_dir(OUT, slot) in rows for slot in slots)
    group_spend = sum((row["cost"] for row in rows.values()), Decimal(0))
    estimate = max(MEAN, group_spend / completed) if completed else MEAN
    remaining = len(slots) - completed
    return spent(rows, prior) + remaining * (2 * estimate + Decimal("0.25"))


def resource_stop(row):
    reason = str(row["summary"].get("stop_reason") or "")
    return reason in ("context_overflow", "context_invalid", "admission_rejected") or bool(row.get("compressed"))


def batch_halt(rows, *, mock, prior=Decimal(0), identity=None):
    attempts = list(rows.values())
    for row in attempts:
        reason = legacy.run_checks([{**row["summary"], "status": row["status"]}])
        if reason or row["http402"] or row["problems"]:
            return reason or ("HTTP 402" if row["http402"] else row["problems"][0])
        if (row["summary"].get("model") or {}).get("changed_to"):
            return "model changed during a run"
    identities = {(r["summary"]["model"].get("name"), r["summary"]["model"].get("base_url_host"))
                  for r in attempts if r["summary"].get("model")}
    if len(identities) > 1 or any(name != legacy.EXPECTED_MODEL for name, _ in identities):
        return "model or base URL identity changed"
    if not mock:
        names = {name for r in attempts for name in r["models"]}
        fingerprints = {fp for r in attempts for fp in r["fingerprints"]}
        if names and names != {legacy.EXPECTED_MODEL}:
            return "returned model identity changed"
        if len(fingerprints) > 1:
            return "system fingerprint changed"
        if identity and fingerprints and fingerprints != {identity["system_fingerprint"]}:
            return "system fingerprint differs from the ET identity window"
        reason = legacy.model_name_halt([r["path"] for r in attempts])
        if reason:
            return reason
    recent = sorted(attempts, key=lambda r: (r["path"] / "summary.json").stat().st_mtime_ns)[-10:]
    if len(recent) >= 10 and sum(r["status"] == "infra_error" for r in recent) >= 3:
        return "at least three infrastructure errors in the last ten attempts"
    done = [r for r in attempts if not r["retired"]]
    stops = sum(resource_stop(r) for r in done)
    if (len(done) < 20 and stops >= 3) or (len(done) >= 20 and stops > Decimal("0.05") * len(done)):
        return f"ET: {stops}/{len(done)} context/admission stops"
    if spent(rows, prior) >= START_THRESHOLD:
        return "settled spend reached 48 CNY, preserving the 12 CNY in-flight buffer"
    return None


def existing_attempts(slots, *, mock):
    rows = {}
    for slot in slots:
        final = legacy.slot_dir(OUT, slot)
        for path in [*sorted(final.parent.glob(final.name + ".infra*")), final]:
            if not path.exists():
                continue
            process_path = path / "child-process.json"
            if process_path.exists():
                process = json.loads(process_path.read_text(encoding="utf-8"))
                if process.get("finished") is not True and legacy._pid_alive(process["pid"]):
                    raise ValueError(f"attempt process is still alive, reconcile before resuming: {path}")
            if not (path / "summary.json").is_file():
                raise ValueError(f"interrupted attempt requires process and billing reconciliation: {path}")
            row = inspect_et(path)
            summary = row["summary"]
            if (bool(summary.get("mock")) != mock or summary.get("profile") != PROFILE.stem
                    or summary.get("variant") != VARIANT):
                raise ValueError(f"attempt does not belong to the frozen ET assignment: {path}")
            rows[path] = row
    return rows


def retire(rows, output):
    if legacy.infra_attempts(output) >= 1:
        return False
    return legacy.retire(rows, output)


def freeze_inputs(*, mock, prior_ledgers):
    from src.runtime.session import source_fingerprint

    paths = [PLAN, PROFILE, MANIFEST, INGEST, BASE_PLAN, BASE_READY, *ORIGINAL_PROFILES.values(),
             ROOT / "prompts/swe_b0.md", Path(__file__), Path(base.__file__),
             ROOT / "scripts/run_swe_task.py", ROOT / "scripts/run_cr2.py", ROOT / "scripts/run_a5_main.py"]
    if not mock:
        _, identity = readiness()
        if (ROOT / identity["ledger_path"]).resolve() not in {p.resolve() for p in prior_ledgers}:
            raise ValueError("the ET identity smoke ledger must be included in --prior-ledger")
        paths += [READY, IDENTITY, BASE_IDENTITY,
                  ROOT / "runs/fix13-preflight/references.json",
                  ROOT / "runs/fix13-preflight/analysis-binding.json"]
    if len({p.resolve() for p in prior_ledgers}) != len(prior_ledgers):
        raise ValueError("duplicate prior ledger")
    prior = Decimal(0)
    for path in prior_ledgers:
        ledger = json.loads(path.read_text(encoding="utf-8"))
        amount = Decimal(ledger["cost_committed"])
        if (not amount.is_finite() or amount < 0 or ledger.get("outstanding") != 0
                or any(row.get("status") == "unknown_keeps_reserve" for row in ledger.get("settlements", []))):
            raise ValueError("invalid prior CNY ledger")
        prior += amount
    binding = {"schema": "fix13et.input-binding.v1", "mock": mock, "group": GROUP,
               "expected_runs": EXPECTED_RUNS, "cap_cny": str(BATCH_CAP), "prior_cost_cny": str(prior),
               "sources": {str(p.resolve()): sha256(p.read_bytes()).hexdigest() for p in paths + prior_ledgers},
               "source_fingerprint": source_fingerprint(ROOT)}
    binding = json.loads(json.dumps(binding))
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "input-binding.json"
    if target.exists():
        if json.loads(target.read_text(encoding="utf-8")) != binding:
            raise ValueError("frozen inputs or prior spending changed, review before resuming")
    else:
        if any(OUT.rglob("cny-ledger.json")):
            raise ValueError("unbound paid attempts exist, review before creating a binding")
        target.write_text(json.dumps(binding, indent=2) + "\n", encoding="utf-8")
    return prior


async def run_one_et(task, output, *, mock):
    from scripts.run_swe_task import run_task

    return await run_task(task, PROFILE, output, mock=mock, variant=VARIANT,
                          on_agent=legacy.tool_recorder(output, tap_model=not mock),
                          ledger_options=legacy.ledger_options(load_config(PROFILE)))


async def run_in_child_et(task, output, *, mock):
    spec = json.dumps({"task": task, "output": str(output), "mock": mock}).encode()
    proc = await asyncio.create_subprocess_exec(sys.executable, str(Path(__file__).resolve()), "--child",
                                                stdin=asyncio.subprocess.PIPE, cwd=ROOT)
    process_path = output / "child-process.json"
    process_path.write_text(json.dumps({"pid": proc.pid, "finished": False}) + "\n", encoding="utf-8")
    await proc.communicate(spec)
    process_path.write_text(json.dumps({"pid": proc.pid, "finished": True, "returncode": proc.returncode}) + "\n",
                            encoding="utf-8")
    if proc.returncode:
        raise RuntimeError(f"run process exited with code {proc.returncode}")


async def run_batch(slots, tasks, *, mock, run=run_in_child_et, fetch=fetch_price_page, prior_ledgers=()):
    if not config_report()["ok"] or not prompt_report()["ok"]:
        raise ValueError("ET config or prompt differs from the reviewed addendum")
    identity = None if mock else readiness()[1]
    lock = legacy.acquire_lock(OUT)
    try:
        prior = freeze_inputs(mock=mock, prior_ledgers=list(prior_ledgers))
        rows = existing_attempts(slots, mock=mock)
        reference_path = OUT / "price-reference.json"
        if reference_path.exists():
            reference = json.loads(reference_path.read_text(encoding="utf-8"))
        elif BASE_IDENTITY.exists():
            reference = json.loads(BASE_IDENTITY.read_text(encoding="utf-8"))["price_snapshot"]
        else:
            reference = identity["price_snapshot"] if identity else None
        last_price = None
        halt = batch_halt(rows, mock=mock, prior=prior, identity=identity)
        started, max_active, active = [], 0, 0
        unreconciled = []

        def price_check(*, force=False):
            nonlocal reference, last_price, halt
            if fetch is None:
                if not mock:
                    raise ValueError("real ET runs require a price-page check")
                return
            at = legacy.now_utc()
            if not force and last_price and (at - last_price).total_seconds() < legacy.PRICE_RECHECK_SECONDS:
                return
            try:
                page = fetch()
            except Exception as exc:
                halt = halt or f"price check unavailable: {type(exc).__name__}"
                return
            snapshot = legacy.price_snapshot(page)
            legacy.archive_price(OUT / "price", "check", page, snapshot, at)
            problem = legacy.price_problem(snapshot, reference)
            if problem:
                halt = halt or problem
                return
            if reference is None:
                reference = snapshot
                reference_path.write_text(json.dumps(reference, indent=2) + "\n", encoding="utf-8")
            last_price = at

        price_check(force=True)

        async def one(slot):
            nonlocal halt, active, max_active
            output = legacy.slot_dir(OUT, slot)
            while not halt:
                halt = batch_halt(rows, mock=mock, prior=prior, identity=identity)
                if halt:
                    return
                price_check()
                if halt:
                    return
                active += 1
                max_active = max(max_active, active)
                started.append(slot["seq"])
                print("start " + legacy.slot_label(slot), flush=True)
                row = None
                try:
                    output.mkdir(parents=True, exist_ok=False)
                    await run(tasks[slot["task"]], output, mock=mock)
                    row = inspect_et(output)
                    rows[output] = row
                except (Exception, SystemExit) as exc:
                    unreconciled.append(str(output))
                    halt = halt or f"attempt needs reconciliation: {type(exc).__name__}: {exc}"
                    return
                finally:
                    active -= 1
                halt = halt or batch_halt(rows, mock=mock, prior=prior, identity=identity)
                if row is not None and row["rerun"] and not row["http402"] and not halt and retire(rows, output):
                    continue
                return

        for block in sorted({slot["block"] for slot in slots}):
            if halt:
                break
            queue = deque()
            for slot in (s for s in slots if s["block"] == block):
                output = legacy.slot_dir(OUT, slot)
                row = rows.get(output)
                if row is not None:
                    if not row["rerun"] or row["http402"] or not retire(rows, output):
                        continue
                queue.append(slot)

            async def worker():
                while queue and not halt:
                    await one(queue.popleft())

            await asyncio.gather(*(worker() for _ in range(PARALLEL)))
            if not halt and block % 5 == 0 and forecast(rows, slots, prior) > BATCH_CAP:
                halt = "conservative forecast exceeds 60 CNY at a completed five-block checkpoint"
            block_finished = all(legacy.slot_dir(OUT, slot) in rows for slot in slots if slot["block"] == block)
            progress = {"current_block": block, "completed_blocks": block - 1 + int(block_finished),
                        "final_runs": sum(not r["retired"] for r in rows.values()),
                        "spent_cny": None if unreconciled else str(spent(rows, prior)),
                        "known_settled_cny": str(spent(rows, prior)),
                        "halt_reason": halt,
                        "by_group": {GROUP: {"final_runs": sum(r["group"] == GROUP and not r["retired"]
                                                             for r in rows.values()),
                                             "solved": sum(r["group"] == GROUP and not r["retired"]
                                                           and bool(r["summary"].get("solved"))
                                                           for r in rows.values())}}}
            (OUT / "progress.json").write_text(json.dumps(progress, indent=2) + "\n", encoding="utf-8")
            if block % 5 == 0 or halt:
                print("checkpoint " + json.dumps(progress), flush=True)
            if halt:
                break
        price_check(force=True)
        report = {"schema": "fix13et.batch-report.v1", "mock": mock, "slots": len(slots), "group": GROUP,
                  "batch_cap_cny": str(BATCH_CAP), "start_threshold_cny": str(START_THRESHOLD),
                  "inflight_buffer_cny": str(BUFFER), "prior_cost_cny": str(prior),
                  "spent_cny": None if unreconciled else str(spent(rows, prior)),
                  "known_settled_cny": str(spent(rows, prior)),
                  "unreconciled_attempts": unreconciled,
                  "unreconciled_reserve_cny": str(len(unreconciled) * RUN_CAP),
                  "forecast_cny": None if unreconciled else str(forecast(rows, slots, prior)),
                  "halt_reason": halt, "started_this_launch": started, "max_concurrent": max_active,
                  "final_runs": sum(not row["retired"] for row in rows.values()),
                  "by_group": {GROUP: {"final_runs": sum(r["group"] == GROUP and not r["retired"]
                                                        for r in rows.values()),
                                       "cost_cny": str(sum((r["cost"] for r in rows.values()
                                                            if r["group"] == GROUP), Decimal(0)))}}}
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "batch-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return report
    finally:
        lock.unlink(missing_ok=True)


def check_authorized(value):
    if value != "60,60":
        raise SystemExit("real ET execution requires separately reviewed --authorized 60,60")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--authorized")
    parser.add_argument("--prior-ledger", type=Path, action="append", default=[])
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        spec = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        asyncio.run(run_one_et(spec["task"], Path(spec["output"]), mock=spec["mock"]))
    elif args.preflight or args.authorized is None:
        report = preflight_report()
        print(json.dumps(report, indent=2))
        raise SystemExit(0 if report["pass"] else 1)
    else:
        check_authorized(args.authorized)
        slots, tasks = load_slots()
        print(json.dumps(asyncio.run(run_batch(slots, tasks, mock=False,
                                               prior_ledgers=args.prior_ledger)), indent=2))


if __name__ == "__main__":
    main()
