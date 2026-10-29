"""CR-2 analysis (contract section 4). Frozen before any real run; reads gold files only here, after the runs.

    analyze_cr2.py                         docs/retrieval/cr2-runs.json -> docs/retrieval/cr2-analysis.json
    analyze_cr2.py --runs X --out Y        any runs file, e.g. the mock one
    analyze_cr2.py --gold-in-prefetch      how many tasks have a gold file in the frozen prefetch top 5

Step rules (unit-tested in tests/test_analyze_cr2.py):
- The step of an event is the number of model.started events before it in events.jsonl, i.e. model calls started
  so far, counted from 1 and including provider retry attempts (the budget counts calls the same way).
- A gold touch is a tool.intent for read_file, edit_file, search_replace or write_file whose path argument,
  normalized, is a gold file. The intent counts whatever the tool result was.
- Path normalization: backslashes to slashes, a leading /workspace/ and leading ./ removed, posix normpath.
- Never touched = 41 (one past the 40-call budget).

Paired per-task differences: P - N, T - N and O - N (r1, contract section 7.3; O is an upper-bound reference,
not a retrieval method). solve_by_early_touch splits each group's runs by whether a gold file was touched
within the first 10 steps.

r3 (contract 9.2): runs with status delta_too_large (the candidate snapshot failed on the workspace delta size) are
task results: their step metrics count, solved counts as false, and delta_too_large_by_group reports them.

r4 (contract 10.1, 10.2):
- A final run whose rerun_reason is set counts as a task result when the runner kept it after its retirements ran
  out (infra_kept). The list comes from the runs file, or else from the runner report given with --report. Any
  other final run with a rerun_reason still stops the analysis.
- Sensitivity analyses, fixed before any result was read, each redo the paired P - N, T - N and O - N for the
  first gold step and solved with the main method: S1 without the infra_kept runs; S2 without
  scikit-learn__scikit-learn-12585; S3 only runs whose system_fingerprints are exactly [bc19ba97...]; S4 per slot
  the first attempt (retired r<k>.infra<n> in order, then the final run) whose status is not infra_error, with the
  runner's frozen counted_status, so a delta_too_large attempt is a task result as in the main analysis.
"""
from __future__ import annotations

import argparse
import json
import posixpath
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SEED = 20261026
N_BOOT = 10000
NEVER = 41
EARLY = 10
TOUCH_TOOLS = frozenset({"read_file", "edit_file", "search_replace", "write_file"})
GROUPS = ("n", "p", "t", "o")
PAIRS = (("p_minus_n", "p", "n"), ("t_minus_n", "t", "n"), ("o_minus_n", "o", "n"))
RUNS = ROOT / "docs" / "retrieval" / "cr2-runs.json"
OUT = ROOT / "docs" / "retrieval" / "cr2-analysis.json"
PREFETCH = ROOT / "docs" / "retrieval" / "cr2-prefetch.json"
CALLS_FILE = "retrieval-calls.jsonl"
DELTA_STATUS = "delta_too_large"
PAIRED_METRICS = ("first_gold_step", "gold_within_10", "solved", "model_calls", "cost_cny", "first_message_bytes")
SENSITIVITY_METRICS = ("first_gold_step", "solved")
S2_TASK = "scikit-learn__scikit-learn-12585"
S3_FINGERPRINT = "bc19ba97cbe016479903222bb1578db6"
INFRA_STATUS = "infra_error"


def normalize_path(path) -> str:
    text = str(path or "").replace("\\", "/").strip()
    if text.startswith("/workspace/"):
        text = text[len("/workspace/"):]
    while text.startswith("./"):
        text = text[2:]
    text = posixpath.normpath(text) if text else ""
    return "" if text == "." else text


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def steps(events: list[dict]):
    """Yield (step, event) with step = model calls started so far."""
    started = 0
    for event in events:
        if event.get("event_type") == "model.started":
            started += 1
        yield started, event


def _intent(event: dict) -> tuple[str | None, dict]:
    payload = event.get("payload") or {}
    return payload.get("name"), payload.get("arguments") or {}


def first_gold_step(events: list[dict], gold) -> int:
    gold = {normalize_path(path) for path in gold}
    for step, event in steps(events):
        if event.get("event_type") != "tool.intent":
            continue
        name, arguments = _intent(event)
        if name in TOUCH_TOOLS and normalize_path(arguments.get("path")) in gold:
            return step
    return NEVER


def retrieve_stats(events: list[dict], calls: list[dict], gold) -> dict:
    gold = {normalize_path(path) for path in gold}
    call_steps = [step for step, event in steps(events)
                  if event.get("event_type") == "tool.intent" and _intent(event)[0] == "retrieve_code"]
    returned = {normalize_path(item["path"]) for row in calls for item in row.get("files") or []}
    return {
        "retrieve_calls": len(call_steps),
        "first_retrieve_step": call_steps[0] if call_steps else None,
        "gold_in_retrieval": bool(returned & gold),
        "retrieval_log_rows": len(calls),
    }


def run_metrics(row: dict, events: list[dict], gold, calls: list[dict] | None = None,
                first_message: str | None = None) -> dict:
    step = first_gold_step(events, gold)
    out = {
        "task_id": row["task_id"], "group": row["group"], "k": row["k"],
        "first_gold_step": step,
        "gold_within_10": step <= EARLY,
        # Contract 9.2: a delta_too_large run left no candidate, so it counts as unsolved; its steps still count.
        "solved": bool(row.get("solved")) and row.get("status") != DELTA_STATUS,
        "delta_too_large": row.get("status") == DELTA_STATUS,
        "model_calls": row.get("model_calls"),
        "cost_cny": float(row["cost_cny"]) if row.get("cost_cny") is not None else None,
        "stop_reason": row.get("stop_reason"),
        "first_message_bytes": len(first_message.encode("utf-8")) if first_message is not None else None,
    }
    if row["group"] == "t":
        out.update(retrieve_stats(events, calls or [], gold))
    return out


def task_means(rows: list[dict], metric: str) -> dict[str, dict[str, float]]:
    """task -> group -> mean of the metric over that task's repeats (missing values skipped)."""
    acc: dict[tuple[str, str], list[float]] = {}
    for row in rows:
        value = row.get(metric)
        if value is None:
            continue
        acc.setdefault((row["task_id"], row["group"]), []).append(float(value))
    out: dict[str, dict[str, float]] = {}
    for (task, group), values in acc.items():
        out.setdefault(task, {})[group] = sum(values) / len(values)
    return out


def paired(means: dict[str, dict[str, float]], a: str, b: str, tasks=None) -> dict:
    """Mean per-task a - b with the per-task bootstrap 95% interval (seed 20261026, 10000 resamples)."""
    from src.retrieval.metrics import paired_bootstrap

    keys = sorted(task for task, groups in means.items() if a in groups and b in groups
                  and (tasks is None or task in tasks))
    if not keys:
        return {"n_tasks": 0, "diff": None, "lo": None, "hi": None}
    result = paired_bootstrap([means[t][a] for t in keys], [means[t][b] for t in keys], n_boot=N_BOOT, seed=SEED)
    return {"n_tasks": len(keys), **result}


def group_mean(means: dict[str, dict[str, float]], group: str, tasks=None):
    values = [groups[group] for task, groups in means.items() if group in groups and (tasks is None or task in tasks)]
    return sum(values) / len(values) if values else None


def gold_in_prefetch(prefetch_rows: dict, gold_by_task: dict) -> dict[str, bool]:
    return {task: bool({normalize_path(f["path"]) for f in row["files"]} & {normalize_path(g) for g in gold_by_task[task]})
            for task, row in prefetch_rows.items() if task in gold_by_task}


def solve_by_early_touch(per_run: list[dict]) -> dict:
    """Per group, the solve rate of runs that touched a gold file within the first 10 steps and of those that did not."""
    out = {}
    for group in GROUPS:
        rows = [r for r in per_run if r["group"] == group]
        out[group] = {}
        for name, flag in (("touched_within_10", True), ("not_touched_within_10", False)):
            part = [r for r in rows if r["gold_within_10"] is flag]
            solved = sum(1 for r in part if r["solved"])
            out[group][name] = {"runs": len(part), "solved": solved, "rate": solved / len(part) if part else None}
    return out


def infra_kept_runs(payload: dict, report: Path | None = None) -> set[str]:
    """run_dir of the slots the runner kept after their retirements ran out (r4, contract 10.1)."""
    if "infra_kept" in payload:
        kept = payload["infra_kept"]
    elif report is not None:
        kept = json.loads(report.read_text(encoding="utf-8"))["infra_kept"]
    else:
        kept = []
    return {str(path).replace("\\", "/") for path in kept}


def blocking_reruns(runs: list[dict], kept: set[str]) -> list[str]:
    return sorted(row["run_dir"] for row in runs if row.get("rerun_reason") and row["run_dir"] not in kept)


def attempt_dirs(final_dir: Path) -> list[Path]:
    """Retired r<k>.infra<n> directories in retirement order, then the final r<k>."""
    retired = [path for path in final_dir.parent.glob(final_dir.name + ".infra*")
               if path.name[len(final_dir.name + ".infra"):].isdigit()]
    retired.sort(key=lambda path: int(path.name[len(final_dir.name + ".infra"):]))
    return retired + [final_dir]


def attempt_row(final_row: dict, run_dir: Path, root: Path) -> dict | None:
    """The runs-file row write_runs() would give this attempt, or None without a summary."""
    from scripts.run_cr2 import agent_snapshot_failure, counted_status

    path = run_dir / "summary.json"
    if not path.is_file():
        return None
    summary = json.loads(path.read_text(encoding="utf-8"))
    no_candidate = agent_snapshot_failure(summary)
    return {
        "task_id": final_row["task_id"], "group": final_row["group"], "k": final_row["k"],
        "run_dir": run_dir.relative_to(root).as_posix(), "status": counted_status(summary),
        "solved": False if no_candidate else summary.get("solved"), "no_candidate": no_candidate,
        "model_calls": summary.get("model_calls"), "cost_cny": summary.get("cost_cny"),
        "stop_reason": summary.get("stop_reason"),
    }


def first_non_infra_runs(runs: list[dict], per_run: list[dict], gold_by_task: dict, root: Path) -> tuple[list[dict], dict]:
    """S4: per slot the first attempt whose status is not infra_error, scored like a final run."""
    final_metrics = {row["run_dir"]: metrics for row, metrics in zip(runs, per_run)}
    chosen, from_retired, dropped = [], Counter(), []
    for row in runs:
        final_dir = root / row["run_dir"]
        pick = None
        for run_dir in attempt_dirs(final_dir):
            candidate = row if run_dir == final_dir else attempt_row(row, run_dir, root)
            if candidate is not None and candidate.get("status") != INFRA_STATUS:
                pick = (run_dir, candidate)
                break
        if pick is None:
            dropped.append(row["run_dir"])
            continue
        run_dir, candidate = pick
        if run_dir == final_dir:
            chosen.append(final_metrics[row["run_dir"]])
            continue
        from_retired[row["group"]] += 1
        calls = _read_jsonl(run_dir / CALLS_FILE) if row["group"] == "t" else None
        metrics = run_metrics(candidate, _read_jsonl(run_dir / "events.jsonl"), gold_by_task[row["task_id"]], calls)
        chosen.append({**metrics, "run_dir": candidate["run_dir"]})
    info = {"slots_from_retired_run": {g: from_retired.get(g, 0) for g in GROUPS},
            "slots_without_non_infra_run": sorted(dropped)}
    return chosen, info


def compare(per_run: list[dict]) -> dict:
    """The main paired method on a subset of runs. A task without runs left in a group drops out of that pair."""
    out = {
        "runs": len(per_run),
        "runs_by_group": {g: sum(1 for r in per_run if r["group"] == g) for g in GROUPS},
        "tasks": len({r["task_id"] for r in per_run}),
    }
    for metric in SENSITIVITY_METRICS:
        means = task_means(per_run, metric)
        out[metric] = {"group_means": {g: group_mean(means, g) for g in GROUPS},
                       "paired": {name: paired(means, a, b) for name, a, b in PAIRS}}
    return out


def sensitivity(runs: list[dict], per_run: list[dict], gold_by_task: dict, root: Path) -> dict:
    s4_runs, s4_info = first_non_infra_runs(runs, per_run, gold_by_task, root)
    return {
        "s1_without_infra_kept": compare([r for r in per_run if not r["infra_kept"]]),
        "s2_without_" + S2_TASK: compare([r for r in per_run if r["task_id"] != S2_TASK]),
        "s3_only_fingerprint_" + S3_FINGERPRINT: compare([r for r in per_run if r["system_fingerprints"] == [S3_FINGERPRINT]]),
        "s4_first_non_infra_attempt": {**compare(s4_runs), **s4_info},
    }


def analyze(runs: list[dict], gold_by_task: dict, prefetch_rows: dict, root: Path = ROOT, repeats: int = 5,
            infra_kept: set[str] | frozenset = frozenset()) -> dict:
    from scripts.run_a6_retry import first_user_message

    per_run = []
    for row in runs:
        run_dir = root / row["run_dir"]
        events = _read_jsonl(run_dir / "events.jsonl")
        calls = _read_jsonl(run_dir / CALLS_FILE) if row["group"] == "t" else None
        metrics = run_metrics(row, events, gold_by_task[row["task_id"]], calls, first_user_message(run_dir))
        metrics.update(run_dir=row["run_dir"], infra_kept=row["run_dir"] in infra_kept,
                       system_fingerprints=list(row.get("system_fingerprints") or []))
        per_run.append(metrics)
    counts = Counter((r["task_id"], r["group"]) for r in per_run)
    tasks = sorted({r["task_id"] for r in per_run})
    report = {
        "seed": SEED, "n_boot": N_BOOT, "never": NEVER, "early_steps": EARLY,
        "runs": len(per_run),
        "tasks": len(tasks),
        "complete": all(counts.get((t, g)) == repeats for t in tasks for g in GROUPS),
        "runs_by_group": {g: sum(1 for r in per_run if r["group"] == g) for g in GROUPS},
        "delta_too_large_by_group": {g: sum(1 for r in per_run if r["group"] == g and r["delta_too_large"]) for g in GROUPS},
        "infra_kept": sorted(r["run_dir"] for r in per_run if r["infra_kept"]),
        "group_means": {}, "paired": {}, "stop_reasons": {},
    }
    for metric in PAIRED_METRICS:
        means = task_means(per_run, metric)
        report["group_means"][metric] = {g: group_mean(means, g) for g in GROUPS}
        report["paired"][metric] = {name: paired(means, a, b) for name, a, b in PAIRS}
    for group in GROUPS:
        report["stop_reasons"][group] = dict(sorted(Counter(r["stop_reason"] for r in per_run if r["group"] == group).items(),
                                                    key=lambda kv: str(kv[0])))
    t_runs = [r for r in per_run if r["group"] == "t"]
    if t_runs:
        firsts = [r["first_retrieve_step"] for r in t_runs if r["first_retrieve_step"] is not None]
        report["t_only"] = {
            "runs": len(t_runs),
            "share_with_call": sum(1 for r in t_runs if r["retrieve_calls"]) / len(t_runs),
            "calls_per_run": sum(r["retrieve_calls"] for r in t_runs) / len(t_runs),
            "first_call_step_mean": sum(firsts) / len(firsts) if firsts else None,
            "first_call_steps": dict(sorted(Counter(firsts).items())),
            "share_gold_in_any_result": sum(1 for r in t_runs if r["gold_in_retrieval"]) / len(t_runs),
        }
    hit = gold_in_prefetch(prefetch_rows, gold_by_task)
    steps_means = task_means(per_run, "first_gold_step")
    split = {}
    for name, flag in (("gold_in_top5", True), ("gold_not_in_top5", False)):
        subset = {task for task, value in hit.items() if value is flag}
        split[name] = {
            "tasks_in_pool": len(subset),
            "n_mean": group_mean(steps_means, "n", subset),
            "p_mean": group_mean(steps_means, "p", subset),
            "p_minus_n": paired(steps_means, "p", "n", subset),
        }
    report["p_only_first_gold_step"] = split
    report["solve_by_early_touch"] = solve_by_early_touch(per_run)
    report["sensitivity"] = sensitivity(runs, per_run, gold_by_task, root)
    report["per_run"] = per_run
    return report


def load_gold(task_ids) -> dict[str, tuple[str, ...]]:
    from src.retrieval import dataset, labels

    queries = [q for q in dataset.load_query_views() if q.instance_id in set(task_ids)]
    views = labels.load_label_views(queries)
    missing = sorted(set(task_ids) - set(views))
    if missing:
        raise SystemExit("no labels for " + ", ".join(missing))
    return {task: views[task].gold_files for task in task_ids}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=Path, default=RUNS)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--prefetch", type=Path, default=PREFETCH)
    parser.add_argument("--gold-in-prefetch", action="store_true", help="only count tasks with gold in the prefetch top 5")
    parser.add_argument("--report", type=Path, help="runner report with infra_kept, used when the runs file has none")
    args = parser.parse_args()
    prefetch_rows = {row["task"]: row for row in json.loads(args.prefetch.read_text(encoding="utf-8"))["tasks"]}
    if args.gold_in_prefetch:
        hit = gold_in_prefetch(prefetch_rows, load_gold(sorted(prefetch_rows)))
        print(json.dumps({"tasks": len(hit), "gold_in_top5": sum(hit.values())}))
        return
    payload = json.loads(args.runs.read_text(encoding="utf-8"))
    runs = payload["runs"]
    kept = infra_kept_runs(payload, args.report)
    blocking = blocking_reruns(runs, kept)
    if blocking:
        raise SystemExit("runs file still has slots whose final run needs a rerun: " + ", ".join(blocking))
    gold = load_gold(sorted(prefetch_rows))
    report = analyze(runs, gold, prefetch_rows, repeats=payload.get("repeats", 5), infra_kept=kept)
    if args.report is not None:
        report["report_file"] = args.report.relative_to(ROOT).as_posix() if args.report.is_relative_to(ROOT) else str(args.report)
    report["runs_file"] = args.runs.relative_to(ROOT).as_posix() if args.runs.is_relative_to(ROOT) else str(args.runs)
    report["scope"] = payload.get("scope")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: v for k, v in report.items() if k != "per_run"}, indent=2))


if __name__ == "__main__":
    main()
