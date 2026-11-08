"""Batch launcher of the unleash pilot (docs/analysis/UNLEASH-PILOT-DESIGN.zh-CN.md sections 4, 8.4 and 9).

Starts the 180 runs of run_order (scripts/rebench/out/unleash_pilot_tasks.json) in seq order, PARALLEL at a time,
each with its group's profile (configs/rebench/unleash-{a,u,ut}.yaml) on the ingested task of its slot
(scripts/rebench/out/unleash_pilot_ingest.json; a replacement inherits the original's slots). Every run gets the
same CNY ledger: 4 CNY per run, provider usage settled at the peak price, the profile's call cap.

Before each new run the launcher waits for the DeepSeek off-peak period (revision R5), then checks every stop
rule of section 9 and the batch cap and mid-run forecast of section 8.4. Runs in flight always finish. Infra
reruns follow CR-2 (section 4.3), except HTTP 402, which stops the batch. The official price page is read and
archived before the first run, at most hourly between runs, and at the end; any change stops the batch.

    run_unleash.py --preflight                 slots, manifest, config diff, price page (no model calls)
    run_unleash.py --mock [--limit N]          scripted model (no network), output under runs/unleash-pilot-mock
    run_unleash.py --authorized 180,250        the real batch; needs the author's approval
"""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import os
import re
import sys
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.rebench.pilot_ingest import task_map  # noqa: E402
from scripts.run_a5_main import MAX_INFRA_RERUNS, infra_attempts, retire_infra, run_checks  # noqa: E402
from scripts.run_cr2 import (  # noqa: E402
    _pid_alive,
    counted_status,
    model_name_halt,
    needs_rerun,
    provider_failure,
    returned_fingerprints,
    returned_models,
    tool_recorder,
)
from src.policy.cny_budget import BEIJING, PEAK_HOURS, price_period  # noqa: E402

GROUPS = ("A", "U", "UT")
PROFILES = {g: ROOT / "configs" / "rebench" / f"unleash-{g.lower()}.yaml" for g in GROUPS}
VARIANTS = {g: "UNLEASH-" + g for g in GROUPS}
PLAN = ROOT / "scripts" / "rebench" / "out" / "unleash_pilot_tasks.json"
MANIFEST = ROOT / "configs" / "rebench" / "unleash-pilot-tasks.yaml"
INGEST = ROOT / "scripts" / "rebench" / "out" / "unleash_pilot_ingest.json"
LOGS = ROOT / "runs" / "unleash-logs"
PARALLEL = 3
EXPECTED_RUNS = 180
BATCH_CAP = Decimal("250")
RUN_CAP_CNY = "4.000"
FORECAST_BLOCKS = 5
FORECAST_FACTOR = Decimal(6) * Decimal("1.14")
CAP_STOP_RATIO = Decimal("0.10")
CAP_STOP_GROUPS = ("U", "UT")
ADMISSION_GROUPS = ("U", "UT")
THINKING = {"A": ("disabled", None), "U": ("disabled", None), "UT": ("enabled", "high")}
# Section 5.6. reasoning_tokens may be null: the provider sends no details when thinking is off.
TELEMETRY_FIELDS = ("request", "admission_input_tokens", "prompt_tokens", "prompt_cache_hit_tokens",
                    "prompt_cache_miss_tokens", "completion_tokens", "reasoning_tokens", "masked", "replaced_count")
NULLABLE = ("reasoning_tokens",)
# Design table 2.1. The profiles read ${DEEPSEEK_MODEL:deepseek-flash}, so an environment override would switch
# every group at once and stay consistent between runs; the name itself has to be checked.
EXPECTED_MODEL = "deepseek-flash"
PRICE_URL = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing"
EXPECTED_PRICE = {"model_version": "DeepSeek-V4.1-Flash-1017", "peak_cny_per_million": ["0.04", "2", "8"]}
PRICE_RECHECK_SECONDS = 3600
LOCK_FILE = "run_unleash.lock"


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# --- slots ---------------------------------------------------------------------------------------

def load_slots(plan: dict, record: dict) -> list[dict]:
    """run_order rows in seq order, each with the ingested task that fills its original's slot."""
    mapping = task_map(record)
    slots = []
    for row in sorted(plan["run_order"], key=lambda r: r["seq"]):
        if row["group"] not in GROUPS:
            raise SystemExit("unknown group in run order: " + str(row["group"]))
        task = mapping.get(row["instance_id"])
        if task is None:
            raise SystemExit("run order task has no ingested task: " + row["instance_id"])
        slots.append({**row, "task": task})
    if [s["seq"] for s in slots] != list(range(1, len(slots) + 1)):
        raise SystemExit("run order seq is not 1..N")
    return slots


def slot_dir(out_root: Path, slot: dict) -> Path:
    return out_root / slot["task"] / slot["group"] / ("r" + str(slot["repeat"]))


def slot_label(slot: dict) -> str:
    return f"{slot['seq']:03d} {slot['task']}/{slot['group']}/r{slot['repeat']}"


def ledger_options(config: dict) -> dict:
    """The same CNY ledger for every group: per-run cap at the peak price, the profile's own call cap."""
    return {"settlement": "provider_usage", "time_of_day": False, "run_cap_cny": RUN_CAP_CNY,
            "max_calls": int(config["runtime"]["budget"]["max_model_calls"])}


# --- off-peak ------------------------------------------------------------------------------------

def seconds_until_off_peak(moment: datetime) -> float:
    """0 in the off-peak period (holidays are not modelled, so a holiday weekday counts as peak)."""
    local = moment.astimezone(BEIJING)
    if price_period(local) == "off_peak":
        return 0.0
    for start, end in PEAK_HOURS:
        if start <= local.hour < end:
            return (local.replace(hour=end, minute=0, second=0, microsecond=0) - local).total_seconds()
    return 0.0


async def wait_off_peak(clock, sleep, log=print) -> float:
    waited = 0.0
    while True:
        left = seconds_until_off_peak(clock())
        if left <= 0:
            return waited
        if waited == 0:
            log(f"peak period: waiting {left:.0f} s for off-peak")
        step = min(left + 1, 600.0)
        await sleep(step)
        waited += step


# --- price page ----------------------------------------------------------------------------------

def price_snapshot(page: str) -> dict:
    """The model details and price table of the official page, as text, plus the fields the ledger relies on."""
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S)
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()
    start, end = text.find("模型细节"), text.find("网页搜索与抓取价格", text.find("模型细节") + 1)
    section = text[start:end].strip() if start >= 0 and end > start else ""
    version = re.search(r"模型版本\s+(\S+)", section)
    peak = [m.group(1) for m in re.finditer(r"高峰时段\s+([0-9.]+)\s*元", section)][:3]
    return {"section": section, "section_sha256": sha256(section.encode("utf-8")).hexdigest(),
            "model_version": version.group(1) if version else None, "peak_cny_per_million": peak}


def price_problem(snapshot: dict, reference: dict | None = None) -> str | None:
    if not snapshot.get("section"):
        return "price page: model and price section not found"
    for key, want in EXPECTED_PRICE.items():
        if snapshot.get(key) != want:
            return f"price page: {key} is {snapshot.get(key)}, expected {want}"
    if reference is not None and snapshot["section_sha256"] != reference["section_sha256"]:
        return "price page: model and price section changed since the start of the batch"
    return None


def fetch_price_page(url: str = PRICE_URL) -> str:
    import httpx

    response = httpx.get(url, timeout=60, follow_redirects=True)
    response.raise_for_status()
    return response.text


def archive_price(directory: Path, label: str, page: str, snapshot: dict, at: datetime) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = at.astimezone(BEIJING).strftime("%Y%m%dT%H%M%S")
    (directory / f"price-{label}-{stamp}.html").write_text(page, encoding="utf-8")
    path = directory / f"price-{label}-{stamp}.json"
    path.write_text(json.dumps({"url": PRICE_URL, "fetched_at": at.isoformat(), "page_sha256":
                                sha256(page.encode("utf-8")).hexdigest(), **snapshot}, ensure_ascii=False, indent=2)
                    + "\n", encoding="utf-8")
    return path


# --- run inspection ------------------------------------------------------------------------------

def settled_records(run_dir: Path) -> list[dict]:
    """record of every model.settled event; payloads moved to session artifacts are read back."""
    path = run_dir / "events.jsonl"
    if not path.is_file():
        return []
    records = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if '"model.settled"' not in line:
                continue
            event = json.loads(line)
            if event.get("event_type") != "model.settled":
                continue
            payload = event.get("payload") or {}
            ref = payload.get("artifact_ref")
            if ref is not None:
                artifact = run_dir / "session" / "artifacts" / ref["path"]
                payload = json.loads(artifact.read_bytes()) if artifact.is_file() else {"record": {"artifact_missing": True}}
            records.append(payload.get("record") or {})
    return records


def telemetry_problems(group: str, records: list[dict]) -> list[str]:
    """Per-request stop rules of section 9.2 and revision R1, on the run's settled requests."""
    problems, answered, reasoned = [], 0, 0
    thinking, effort = THINKING[group]
    for index, record in enumerate(records):
        where = f"request {index + 1}"
        if record.get("artifact_missing"):
            problems.append(where + ": settled event artifact missing")
            continue
        if record.get("billing_stop_reason") == "billing_usage_unknown":
            problems.append(where + ": billing usage unknown")
        if record.get("error_kind") == "http" and record.get("status_code") == 400:
            problems.append(where + ": HTTP 400 (reasoning_content replay or request rejected)")
        if record.get("success") is False:
            continue
        answered += 1
        telemetry = record.get("telemetry")
        if not isinstance(telemetry, dict):
            problems.append(where + ": no request telemetry")
            continue
        missing = [k for k in TELEMETRY_FIELDS if k not in telemetry or (telemetry[k] is None and k not in NULLABLE)]
        if missing:
            problems.append(where + ": telemetry fields missing: " + ", ".join(missing))
            continue
        request = telemetry["request"]
        if request.get("thinking") != thinking or request.get("reasoning_effort") != effort:
            problems.append(f"{where}: sent thinking={request.get('thinking')} effort={request.get('reasoning_effort')},"
                            f" expected {thinking}/{effort}")
        reasoning = telemetry["reasoning_tokens"] or 0
        reasoned += reasoning > 0
        if group in ("A", "U") and reasoning > 0:
            problems.append(f"{where}: {reasoning} reasoning tokens with thinking disabled")
        if group in ADMISSION_GROUPS and telemetry["prompt_tokens"] > telemetry["admission_input_tokens"]:
            problems.append(f"{where}: provider prompt_tokens {telemetry['prompt_tokens']} above the admission "
                            f"estimate {telemetry['admission_input_tokens']}")
    if group == "UT" and answered and not reasoned:
        problems.append("no request of the run had reasoning tokens > 0 (thinking not in effect)")
    return problems


def inspect_run(run_dir: Path, group: str) -> dict:
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    ledger_path = run_dir / "cny-ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.is_file() else {}
    records = settled_records(run_dir)
    return {"path": run_dir, "group": group, "retired": "." in run_dir.name, "summary": summary,
            "status": counted_status(summary), "cost": Decimal(str(summary.get("cost_cny") or "0")),
            "models": returned_models(run_dir), "fingerprints": returned_fingerprints(run_dir),
            "requests": len(records), "problems": telemetry_problems(group, records),
            "http402": provider_failure(run_dir) == "http 402", "cny_cap_stop": bool(ledger.get("rejections")),
            "rerun": needs_rerun(run_dir, summary)}


# --- stop rules ----------------------------------------------------------------------------------

def forecast(rows: dict, slots: list[dict], out_root: Path) -> Decimal | None:
    """Section 8.4: once every slot of the first FORECAST_BLOCKS blocks has a final run, spend so far x 6 x 1.14."""
    first = [s for s in slots if s["block"] <= FORECAST_BLOCKS]
    finals = [rows.get(slot_dir(out_root, s)) for s in first]
    if not first or any(r is None or (r["rerun"] and not r["http402"] and infra_attempts(r["path"]) < MAX_INFRA_RERUNS)
                        for r in finals):
        return None
    return sum((r["cost"] for r in rows.values()), Decimal(0)) * FORECAST_FACTOR


def batch_halt(rows: dict, slots: list[dict], out_root: Path, *, mock: bool, cap: Decimal = BATCH_CAP,
               finished_order: list[Path] | None = None) -> str | None:
    """Every stop rule of sections 8.4 and 9, on all attempts so far (retired infra runs included)."""
    attempts = list(rows.values())
    reason = run_checks([{**r["summary"], "status": r["status"]} for r in attempts])
    if reason:
        return reason
    for r in attempts:
        if r["http402"]:
            return "HTTP 402 (insufficient balance): " + rel(r["path"])
    for r in attempts:
        if r["problems"]:
            return rel(r["path"]) + ": " + r["problems"][0]
    identities = set()
    for r in attempts:
        model = r["summary"].get("model") or {}
        if model.get("changed_to"):
            return "model changed during a run: " + rel(r["path"])
        if model:
            identities.add((model.get("name"), model.get("base_url_host")))
    if len(identities) > 1:
        return "model name or base_url host differs between runs: " + json.dumps(sorted(identities, key=str))
    if identities and next(iter(identities))[0] != EXPECTED_MODEL:
        return f"configured model {next(iter(identities))[0]} is not {EXPECTED_MODEL}"
    if not mock:
        names = {n for r in attempts for n in r["models"]}
        if len(names) > 1:
            return "returned model names differ: " + ", ".join(sorted(names))
        if names and names != {EXPECTED_MODEL}:
            return f"returned model name {next(iter(names))} is not {EXPECTED_MODEL}"
        prints = {f for r in attempts for f in r["fingerprints"]}
        if len(prints) > 1:
            return "returned system_fingerprint changed: " + ", ".join(sorted(prints))
        halt = model_name_halt(finished_order or [])
        if halt:
            return halt
    for group in CAP_STOP_GROUPS:
        done = [r for r in attempts if r["group"] == group and not r["retired"] and r["status"] != "infra_error"]
        stops = sum(1 for r in done if r["cny_cap_stop"])
        if done and Decimal(stops) > CAP_STOP_RATIO * len(done):
            return f"group {group}: {stops} of {len(done)} completed runs stopped at the per-run CNY cap"
    spent = sum((r["cost"] for r in attempts), Decimal(0))
    if spent >= cap:
        return f"spend {spent} CNY reached the batch cap {cap}"
    predicted = forecast(rows, slots, out_root)
    if predicted is not None and predicted > cap:
        return f"forecast {predicted:.3f} CNY after {FORECAST_BLOCKS} blocks exceeds the batch cap {cap}"
    return None


# --- running -------------------------------------------------------------------------------------

def acquire_lock(out_root: Path) -> Path:
    out_root.mkdir(parents=True, exist_ok=True)
    path = out_root / LOCK_FILE
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            pid = int(json.loads(path.read_text(encoding="utf-8"))["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            raise SystemExit("unreadable lock " + rel(path) + "; check for another launcher, then delete it")
        state = "holds" if _pid_alive(pid) else "left a stale"
        raise SystemExit(f"launcher pid {pid} {state} lock {rel(path)}; not starting")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"pid": os.getpid(), "started_at": now_utc().isoformat()}) + "\n")
    return path


def resumable(summary: dict, group: str, *, mock: bool) -> str | None:
    if bool(summary.get("mock")) != mock:
        return "summary is a mock run" if summary.get("mock") else "summary is a real run"
    if summary.get("profile") != PROFILES[group].stem or summary.get("variant") != VARIANTS[group]:
        return f"profile/variant is {summary.get('profile')}/{summary.get('variant')}"
    return None


def existing_attempts(out_root: Path, slots: list[dict]) -> dict:
    rows = {}
    for slot in slots:
        final = slot_dir(out_root, slot)
        for path in [*sorted(final.parent.glob(final.name + ".infra*")), final]:
            if (path / "summary.json").is_file():
                rows[path] = inspect_run(path, slot["group"])
    return rows


async def run_one(task: dict, group: str, output: Path, *, mock: bool) -> dict:
    from scripts.run_swe_task import run_task
    from src.harness.factory import load_config

    config = load_config(PROFILES[group])
    llm = None
    if mock:
        from scripts.rebench.unleash_mock_e2e import scripted_llm

        llm = scripted_llm(group, task, config)[0]
    return await run_task(task, PROFILES[group], output, mock=mock, variant=VARIANTS[group], mock_llm=llm,
                          on_agent=tool_recorder(output, tap_model=not mock), ledger_options=ledger_options(config))


def child_command() -> list[str]:
    return [sys.executable, str(Path(__file__).resolve()), "--child"]


async def run_in_child(task: dict, group: str, output: Path, *, mock: bool) -> None:
    """Session recording writes synchronously, so runs sharing one event loop stall each other's Docker calls
    past their cleanup timeouts. Each run therefore gets its own interpreter."""
    spec = json.dumps({"task": task, "group": group, "output": str(output), "mock": mock}).encode("utf-8")
    proc = await asyncio.create_subprocess_exec(*child_command(), stdin=asyncio.subprocess.PIPE, cwd=ROOT)
    try:
        await proc.communicate(spec)
    except asyncio.CancelledError:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise
    if proc.returncode:
        raise RuntimeError(f"run process exited with code {proc.returncode}")


async def run_batch(slots: list[dict], tasks: dict, *, out_root: Path, mock: bool, run=None, mock_llm=None,
                    clock=now_utc, sleep=asyncio.sleep, fetch=fetch_price_page, wait_for_off_peak: bool = True,
                    cap: Decimal = BATCH_CAP, label: str = "batch", log=print) -> dict:
    """tasks: ingested task id -> manifest row. Without run, every slot runs in its own process (run_in_child).
    An injected run is awaited in this process; mock_llm(group, task, config) then returns its scripted client."""
    from src.harness.factory import load_config

    lock = acquire_lock(out_root)
    try:
        started_at = clock()
        prices = {"start": None, "checks": [], "end": None, "fetch_errors": []}
        reference = None
        if fetch is not None:
            page = fetch()
            reference = price_snapshot(page)
            prices["start"] = rel(archive_price(out_root / "price", "start", page, reference, started_at))
            problem = price_problem(reference)
            if problem:
                raise SystemExit("not starting: " + problem)
        rows = existing_attempts(out_root, slots)
        refused, queue, kept_infra = [], deque(), []
        for slot in slots:
            output = slot_dir(out_root, slot)
            row = rows.get(output)
            if row is not None:
                problem = resumable(row["summary"], slot["group"], mock=mock)
                if problem:
                    refused.append(rel(output) + ": " + problem)
                    continue
                if not row["rerun"] or row["http402"]:
                    continue
                if not retire(rows, output):
                    kept_infra.append(rel(output))
                    continue
            elif output.exists():
                refused.append(rel(output) + ": directory has no summary.json (an interrupted run)")
                continue
            queue.append(slot)
        if refused:
            raise SystemExit("existing runs do not belong to these slots; not overwriting:\n" + "\n".join(refused))
        finished_order: list[Path] = []
        state = {"halt": batch_halt(rows, slots, out_root, mock=mock, cap=cap, finished_order=finished_order),
                 "price_checked": clock(), "started": [], "active": 0, "max_active": 0}

        def price_recheck():
            if fetch is None or (clock() - state["price_checked"]).total_seconds() < PRICE_RECHECK_SECONDS:
                return
            state["price_checked"] = clock()
            try:
                page = fetch()
            except Exception as exc:  # noqa: BLE001 - an unreachable page is logged, not a stop
                prices["fetch_errors"].append(f"{clock().isoformat()} {type(exc).__name__}")
                return
            snapshot = price_snapshot(page)
            problem = price_problem(snapshot, reference)
            prices["checks"].append({"at": clock().isoformat(), "section_sha256": snapshot["section_sha256"],
                                     "problem": problem})
            if problem:
                archive_price(out_root / "price", "changed", page, snapshot, clock())
                state["halt"] = state["halt"] or problem

        async def worker():
            while queue and not state["halt"]:
                if wait_for_off_peak:
                    await wait_off_peak(clock, sleep, log)
                price_recheck()
                if state["halt"] or not queue:
                    return
                slot = queue.popleft()
                await one(slot)

        async def one(slot):
            group, output = slot["group"], slot_dir(out_root, slot)
            config = load_config(PROFILES[group])
            while True:
                state["started"].append(slot["seq"])
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
                log("start " + slot_label(slot))
                try:
                    output.mkdir(parents=True, exist_ok=True)
                    task = tasks[slot["task"]]
                    if run is None:
                        await run_in_child(task, group, output, mock=mock)
                    else:
                        await run(task, PROFILES[group], output, mock=mock, variant=VARIANTS[group],
                                  mock_llm=mock_llm(group, task, config) if mock_llm else None,
                                  on_agent=tool_recorder(output, tap_model=not mock),
                                  ledger_options=ledger_options(config))
                except (Exception, SystemExit) as exc:
                    state["halt"] = state["halt"] or f"run crashed: {slot_label(slot)} {type(exc).__name__}: {str(exc)[:300]}"
                    return
                finally:
                    state["active"] -= 1
                row = inspect_run(output, group)
                rows[output] = row
                if not mock:
                    finished_order.append(output)
                state["halt"] = state["halt"] or batch_halt(rows, slots, out_root, mock=mock, cap=cap,
                                                            finished_order=finished_order)
                if row["rerun"] and not row["http402"] and not state["halt"]:
                    log("rerun " + slot_label(slot) + ": " + row["rerun"])
                    if retire(rows, output):
                        continue
                    kept_infra.append(rel(output))
                return

        await asyncio.gather(*(worker() for _ in range(PARALLEL)))
        finished_at = clock()
        if fetch is not None:
            try:
                page = fetch()
                snapshot = price_snapshot(page)
                prices["end"] = rel(archive_price(out_root / "price", "end", page, snapshot, finished_at))
                prices["end_problem"] = price_problem(snapshot, reference)
            except Exception as exc:  # noqa: BLE001
                prices["fetch_errors"].append(f"end {type(exc).__name__}")
        report = summarize(rows, slots, out_root, mock=mock, cap=cap, label=label, state=state, prices=prices,
                           kept_infra=kept_infra, started_at=started_at, finished_at=finished_at)
        (out_root / (label + "-report.json")).write_text(json.dumps(report, indent=2, default=str) + "\n",
                                                         encoding="utf-8")
        return report
    finally:
        lock.unlink(missing_ok=True)


def retire(rows: dict, output: Path) -> bool:
    """Move an infra run aside as r<k>.infra<n> (CR-2 rule); its record follows it."""
    retired = output.with_name(output.name + ".infra" + str(infra_attempts(output) + 1))
    if not retire_infra(output):
        return False
    if output in rows:
        row = rows.pop(output)
        rows[retired] = {**row, "path": retired, "retired": True}
    return True


def summarize(rows, slots, out_root, *, mock, cap, label, state, prices, kept_infra, started_at, finished_at) -> dict:
    finals = {s["seq"]: rows.get(slot_dir(out_root, s)) for s in slots}
    done = {seq: r for seq, r in finals.items() if r is not None}
    by_group = {}
    for group in GROUPS:
        group_rows = [r for r in rows.values() if r["group"] == group]
        group_final = [r for r in done.values() if r["group"] == group]
        by_group[group] = {"final_runs": len(group_final),
                           "solved": sum(1 for r in group_final if r["summary"].get("solved")),
                           "cny_cap_stops": sum(1 for r in group_final if r["cny_cap_stop"]),
                           "infra_retired": sum(1 for r in group_rows if r["retired"]),
                           "retired_cost_cny": str(sum((r["cost"] for r in group_rows if r["retired"]), Decimal(0))),
                           "cost_cny": str(sum((r["cost"] for r in group_rows), Decimal(0)))}
    predicted = forecast(rows, slots, out_root)
    return {"schema": "unleash-pilot.batch-report.v1", "label": label, "mock": mock,
            "started_at": started_at.isoformat(), "finished_at": finished_at.isoformat(), "parallel": PARALLEL,
            "batch_cap_cny": str(cap), "run_cap_cny": RUN_CAP_CNY, "halt_reason": state["halt"],
            "slots": len(slots), "final_runs": len(done), "started_this_launch": len(state["started"]),
            "max_concurrent": state["max_active"], "spent_cny": str(sum((r["cost"] for r in rows.values()), Decimal(0))),
            "forecast_cny": None if predicted is None else str(predicted), "by_group": by_group,
            "infra_kept": sorted(set(kept_infra)), "price": prices,
            "returned_models": sorted({n for r in rows.values() for n in r["models"]}),
            "system_fingerprints": sorted({f for r in rows.values() for f in r["fingerprints"]})}


# --- cli -----------------------------------------------------------------------------------------

def load_inputs(plan_path: Path = PLAN, ingest_path: Path = INGEST, manifest_path: Path = MANIFEST):
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    record = json.loads(ingest_path.read_text(encoding="utf-8"))
    slots = load_slots(plan, record)
    tasks = {t["id"]: t for t in yaml.safe_load(manifest_path.read_text(encoding="utf-8"))["tasks"]}
    missing = sorted({s["task"] for s in slots} - set(tasks))
    if missing:
        raise SystemExit("ingested tasks missing from the manifest: " + ", ".join(missing))
    return plan, record, slots, tasks


def preflight() -> dict:
    from scripts.rebench import unleash_config_diff

    report = {"model_calls": 0}
    try:
        _, record, slots, tasks = load_inputs()
        report.update(slots=len(slots), tasks=len({s["task"] for s in slots}), substitutions=len(record["substitutions"]),
                      unfilled=record.get("unfilled"), pending=record.get("pending"),
                      memory_2gib=sorted(t for t in {s["task"] for s in slots}
                                         if tasks[t].get("memory_bytes") == 2 * 1024**3))
        report["slots_ok"] = len(slots) == EXPECTED_RUNS and not record.get("unfilled") and not record.get("pending")
    except (Exception, SystemExit) as exc:
        report.update(slots_ok=False, slots_error=f"{type(exc).__name__}: {str(exc)[:300]}")
    os.environ["LLM_API_KEY"] = os.environ.get("LLM_API_KEY") or "preflight-placeholder"
    configs = {"b0-v2": unleash_config_diff.load(unleash_config_diff.B0_V2),
               **{g: unleash_config_diff.load(p) for g, p in unleash_config_diff.PROFILES.items()}}
    report["config_diff_ok"] = unleash_config_diff.check(configs)["ok"]
    report["models"] = sorted({configs[g]["llm.model"] for g in unleash_config_diff.PROFILES})
    report["model_ok"] = report["models"] == [EXPECTED_MODEL]
    try:
        page = fetch_price_page()
        snapshot = price_snapshot(page)
        report["price_archive"] = rel(archive_price(LOGS / "price", "preflight", page, snapshot, now_utc()))
        report["price_problem"] = price_problem(snapshot)
    except Exception as exc:  # noqa: BLE001
        report["price_problem"] = "price page unreadable: " + type(exc).__name__
    report["off_peak_now"] = seconds_until_off_peak(now_utc()) == 0
    report["pass"] = bool(report["slots_ok"] and report["config_diff_ok"] and report["model_ok"]
                          and report["price_problem"] is None)
    return report


def check_authorized(value: str | None) -> None:
    if value is None:
        raise SystemExit("real runs need --authorized RUNS,CAP as approved by the project author")
    try:
        runs, cap = value.split(",")
        ok = int(runs) == EXPECTED_RUNS and Decimal(cap) == BATCH_CAP
    except ValueError:
        ok = False
    if not ok:
        raise SystemExit(f"--authorized {value} does not match {EXPECTED_RUNS} runs, cap {BATCH_CAP} in this launcher")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--mock", action="store_true", help="scripted model, no network; output under runs/unleash-pilot-mock")
    parser.add_argument("--limit", type=int, default=None, help="with --mock: only the first N slots")
    parser.add_argument("--wait-off-peak", action="store_true", help="with --mock: wait for off-peak like a real run")
    parser.add_argument("--authorized", default=None)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        spec = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        asyncio.run(run_one(spec["task"], spec["group"], Path(spec["output"]), mock=spec["mock"]))
        return
    if args.preflight:
        report = preflight()
        LOGS.mkdir(parents=True, exist_ok=True)
        (LOGS / "preflight.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
        raise SystemExit(0 if report["pass"] else 1)
    _, _, slots, tasks = load_inputs()
    if args.mock:
        os.environ["LLM_API_KEY"] = "mock-placeholder"
        report = asyncio.run(run_batch(slots[:args.limit] if args.limit else slots, tasks,
                                       out_root=ROOT / "runs" / "unleash-pilot-mock", mock=True,
                                       fetch=None, wait_for_off_peak=args.wait_off_peak, label="mock"))
    else:
        check_authorized(args.authorized)
        report = asyncio.run(run_batch(slots, tasks, out_root=ROOT / "runs" / "unleash-pilot", mock=False,
                                       label="pilot"))
    print(json.dumps({k: report[k] for k in ("halt_reason", "final_runs", "spent_cny", "forecast_cny")}, indent=2))


if __name__ == "__main__":
    main()
