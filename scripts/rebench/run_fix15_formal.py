"""FIX-15 formal three-arm launcher. The formal run uses the frozen preflight binding and 180 slots."""
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
from scripts.rebench import run_unleash as legacy  # noqa: E402
from scripts.rebench import unleash_config_diff as config_diff  # noqa: E402
from src.harness.factory import load_config  # noqa: E402

GROUPS = ("U-new", "N", "E-new")
PROFILES = {"U-new": ROOT / "configs/rebench/fix15-u-new.yaml", "N": ROOT / "configs/rebench/fix15-n.yaml", "E-new": ROOT / "configs/rebench/fix15-e-new.yaml"}
VARIANTS = {"U-new": "FIX15-U-NEW", "N": "FIX15-N", "E-new": "FIX15-E-NEW"}
PLAN = ROOT / "scripts/rebench/out/fix15_tasks.json"
INGEST = ROOT / "scripts/rebench/out/fix13_ingest.json"
MANIFEST = ROOT / "configs/rebench/fix13-tasks.yaml"
OUT = ROOT / "runs/fix15"
PREFLIGHT = ROOT / "runs/fix15-preflight"
READY = PREFLIGHT / "input-binding.json"
RECOVERY_BLOCKED = OUT / "recovery-blocked.json"
EXPECTED_RUNS = 180
BATCH_CAP = Decimal("60")
PARALLEL = 3
RUN_CAP = Decimal("4")
BUFFER = PARALLEL * RUN_CAP
START_THRESHOLD = Decimal("48")
HISTORICAL_MEAN = {"U-new": Decimal("0.2634"), "N": Decimal("0.2634"), "E-new": Decimal("0.1221")}


def fetch_price_page():
    import httpx

    response = httpx.get(legacy.PRICE_URL, timeout=60, follow_redirects=True, trust_env=False)
    response.raise_for_status()
    return response.text


def config_report() -> dict:
    configs = {g: config_diff.load(p) for g, p in PROFILES.items()}
    baseline = config_diff.load(legacy.PROFILES["U"])
    baseline["llm.model"] = legacy.EXPECTED_MODEL
    declared = {"U": {}, "R": {"runtime.tool_intervention": ("none", "repeat_reminder")},
                "E": {"runtime.tool_intervention": ("none", "result_explanation")},
                "UT": config_diff.THINKING}
    differences = {g: config_diff.compare(baseline, config) for g, config in configs.items()}
    return {"ok": all(differences[g] == declared[g] for g in GROUPS), "differences": differences,
            "models": sorted({c["llm.model"] for c in configs.values()})}


def load_slots(plan, record, *, complete=True):
    if record.get("plan_sha256") != sha256(PLAN.read_bytes()).hexdigest():
        raise ValueError("ingestion does not match the frozen FIX-13 plan")
    mapping = legacy.task_map(record)
    order = sorted(plan["run_order"], key=lambda row: row["seq"])
    if len(order) != EXPECTED_RUNS or [r["seq"] for r in order] != list(range(1, EXPECTED_RUNS + 1)):
        raise ValueError("FIX-13 requires 240 unique sequential slots")
    if [row["block"] for row in order] != [block for block in range(1, 31) for _ in range(8)]:
        raise ValueError("FIX-13 task blocks must be contiguous in the frozen run order")
    originals = {row["instance_id"] for row in order}
    if len(originals) != 30 or set(row["block"] for row in order) != set(range(1, 31)):
        raise ValueError("FIX-13 requires 30 task blocks")
    expected = {(group, repeat) for group in GROUPS for repeat in (1, 2)}
    for block in range(1, 31):
        rows = [row for row in order if row["block"] == block]
        if (len(rows) != 8 or {(r["group"], r["repeat"]) for r in rows} != expected
                or len({r["instance_id"] for r in rows}) != 1):
            raise ValueError("each block requires one task and all eight four-arm slots")
    if len({next(r["instance_id"] for r in order if r["block"] == b) for b in range(1, 31)}) != 30:
        raise ValueError("tasks may not recur in different blocks")
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("two original tasks map to the same accepted task")
    if complete and (originals - mapping.keys() or record.get("pending") or record.get("unfilled")):
        raise ValueError("all 30 positions must pass ingestion before execution")
    return [{**row, "task": mapping[row["instance_id"]]} for row in order if row["instance_id"] in mapping]


def load_inputs(*, complete=True):
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    record = json.loads(INGEST.read_text(encoding="utf-8"))
    slots = load_slots(plan, record, complete=complete)
    tasks = {row["id"]: row for row in yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))["tasks"]}
    if {slot["task"] for slot in slots} - tasks.keys():
        raise ValueError("accepted task is missing from manifest")
    return slots, tasks


def preflight():
    report = {"model_calls": 0, "groups": GROUPS, "expected_slots": EXPECTED_RUNS,
              "batch_cap_cny": str(BATCH_CAP), "start_threshold_cny": str(START_THRESHOLD),
              "inflight_buffer_cny": str(BUFFER), "paid_authorization": "approved 2026-11-11, conditional on full readiness",
              "scope": "configuration and slots, full Docker/identity evidence is checked before execution"}
    report["config"] = config_report()
    try:
        slots, _ = load_inputs()
        report.update(slots_ok=True, accepted_slots=len(slots))
    except (OSError, ValueError, KeyError) as exc:
        report.update(slots_ok=False, slots_error=str(exc))
    report["pass"] = report["config"]["ok"] and report["slots_ok"]
    return report


def readiness():
    """Verify the recorded no-model checks and the paid identity smoke before a real batch."""
    from src.runtime.session import source_fingerprint

    ready = json.loads(READY.read_text(encoding="utf-8"))
    if ready.get("ok") is not True or ready.get("e2e_cases") != 64 or ready.get("tasks") != 30:
        raise ValueError("complete FIX-13 readiness evidence is required")
    if ready.get("source_fingerprint") != source_fingerprint(ROOT):
        raise ValueError("runtime source changed after readiness checks")
    for name, expected in ready["sources"].items():
        if sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError("readiness source changed: " + name)
    identity = json.loads((PREFLIGHT / "identity.json").read_text(encoding="utf-8"))
    if (identity.get("ok") is not True or identity.get("model") != legacy.EXPECTED_MODEL
            or not identity.get("system_fingerprint")):
        raise ValueError("verified provider identity and fingerprint are required")
    return ready, identity


def inspect_run(path, group):
    # R/E have U's thinking and telemetry contract. Keep the actual assignment in the row.
    row = legacy.inspect_run(path, "UT" if group == "UT" else "U")
    summary = row["summary"]
    if summary.get("profile") != PROFILES[group].stem or summary.get("variant") != VARIANTS[group]:
        row["problems"].append("profile/variant differs from the assigned FIX-13 group")
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
    records = legacy.settled_records(path)
    expected_output = 32768 if group == "UT" else 8192
    for record in records:
        request = (record.get("telemetry") or {}).get("request") or {}
        if request and (request.get("model") != legacy.EXPECTED_MODEL or request.get("max_tokens") != expected_output):
            row["problems"].append("request model/output limit differs from the assigned FIX-13 group")
        if record.get("billing_stop_reason") == "billing_usage_unknown":
            row["problems"].append("billing usage unknown")
    compressed = any((record.get("telemetry") or {}).get("masked") for record in records)
    return {**row, "group": group, "compressed": compressed}


def unresolved_recovery_reserve(out_root: Path = OUT) -> Decimal:
    """Upper bound for preserved unknown reservations from a prior interruption."""
    path = out_root / "recovery-blocked.json"
    if not path.is_file():
        return Decimal(0)
    data = json.loads(path.read_text(encoding="utf-8"))
    reserve = Decimal(str(data.get("unknown_reserve_upper_bound_cny", "0")))
    if not reserve.is_finite() or reserve < 0:
        raise ValueError("invalid unresolved recovery reserve")
    return reserve


def resource_stop(row):
    reason = str(row["summary"].get("stop_reason") or "")
    return reason in ("context_overflow", "context_invalid", "admission_rejected") or bool(row.get("compressed"))


def spent(rows, prior=Decimal(0)):
    return prior + sum((r["cost"] for r in rows.values()), Decimal(0))


def forecast(rows, slots, out_root, prior=Decimal(0)):
    prediction = spent(rows, prior)
    for group in GROUPS:
        finals = [rows.get(legacy.slot_dir(out_root, s)) for s in slots if s["group"] == group]
        completed = sum(row is not None for row in finals)
        group_spend = sum((row["cost"] for row in rows.values() if row["group"] == group), Decimal(0))
        estimate = max(HISTORICAL_MEAN[group], group_spend / completed) if completed else HISTORICAL_MEAN[group]
        remaining = len(finals) - completed
        prediction += remaining * (2 * estimate + (Decimal("0.25") if group == "E" else Decimal(0)))
    return prediction


def _summary_mtime(row):
    """Sort key for the rolling infra window. A withdrawn slot can disappear between polls,
    so a missing summary must not crash the launcher."""
    path = row["path"] / "summary.json"
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def cleanup_only(row):
    """An infra error whose candidate patch was captured and only the container removal failed.

    The agent finished, the delta is on disk, and the slot can be regraded offline without
    another model call, so such an attempt must not stop the batch. Without the captured
    delta the exemption does not apply and the attempt counts as lost work.
    """
    if row["status"] != "infra_error":
        return False
    if str(row["summary"].get("stop_reason") or "") != "cleanup_failed":
        return False
    delta = row["path"] / "candidate-delta.json"
    return delta.is_file() and delta.stat().st_size > 2


def unknown_cost_only(row):
    """A run whose only telemetry problem is one request without provider usage, after the run finished.

    The agent loop ended normally and the grader produced a real verdict, so the measurement is
    intact; the ledger still holds a conservative reservation for the unaccounted request, so the
    batch total stays an upper bound. Such a slot is reported separately instead of being rerun,
    because rerunning would replace a valid measurement with a second draw.
    """
    problems = row["problems"]
    if not problems:
        return False
    unaccounted = ("billing usage unknown", "telemetry fields missing")
    if any(not any(marker in problem for marker in unaccounted) for problem in problems):
        return False
    summary = row["summary"]
    if summary.get("status") == "infra_error" or summary.get("run_error"):
        return False
    if not summary.get("grader_status"):
        return False
    ledger = row["path"] / "cny-ledger.json"
    return ledger.is_file()


def batch_halt(rows, *, mock, prior=Decimal(0), identity=None):
    attempts = list(rows.values())
    for row in attempts:
        # Apply the established isolation/grading checks per attempt. The new rolling infra
        # threshold is checked below, replacing the pilot's cumulative infra ratio.
        reason = legacy.run_checks([{**row["summary"], "status": row["status"]}])
        if reason or row["http402"]:
            return reason or "HTTP 402"
        if row["problems"] and not unknown_cost_only(row):
            return row["problems"][0]
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
            return "system fingerprint differs from the preflight identity window"
        reason = legacy.model_name_halt([r["path"] for r in attempts])
        if reason:
            return reason
    recent = sorted(attempts, key=_summary_mtime)[-10:]
    lost = [r for r in recent if r["status"] == "infra_error" and not cleanup_only(r)]
    if len(recent) >= 10 and len(lost) >= 3:
        return "at least three infrastructure errors in the last ten attempts"
    for group in GROUPS:
        done = [r for r in attempts if r["group"] == group and not r["retired"]]
        for label, count, ratio in (("CNY cap", sum(r["cny_cap_stop"] for r in done), Decimal("0.10")),
                                    ("context/admission", sum(resource_stop(r) for r in done), Decimal("0.05"))):
            if (len(done) < 20 and count >= 3) or (len(done) >= 20 and count > ratio * len(done)):
                return f"group {group}: {count}/{len(done)} {label} stops"
    if spent(rows, prior) >= START_THRESHOLD:
        return "settled spend reached 48 CNY, preserving the 12 CNY in-flight buffer"
    return None


def existing_attempts(out_root, slots, *, mock):
    rows = {}
    for slot in slots:
        final = legacy.slot_dir(out_root, slot)
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
            row = inspect_run(path, slot["group"])
            summary = row["summary"]
            if (bool(summary.get("mock")) != mock or summary.get("profile") != PROFILES[slot["group"]].stem
                    or summary.get("variant") != VARIANTS[slot["group"]]):
                raise ValueError(f"attempt does not belong to the frozen FIX-13 assignment: {path}")
            rows[path] = row
    return rows


def retire(rows, output):
    """FIX-13 permits one infra retry per slot, while the legacy pilot permitted two."""
    if legacy.infra_attempts(output) >= 1:
        return False
    return legacy.retire(rows, output)


def freeze_inputs(out_root, *, mock, prior_ledgers):
    from src.runtime.session import source_fingerprint

    paths = [PLAN, INGEST, MANIFEST, *PROFILES.values(), ROOT / "prompts/swe_b0.md", Path(__file__),
             Path(legacy.__file__), Path(config_diff.__file__), ROOT / "scripts/run_swe_task.py",
             ROOT / "scripts/run_cr2.py", ROOT / "scripts/run_a5_main.py"]
    if not mock:
        _, identity = readiness()
        if (ROOT / identity["ledger_path"]).resolve() not in {p.resolve() for p in prior_ledgers}:
            raise ValueError("the paid identity smoke ledger must be included in --prior-ledger")
        paths += [READY, PREFLIGHT / "identity.json", PREFLIGHT / "references.json", PREFLIGHT / "analysis-binding.json"]
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
    binding = {"schema": "fix13.input-binding.v1", "mock": mock, "groups": GROUPS,
               "expected_runs": EXPECTED_RUNS, "cap_cny": str(BATCH_CAP), "prior_cost_cny": str(prior),
               "sources": {str(p.resolve()): sha256(p.read_bytes()).hexdigest() for p in paths + prior_ledgers},
               "source_fingerprint": source_fingerprint(ROOT)}
    binding = json.loads(json.dumps(binding))
    target = out_root / "input-binding.json"
    if target.exists():
        if json.loads(target.read_text(encoding="utf-8")) != binding:
            raise ValueError("frozen inputs or prior spending changed, review before resuming")
    else:
        if any(out_root.rglob("cny-ledger.json")):
            raise ValueError("unbound paid attempts exist, review before creating a binding")
        target.write_text(json.dumps(binding, indent=2) + "\n", encoding="utf-8")
    return prior


async def run_one(task, group, output, *, mock):
    from scripts.run_swe_task import run_task
    return await run_task(task, PROFILES[group], output, mock=mock, variant=VARIANTS[group],
                          on_agent=legacy.tool_recorder(output, tap_model=not mock),
                          ledger_options=legacy.ledger_options(load_config(PROFILES[group])))


async def run_in_child(task, group, output, *, mock):
    spec = json.dumps({"task": task, "group": group, "output": str(output), "mock": mock}).encode()
    proc = await asyncio.create_subprocess_exec(sys.executable, str(Path(__file__).resolve()), "--child",
                                                stdin=asyncio.subprocess.PIPE, cwd=ROOT)
    process_path = output / "child-process.json"
    process_path.write_text(json.dumps({"pid": proc.pid, "finished": False}) + "\n", encoding="utf-8")
    await proc.communicate(spec)
    process_path.write_text(json.dumps({"pid": proc.pid, "finished": True, "returncode": proc.returncode}) + "\n",
                            encoding="utf-8")
    if proc.returncode:
        raise RuntimeError(f"run process exited with code {proc.returncode}")


async def run_batch(slots, tasks, *, out_root, mock, run=run_in_child, fetch=fetch_price_page,
                    prior_ledgers=()):
    if not config_report()["ok"]:
        raise ValueError("FIX-15 profiles differ from the reviewed three-arm settings")
    identity = None if mock else readiness()[1]
    # Reuse the pilot's exclusive process lock without changing that launcher's globals.
    lock = legacy.acquire_lock(out_root)
    try:
        prior = freeze_inputs(out_root, mock=mock, prior_ledgers=list(prior_ledgers))
        rows = existing_attempts(out_root, slots, mock=mock)
        recovery_reserve = unresolved_recovery_reserve(out_root)
        reference_path = out_root / "price-reference.json"
        reference = (json.loads(reference_path.read_text(encoding="utf-8")) if reference_path.exists()
                     else identity["price_snapshot"] if identity else None)
        last_price = None
        halt = batch_halt(rows, mock=mock, prior=prior + recovery_reserve, identity=identity)
        started, max_active, active = [], 0, 0
        unreconciled = []

        def price_check(*, force=False):
            nonlocal reference, last_price, halt
            if fetch is None:
                if not mock:
                    raise ValueError("real runs require a price-page check")
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
            legacy.archive_price(out_root / "price", "check", page, snapshot, at)
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
            output, group = legacy.slot_dir(out_root, slot), slot["group"]
            while not halt:
                # Check immediately before every attempt, including infra retries. No await
                # occurs between this check and incrementing active. At most three run caps
                # can remain unobserved, so settled <148 plus 12 bounds the batch by 160.
                halt = batch_halt(rows, mock=mock, prior=prior + recovery_reserve, identity=identity)
                if halt:
                    return
                price_check()
                if halt:
                    return
                active += 1
                max_active = max(max_active, active)
                started.append(slot["seq"])
                print("start " + legacy.slot_label(slot), flush=True)
                try:
                    output.mkdir(parents=True, exist_ok=False)
                    await run(tasks[slot["task"]], group, output, mock=mock)
                    row = inspect_run(output, group)
                    rows[output] = row
                except (Exception, SystemExit) as exc:
                    unreconciled.append(str(output))
                    halt = halt or f"attempt needs reconciliation: {type(exc).__name__}: {exc}"
                    return
                finally:
                    active -= 1
                halt = halt or batch_halt(rows, mock=mock, prior=prior + recovery_reserve, identity=identity)
                if row["rerun"] and not row["http402"] and not halt and retire(rows, output):
                    continue
                return

        for block in sorted({s["block"] for s in slots}):
            if halt:
                break
            queue = deque()
            for slot in (s for s in slots if s["block"] == block):
                output = legacy.slot_dir(out_root, slot)
                row = rows.get(output)
                if row is not None:
                    if not row["rerun"] or row["http402"] or not retire(rows, output):
                        continue
                queue.append(slot)

            async def worker():
                while queue and not halt:
                    await one(queue.popleft())

            await asyncio.gather(*(worker() for _ in range(PARALLEL)))
            if not halt and block % 5 == 0 and forecast(rows, slots, out_root, prior + recovery_reserve) > BATCH_CAP:
                halt = "conservative forecast exceeds 60 CNY at a completed five-block checkpoint"
            block_finished = all(legacy.slot_dir(out_root, slot) in rows for slot in slots if slot["block"] == block)
            progress = {"current_block": block, "completed_blocks": block - 1 + int(block_finished),
                        "final_runs": sum(not r["retired"] for r in rows.values()),
                        "spent_cny": None if unreconciled or recovery_reserve else str(spent(rows, prior)),
                        "known_settled_cny": str(spent(rows, prior)), "recovery_reserve_cny": str(recovery_reserve),
                        "halt_reason": halt,
                        "by_group": {g: {"final_runs": sum(r["group"] == g and not r["retired"] for r in rows.values()),
                                         "solved": sum(r["group"] == g and not r["retired"] and bool(r["summary"].get("solved"))
                                                       for r in rows.values())} for g in GROUPS}}
            (out_root / "progress.json").write_text(json.dumps(progress, indent=2) + "\n", encoding="utf-8")
            if block % 5 == 0 or halt:
                print("checkpoint " + json.dumps(progress), flush=True)
            if halt:
                break
        price_check(force=True)
        report = {"schema": "fix13.batch-report.v1", "mock": mock, "slots": len(slots),
                  "groups": GROUPS, "batch_cap_cny": str(BATCH_CAP), "start_threshold_cny": str(START_THRESHOLD),
                  "inflight_buffer_cny": str(BUFFER), "prior_cost_cny": str(prior),
                  "recovery_reserve_cny": str(recovery_reserve),
                  "spent_cny": None if unreconciled or recovery_reserve else str(spent(rows, prior)),
                  "known_settled_cny": str(spent(rows, prior)),
                  "total_with_recovery_reserve_cny": str(spent(rows, prior + recovery_reserve)), "unreconciled_attempts": unreconciled,
                  "unreconciled_reserve_cny": str(len(unreconciled) * RUN_CAP),
                  "forecast_cny": None if unreconciled else str(forecast(rows, slots, out_root, prior + recovery_reserve)), "halt_reason": halt,
                  "started_this_launch": started, "max_concurrent": max_active,
                  "final_runs": sum(not row["retired"] for row in rows.values()),
                  "by_group": {g: {"final_runs": sum(r["group"] == g and not r["retired"] for r in rows.values()),
                                     "cost_cny": str(sum((r["cost"] for r in rows.values() if r["group"] == g), Decimal(0)))}
                               for g in GROUPS}}
        (out_root / "batch-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return report
    finally:
        lock.unlink(missing_ok=True)



# FIX-15-specific bindings layered over the reused execution loop.
def config_report() -> dict:
    from scripts.rebench import run_fix15 as prepared
    return prepared.config_report()


def load_inputs(*, complete=True):
    from scripts.rebench import run_fix15 as prepared
    slots, tasks = prepared.load_slots()
    return slots, tasks


def preflight():
    from scripts.rebench import run_fix15 as prepared
    report = prepared.preflight()
    report.update({"batch_cap_cny": str(BATCH_CAP), "start_threshold_cny": str(START_THRESHOLD),
                   "inflight_buffer_cny": str(BUFFER)})
    return report


def readiness():
    from src.runtime.session import source_fingerprint
    binding = json.loads(READY.read_text(encoding="utf-8"))
    if binding.get("schema") != "fix15.input-binding.v1" or binding.get("expected_runs") != EXPECTED_RUNS:
        raise ValueError("FIX-15 preflight binding is missing or has the wrong slot count")
    if binding.get("source_fingerprint") != source_fingerprint(ROOT):
        raise ValueError("runtime source changed after FIX-15 preflight")
    for name, expected in binding.get("sources", {}).items():
        path = Path(name)
        if not path.is_file() or sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("FIX-15 binding source changed: " + name)
    e2e = json.loads((PREFLIGHT / "e2e-report.json").read_text(encoding="utf-8"))
    if e2e.get("ok") is not True or e2e.get("completed_cases") != 3 or e2e.get("provider_calls") != 0:
        raise ValueError("complete FIX-15 Docker preflight evidence is required")
    identity_path = PREFLIGHT / "identity-smoke" / "identity.json"
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    if identity.get("ok") is not True or identity.get("model") != legacy.EXPECTED_MODEL:
        raise ValueError("verified FIX-15 provider identity is required")
    if not identity.get("system_fingerprint"):
        raise ValueError("FIX-15 identity fingerprint is missing")
    return binding, identity


def inspect_run(path, group):
    row = legacy.inspect_run(path, "U")
    summary = row["summary"]
    if summary.get("profile") != PROFILES[group].stem or summary.get("variant") != VARIANTS[group]:
        row["problems"].append("profile/variant differs from the assigned FIX-15 group")
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
        if request and (request.get("model") != legacy.EXPECTED_MODEL or request.get("max_tokens") != 8192):
            row["problems"].append("request model/output limit differs from the assigned FIX-15 group")
        if record.get("billing_stop_reason") == "billing_usage_unknown":
            row["problems"].append("billing usage unknown")
    compressed = any((record.get("telemetry") or {}).get("masked") for record in legacy.settled_records(path))
    return {**row, "group": group, "compressed": compressed}


def forecast(rows, slots, out_root, prior=Decimal(0)):
    prediction = spent(rows, prior)
    for group in GROUPS:
        finals = [rows.get(legacy.slot_dir(out_root, s)) for s in slots if s["group"] == group]
        completed = sum(row is not None for row in finals)
        group_spend = sum((row["cost"] for row in rows.values() if row["group"] == group), Decimal(0))
        estimate = max(HISTORICAL_MEAN[group], group_spend / completed) if completed else HISTORICAL_MEAN[group]
        prediction += (len(finals) - completed) * estimate
    return prediction


def freeze_inputs(out_root, *, mock, prior_ledgers):
    from src.runtime.session import source_fingerprint
    binding, identity = (None, None) if mock else readiness()
    paths = [PLAN, INGEST, MANIFEST, *PROFILES.values(), ROOT / "prompts/swe_b0.md", Path(__file__),
             Path(legacy.__file__), Path(config_diff.__file__), ROOT / "scripts/run_swe_task.py",
             ROOT / "scripts/run_cr2.py", ROOT / "scripts/run_a5_main.py", READY]
    if not mock:
        identity_ledger = (ROOT / identity["ledger_path"]).resolve()
        if identity_ledger not in {p.resolve() for p in prior_ledgers}:
            raise ValueError("the paid FIX-15 identity ledger must be included as --prior-ledger")
        paths += [PREFLIGHT / "e2e-report.json", PREFLIGHT / "identity-smoke" / "identity.json",
                  PREFLIGHT / "identity-smoke" / "smoke.json"]
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
    target = out_root / "input-binding.json"
    frozen = {"schema": "fix15.formal-input-binding.v1", "mock": mock, "groups": GROUPS,
              "expected_runs": EXPECTED_RUNS, "cap_cny": str(BATCH_CAP), "prior_cost_cny": str(prior),
              "sources": {str(p.resolve()): sha256(p.read_bytes()).hexdigest() for p in paths + list(prior_ledgers)},
              "source_fingerprint": source_fingerprint(ROOT)}
    if target.exists():
        if json.loads(target.read_text(encoding="utf-8")) != frozen:
            raise ValueError("frozen FIX-15 inputs or prior spending changed")
    else:
        if (any(out_root.rglob("cny-ledger.json"))
                and not (out_root / "binding-history" / "input-binding-before-model-name-fix.json").is_file()):
            raise ValueError("unbound paid attempts exist before creating FIX-15 binding")
        out_root.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(frozen, indent=2) + "\n", encoding="utf-8")
    return prior


def batch_halt(rows, *, mock, prior=Decimal(0), identity=None):
    attempts = list(rows.values())
    for row in attempts:
        reason = legacy.run_checks([{**row["summary"], "status": row["status"]}])
        if reason or row["http402"]:
            return reason or "HTTP 402"
        if row["problems"] and not unknown_cost_only(row):
            return row["problems"][0]
        if (row["summary"].get("model") or {}).get("changed_to"):
            return "model changed during a run"
    identities = {(r["summary"].get("model") or {}).get("name") for r in attempts
                  if r["summary"].get("model")}
    if identities and identities != {legacy.EXPECTED_MODEL}:
        return "model identity changed"
    if not mock:
        names = {name for r in attempts for name in r["models"]}
        fingerprints = {fp for r in attempts for fp in r["fingerprints"]}
        if names and names != {legacy.EXPECTED_MODEL}:
            return "returned model identity changed"
        if len(fingerprints) > 1:
            return "system fingerprint changed"
        if identity and fingerprints and fingerprints != {identity["system_fingerprint"]}:
            return "system fingerprint differs from the preflight identity window"
    recent = sorted(attempts, key=_summary_mtime)[-10:]
    lost = [r for r in recent if r["status"] == "infra_error" and not cleanup_only(r)]
    if len(recent) >= 10 and len(lost) >= 3:
        return "at least three infrastructure errors in the last ten attempts"
    for group in GROUPS:
        done = [r for r in attempts if r["group"] == group and not r["retired"]]
        for label, count, ratio in (("CNY cap", sum(r["cny_cap_stop"] for r in done), Decimal("0.10")),
                                    ("context/admission", sum(resource_stop(r) for r in done), Decimal("0.05"))):
            if (len(done) < 20 and count >= 3) or (len(done) >= 20 and count > ratio * len(done)):
                return f"group {group}: {count}/{len(done)} {label} stops"
    if spent(rows, prior) >= START_THRESHOLD:
        return "settled spend reached 48 CNY, preserving the 12 CNY in-flight buffer"
    return None

def check_authorized(value):
    if value != "180,60":
        raise SystemExit("real FIX-15 execution requires --authorized 180,60")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--authorized")
    parser.add_argument("--prior-ledger", type=Path, action="append", default=[])
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        spec = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        asyncio.run(run_one(spec["task"], spec["group"], Path(spec["output"]), mock=spec["mock"]))
    elif args.preflight or args.authorized is None:
        report = preflight()
        print(json.dumps(report, indent=2))
        raise SystemExit(0 if report["pass"] else 1)
    else:
        check_authorized(args.authorized)
        slots, tasks = load_inputs()
        print(json.dumps(asyncio.run(run_batch(slots, tasks, out_root=OUT, mock=False,
                                             prior_ledgers=args.prior_ledger)), indent=2))


if __name__ == "__main__":
    main()
