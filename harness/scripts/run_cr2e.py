"""CR-2e-a: development check of the T2 configuration (docs/retrieval/CR-2-RESULTS.zh-CN.md).

T2 = configs/retrieval/cr2e-t2.yaml (cr2-t.yaml with the system prompt prompts/swe_cr2e.md) plus the v2
description of retrieve_code, set by the on_agent hook after the agent is built and before the first model call.
Six development tasks x 2 repeats. These tasks are for this check only, never for a later comparison.

    run_cr2e.py --select                    write the task list docs/retrieval/cr2e-dev-tasks.json (no model calls)
    run_cr2e.py --mock                      wiring check with a scripted model under runs/cr2e-mock (not counted)
    run_cr2e.py --authorized dev,20         the 12 real runs of round 1 under runs/cr2e/round1
    run_cr2e.py --round 2 --authorized dev,20   round 2 (the one allowed change), runs/cr2e/round2, its own 20 CNY cap
    run_cr2e.py --runs-file                 rewrite the round's runs file from existing summaries

The functions of run_cr2.py are imported, not copied or changed. Infra rule: CR-2-FREEZE section 6 and r3.
A finished round is never run again: with a complete runs file the runner refuses the real mode.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import build_a6_attempts as attempts_lib  # noqa: E402
from scripts.handoff_sha import handoff_sha256  # noqa: E402
from scripts.run_a5_main import (  # noqa: E402
    PARALLEL,
    PROFILE,
    _display_path,
    materialize_runner_task,
    retire_infra,
    run_checks,
    spent,
)
from scripts.run_a6_retry import first_user_message  # noqa: E402
from scripts.run_cr2 import (  # noqa: E402
    CALLS_FILE,
    CR2MockLLM,
    TOOLS_FILE,
    _now,
    _sha,
    acquire_lock,
    agent_snapshot_failure,
    counted_status,
    model_name_halt,
    model_name_tap,
    model_recorder,
    needs_rerun,
    pool_tasks,
    returned_fingerprints,
    returned_models,
)
from scripts.run_swe_task import build_prompt, run_task  # noqa: E402

DEV_SEED = 20261029
TOP_HARD = 4
RANDOM_PICKS = 2
REPEATS = (1, 2)
CAP_CNY = Decimal("20")
AUTHORIZED = "dev,20"
VARIANT = "CR2E-T2"
T2_PROFILE = ROOT / "configs" / "retrieval" / "cr2e-t2.yaml"
T2_PROMPT = ROOT / "prompts" / "swe_cr2e.md"
CR2_ANALYSIS = ROOT / "docs" / "retrieval" / "cr2-analysis.json"
TASKS_FILE = ROOT / "docs" / "retrieval" / "cr2e-dev-tasks.json"
TASKS_SCHEMA = "cr2e.dev-tasks.v1"
RUNS_SCHEMA = "cr2e.runs.v1"
DESCRIPTION_V2 = (
    "Semantic search over the original repository at the base revision (before any edits made in this session). "
    "Describe the bug or behavior in words; returns the files most likely involved, each with the symbols and line "
    "ranges to read first. Use search_code instead for exact names or error strings. Does not return code: read the "
    "listed ranges with read_file."
)
# One entry per round. Contract 2: if round 1 misses the threshold, one change is allowed and runs as round 2.
# Round 2 changes only the retrieve_code line of the system prompt; the description stays v2.
ROUNDS = {
    1: {"profile": T2_PROFILE, "prompt": T2_PROMPT, "description": DESCRIPTION_V2},
    2: {"profile": ROOT / "configs" / "retrieval" / "cr2e-t2-r2.yaml", "prompt": ROOT / "prompts" / "swe_cr2e-r2.md",
        "description": DESCRIPTION_V2},
}


def out_root_for(round_no: int, mock: bool) -> Path:
    return ROOT / "runs" / ("cr2e-mock" if mock else "cr2e") / ("round" + str(round_no))


def runs_path_for(round_no: int, mock: bool) -> Path:
    if mock:
        return out_root_for(round_no, True) / "cr2e-runs.json"
    return ROOT / "docs" / "retrieval" / ("cr2e-runs-round" + str(round_no) + ".json")


# --- task selection --------------------------------------------------------------------------------

def select_tasks(per_run: list[dict], pool_ids: list[str]) -> dict:
    """Contract 2: the 4 tasks with the largest CR-2 N mean first gold step (ties by instance_id), then 2 drawn
    with random.Random(20261029).sample from the other tasks sorted by instance_id."""
    steps: dict[str, list[int]] = {}
    for row in per_run:
        if row["group"] == "n":
            steps.setdefault(row["task_id"], []).append(row["first_gold_step"])
    if sorted(steps) != sorted(pool_ids):
        raise SystemExit("CR-2 N runs do not cover the pool")
    means = {task: sum(values) / len(values) for task, values in steps.items()}
    hard = sorted(means, key=lambda task: (-means[task], task))[:TOP_HARD]
    rest = sorted(task for task in pool_ids if task not in hard)
    drawn = random.Random(DEV_SEED).sample(rest, RANDOM_PICKS)
    return {"hardest": [{"task_id": t, "n_mean_first_gold_step": means[t]} for t in hard],
            "random": drawn, "tasks": hard + drawn}


def write_task_list(destination: Path = TASKS_FILE) -> dict:
    if destination.exists():
        raise SystemExit(_display_path(destination) + " exists; the development tasks are chosen once")
    pool = [task["id"] for task in pool_tasks()]
    analysis = json.loads(CR2_ANALYSIS.read_text(encoding="utf-8"))
    chosen = select_tasks(analysis["per_run"], pool)
    payload = {"schema": TASKS_SCHEMA, "seed": DEV_SEED, "source": _display_path(CR2_ANALYSIS),
               "source_sha256": handoff_sha256(CR2_ANALYSIS),
               "rule": "4 largest CR-2 N mean first gold step (ties by instance_id); 2 by random.Random(20261029)"
                       ".sample from the other 52 sorted by instance_id",
               "use": "CR-2e development check only; never used in a later comparison",
               **chosen}
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    return payload


def load_task_list(path: Path = TASKS_FILE) -> list[str]:
    if not path.is_file():
        raise SystemExit(_display_path(path) + " missing; run --select first")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != TASKS_SCHEMA or len(payload["tasks"]) != TOP_HARD + RANDOM_PICKS:
        raise SystemExit("bad task list " + _display_path(path))
    return payload["tasks"]


def all_slots(task_ids) -> list[tuple[str, int]]:
    return [(task, k) for k in REPEATS for task in sorted(task_ids)]


def slot_dir(out_root: Path, slot) -> Path:
    return out_root / slot[0] / ("r" + str(slot[1]))


def slot_label(slot) -> str:
    return slot[0] + "/r" + str(slot[1])


# --- T2 agent and injection check ------------------------------------------------------------------

def system_text(prompt: Path) -> str:
    """The system prompt as the factory sends it (file text, stripped)."""
    return prompt.read_text(encoding="utf-8").strip()


def set_description(agent, retrieval, description: str) -> None:
    """Replace the retrieve_code instance with one carrying the given description (contract 0).
    The registry is a name -> tool dict, so the tool keeps its place in the schema list."""
    from src.tools.retrieve import RetrieveCodeTool

    if agent.tools.get("retrieve_code") is None:
        raise SystemExit("the T2 agent has no retrieve_code tool")
    agent.tools._tools["retrieve_code"] = RetrieveCodeTool(retrieval, description=description)


def t2_hook(output: Path, retrieval, description: str, *, tap_model: bool):
    """on_agent: set the description, then record what the agent will send, before the first model call."""
    def hook(agent) -> None:
        set_description(agent, retrieval, description)
        schemas = agent.tools.schemas(agent.enabled_tools)
        tool = agent.tools.get("retrieve_code")
        payload = {"tools": [s["function"]["name"] for s in schemas],
                   "schemas_sha256": _sha(json.dumps(schemas, ensure_ascii=False)),
                   "retrieve_code_description": tool.description,
                   "system_prompt_sha256": _sha(agent.system_prompt or "")}
        (output / TOOLS_FILE).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        if tap_model:
            model_name_tap(agent.llm, model_recorder(output))
    return hook


def expected_agent(round_cfg: dict) -> dict:
    """Tool names, schema sha256 and system prompt T2 must send: b0-v2 + retrieve_code with the round's description."""
    import tempfile

    from src.harness.factory import build_agent, load_config
    from src.tools.retrieve import TaskRetrieval

    with tempfile.TemporaryDirectory() as tmp:
        b0 = load_config(PROFILE)
        b0["llm"]["api_key"] = "names-only"
        b0_names = [s["function"]["name"] for s in build_agent(b0, Path(tmp)).tools.schemas(None)]
        config = load_config(round_cfg["profile"])
        config["llm"]["api_key"] = "names-only"
        retrieval = TaskRetrieval(None, Path(tmp) / CALLS_FILE)
        agent = build_agent(config, Path(tmp), retrieval=retrieval)
        set_description(agent, retrieval, round_cfg["description"])
        schemas = agent.tools.schemas(agent.enabled_tools)
    names = [s["function"]["name"] for s in schemas]
    if names != b0_names + ["retrieve_code"]:
        raise SystemExit("T2 tools are not b0-v2 + retrieve_code: " + json.dumps(names))
    text = system_text(round_cfg["prompt"])
    if agent.system_prompt != text:
        raise SystemExit("T2 system prompt differs from " + _display_path(round_cfg["prompt"]))
    return {"tools": names, "schemas_sha256": _sha(json.dumps(schemas, ensure_ascii=False)),
            "description": round_cfg["description"], "system_prompt": text, "system_prompt_sha256": _sha(text)}


def first_system_message(run_dir: Path) -> str | None:
    events = run_dir / "events.jsonl"
    if not events.is_file():
        return None
    try:
        with events.open(encoding="utf-8") as handle:
            for line in handle:
                if '"state.saved"' not in line:
                    continue
                event = json.loads(line)
                if event.get("event_type") != "state.saved":
                    continue
                payload = attempts_lib._payload(run_dir, event.get("payload") or {})
                for message in payload.get("messages") or []:
                    if message.get("role") == "system":
                        content = message.get("content")
                        return content if isinstance(content, str) else None
    except (ValueError, KeyError, TypeError):
        return None
    return None


def injection_problem(run_dir: Path, summary: dict, prompt: str, want: dict) -> str | None:
    """Contract 2: first message = build_prompt(task); system prompt = swe_cr2e.md; tools = b0-v2 + retrieve_code;
    retrieve_code description = v2."""
    if summary.get("prompt_sha256") != _sha(prompt):
        return "prompt_sha256 differs from build_prompt(task)"
    if first_user_message(run_dir) != prompt:
        return "first user message differs from build_prompt(task)"
    if first_system_message(run_dir) != want["system_prompt"]:
        return "system message in events.jsonl differs from the T2 system prompt"
    record = run_dir / TOOLS_FILE
    if not record.is_file():
        return TOOLS_FILE + " missing"
    tools = json.loads(record.read_text(encoding="utf-8"))
    if tools.get("tools") != want["tools"]:
        return "tool list is not b0-v2 + retrieve_code"
    if tools.get("retrieve_code_description") != want["description"]:
        return "retrieve_code description is not the round's description"
    if tools.get("system_prompt_sha256") != want["system_prompt_sha256"]:
        return "system prompt sha256 differs"
    if tools.get("schemas_sha256") != want["schemas_sha256"]:
        return "tool schema sha256 differs from the expected T2 schemas"
    calls = run_dir / CALLS_FILE
    if calls.is_file():
        crashed = [row.get("call_id") for row in map(json.loads, filter(str.strip, calls.read_text(encoding="utf-8").splitlines()))
                   if row.get("traceback")]
        if crashed:
            return "retrieve_code crashed on " + ", ".join(map(str, crashed))
    return None


# --- runs file and completion guard -----------------------------------------------------------------

def run_row(task: str, k: int, run_dir: Path, summary: dict) -> dict:
    no_candidate = agent_snapshot_failure(summary)
    return {
        "task_id": task, "k": k, "run_dir": _display_path(run_dir), "mock": summary.get("mock"),
        "profile": summary.get("profile"), "variant": summary.get("variant"),
        "status": counted_status(summary), "solved": False if no_candidate else summary.get("solved"),
        "no_candidate": no_candidate, "snapshot_error": summary.get("snapshot_error"),
        "model_calls": summary.get("model_calls"), "cost_cny": summary.get("cost_cny"),
        "stop_reason": summary.get("stop_reason"), "prompt_sha256": summary.get("prompt_sha256"),
        "rerun_reason": needs_rerun(run_dir, summary),
        "returned_models": returned_models(run_dir), "system_fingerprints": returned_fingerprints(run_dir),
    }


def write_runs(task_ids, round_no: int, *, mock: bool, out_root: Path | None = None,
               destination: Path | None = None) -> Path:
    out_root = out_root or out_root_for(round_no, mock)
    rows = []
    for slot in all_slots(task_ids):
        run_dir = slot_dir(out_root, slot)
        path = run_dir / "summary.json"
        if path.is_file():
            rows.append(run_row(slot[0], slot[1], run_dir, json.loads(path.read_text(encoding="utf-8"))))
    payload = {"schema": RUNS_SCHEMA, "round": round_no, "mock": mock, "variant": VARIANT,
               "profile": _display_path(ROUNDS[round_no]["profile"]),
               "system_prompt": _display_path(ROUNDS[round_no]["prompt"]),
               "description_sha256": _sha(ROUNDS[round_no]["description"]),
               "slots_expected": len(all_slots(task_ids)), "runs": rows}
    destination = destination or runs_path_for(round_no, mock)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    print("runs", _display_path(destination), len(rows), handoff_sha256(destination), flush=True)
    return destination


def runs_complete(path: Path) -> bool:
    """A runs file with a row for every slot and no slot left to rerun."""
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload["runs"]
        return len(rows) == payload["slots_expected"] and not any(row.get("rerun_reason") for row in rows)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def refuse_finished(path: Path) -> None:
    """Contract 2: twice in CR-2 the same command started again after the runner had exited normally."""
    if runs_complete(path):
        raise SystemExit(_display_path(path) + " is complete; this round is finished and is not run again")


# --- running ---------------------------------------------------------------------------------------

def halt_reason(out_root: Path, cap: Decimal = CAP_CNY) -> str | None:
    rows = [{**json.loads(p.read_text(encoding="utf-8"))} for p in sorted(out_root.glob("*/r*/summary.json"))]
    reason = run_checks([{**row, "status": counted_status(row)} for row in rows])
    if reason:
        return reason
    if spent(out_root) >= cap:
        return "ledger reached the cap of " + str(cap) + " CNY"
    return None


async def run_round(slots, *, round_no: int, mock: bool, out_root: Path, prepared: dict, want: dict, service,
                    run=run_task) -> dict:
    lock = acquire_lock(out_root)
    try:
        return await _run_round(slots, round_no=round_no, mock=mock, out_root=out_root, prepared=prepared,
                                want=want, service=service, run=run)
    finally:
        lock.unlink(missing_ok=True)


async def _run_round(slots, *, round_no: int, mock: bool, out_root: Path, prepared: dict, want: dict, service,
                     run) -> dict:
    from src.tools.retrieve import TaskRetrieval

    started_at = _now()
    description = ROUNDS[round_no]["description"]
    profile = ROUNDS[round_no]["profile"]
    pending, kept_infra, injection = [], [], {}
    for slot in slots:
        output = slot_dir(out_root, slot)
        summary_path = output / "summary.json"
        if summary_path.is_file():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            if bool(summary.get("mock")) != mock or summary.get("variant") != VARIANT:
                raise SystemExit(_display_path(summary_path) + " is not a " + VARIANT + (" mock" if mock else "") + " run")
            if not needs_rerun(output, summary):
                continue
            if not retire_infra(output):
                kept_infra.append(_display_path(output))
                continue
        elif output.exists():
            raise SystemExit(_display_path(output) + ": directory has no summary.json (an interrupted run)")
        pending.append((slot, output))
    await asyncio.to_thread(service.warm)
    state = {"halt": halt_reason(out_root)}
    semaphore = asyncio.Semaphore(PARALLEL)
    finished_real: list[Path] = []

    async def one(slot, output):
        task = slot[0]
        prompt = build_prompt(prepared[task])
        async with semaphore:
            while True:
                if state["halt"]:
                    return None
                try:
                    output.mkdir(parents=True, exist_ok=True)
                    index = await asyncio.to_thread(service.task_index, prepared[task]["repo"], prepared[task]["base_commit"])
                    retrieval = TaskRetrieval(index, output / CALLS_FILE)
                    summary = await run(prepared[task], profile, output, mock=mock, variant=VARIANT, retrieval=retrieval,
                                        mock_llm=CR2MockLLM() if mock else None,
                                        on_agent=t2_hook(output, retrieval, description, tap_model=not mock))
                except (Exception, SystemExit) as exc:
                    state["halt"] = state["halt"] or ("run crashed: " + slot_label(slot) + " " + type(exc).__name__
                                                      + ": " + str(exc)[:300])
                    return None
                problem = injection_problem(output, summary, prompt, want)
                injection[slot_label(slot)] = problem
                if problem and counted_status(summary) != "infra_error":
                    state["halt"] = state["halt"] or ("injection check failed: " + slot_label(slot) + ": " + problem)
                state["halt"] = state["halt"] or halt_reason(out_root)
                why = needs_rerun(output, summary)
                if not why:
                    if not mock and len(finished_real) < 3:
                        finished_real.append(output)
                        if len(finished_real) == 3:
                            state["halt"] = state["halt"] or model_name_halt(finished_real)
                    return summary
                print("rerun " + slot_label(slot) + ": " + why, flush=True)
                if state["halt"] or not retire_infra(output):
                    kept_infra.append(_display_path(output))
                    return summary

    results = await asyncio.gather(*(one(*item) for item in pending))
    started = [slot for (slot, _), row in zip(pending, results) if row is not None]
    report = {
        "round": round_no, "mock": mock, "started_at": started_at, "finished_at": _now(), "parallel": PARALLEL,
        "cap_cny": str(CAP_CNY), "slots": [slot_label(slot) for slot in slots], "halt_reason": state["halt"],
        "started": len(started), "not_started": len(results) - len(started), "skipped_finished": len(slots) - len(pending),
        "injection": injection, "infra_kept": sorted(set(kept_infra)),
        "infra_retired": sorted(_display_path(p) for p in out_root.glob("*/r*.infra*")),
        "returned_models": sorted({m for slot in started for m in returned_models(slot_dir(out_root, slot))}),
        "spent_cny": str(spent(out_root)),
    }
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def check_authorized(value: str | None) -> None:
    if value != AUTHORIZED:
        raise SystemExit("real runs need --authorized " + AUTHORIZED + " (CR-2e contract section 2)")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--select", action="store_true", help="write the development task list")
    parser.add_argument("--mock", action="store_true", help="scripted model; two slots under runs/cr2e-mock")
    parser.add_argument("--round", type=int, default=1, choices=sorted(ROUNDS))
    parser.add_argument("--runs-file", action="store_true", help="rewrite the runs file from existing summaries")
    parser.add_argument("--authorized", help="required without --mock: " + AUTHORIZED)
    args = parser.parse_args()
    if args.select:
        print(json.dumps(write_task_list(), indent=2))
        return
    task_ids = load_task_list()
    if args.runs_file:
        write_runs(task_ids, args.round, mock=args.mock)
        return
    runs_path = runs_path_for(args.round, args.mock)
    if not args.mock:
        check_authorized(args.authorized)
        refuse_finished(runs_path)
    slots = all_slots(task_ids)
    if args.mock:
        slots = slots[:2]
    pool = {task["id"]: task for task in pool_tasks()}
    prepared = {task: materialize_runner_task(pool[task]) for task in task_ids}
    want = expected_agent(ROUNDS[args.round])
    from src.retrieval.serve import RetrievalService

    service = RetrievalService.from_freeze()
    out_root = out_root_for(args.round, args.mock)
    report = asyncio.run(run_round(slots, round_no=args.round, mock=args.mock, out_root=out_root, prepared=prepared,
                                   want=want, service=service))
    print(json.dumps({k: v for k, v in report.items() if k != "slots"}, indent=2), flush=True)
    if not report["halt_reason"]:
        write_runs(task_ids, args.round, mock=args.mock, out_root=out_root, destination=runs_path)
    else:
        raise SystemExit("stopped: " + report["halt_reason"])


if __name__ == "__main__":
    main()
