"""Free offline audit of the E intervention at task level.

Reads existing FIX-13/ET run summaries only.  It makes no model, Docker, or
network calls and never rewrites source run records.  The analysis is
descriptive and post-hoc: the baseline task groups are selected from U's
observed trajectory, so the strata are not an independent test.
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "analysis" / "fix13-et"


def load_rows() -> list[dict]:
    base = json.loads((ROOT / "docs" / "analysis" / "fix13" / "runs.json").read_text(encoding="utf-8"))
    et = json.loads((ROOT / "docs" / "analysis" / "fix13-et" / "runs.json").read_text(encoding="utf-8"))
    rows = [r for r in base if r["group"] in {"U", "E", "UT"}]
    rows.extend(r for r in et if r["group"] == "ET")
    for row in rows:
        # The historical report counted candidate_snapshot_failed as valid.
        # This audit uses the corrected rule without changing the raw files.
        row["audit_valid"] = row.get("status") != "infra_error" and row.get("stop") != "candidate_snapshot_failed"
        row["found_idle"] = row.get("edit_calls", 0) == 0 and row.get("located_request") is not None
        row["high_repeat"] = (row.get("tail_repeat") or 0) >= 0.5
        located = row.get("located_request")
        first = row.get("first_edit_request")
        row["edit_delay"] = first - located if isinstance(first, (int, float)) and isinstance(located, (int, float)) else None
    return rows


def median(values):
    return statistics.median(values) if values else None


def aggregate(rows: list[dict]) -> dict:
    n = len(rows)
    valid = [r for r in rows if r["audit_valid"]]
    edited = [r for r in valid if (r.get("edit_calls") or 0) > 0]
    delays = [r["edit_delay"] for r in edited if r["edit_delay"] is not None]
    return {
        "planned_or_present": n,
        "audit_valid": len(valid),
        "solved": sum(r.get("status") == "pass" for r in valid),
        "found_idle": sum(r["found_idle"] for r in valid),
        "found_idle_rate": sum(r["found_idle"] for r in valid) / len(valid) if valid else None,
        "no_edit": sum((r.get("edit_calls") or 0) == 0 for r in valid),
        "high_repeat": sum(r["high_repeat"] for r in valid),
        "edited": len(edited),
        "median_edit_delay_after_locate": median(delays),
        "tests_invoked": sum(r.get("tests", 0) or 0 for r in valid),
    }


def task_aggregates(rows: list[dict], groups: tuple[str, ...]) -> dict[str, dict[str, dict]]:
    by_task: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if row["group"] in groups:
            by_task[row["task"]][row["group"]].append(row)

    complete = {}
    for task, arms in by_task.items():
        if all(len([r for r in arms.get(g, []) if r["audit_valid"]]) >= 2 for g in groups):
            complete[task] = {}
            for g in groups:
                valid = [r for r in arms[g] if r["audit_valid"]]
                delays = [r["edit_delay"] for r in valid if r["edit_delay"] is not None]
                complete[task][g] = {
                    "runs": len(valid),
                    "solve_rate": sum(r.get("status") == "pass" for r in valid) / len(valid),
                    "found_idle_rate": sum(r["found_idle"] for r in valid) / len(valid),
                    "high_repeat_rate": sum(r["high_repeat"] for r in valid) / len(valid),
                    "edit_rate": sum((r.get("edit_calls") or 0) > 0 for r in valid) / len(valid),
                    "median_edit_delay_after_locate": median(delays),
                    "baseline_found_idle": any(r["found_idle"] for r in valid),
                }
    return complete


def compare(tasks: dict[str, dict[str, dict]], left: str, right: str) -> dict:
    rows = []
    for task, arms in tasks.items():
        l, r = arms[left], arms[right]
        rows.append({
            "task": task,
            "baseline_found_idle": arms.get("U", {}).get("baseline_found_idle", False),
            "solve_delta": l["solve_rate"] - r["solve_rate"],
            "found_idle_delta": l["found_idle_rate"] - r["found_idle_rate"],
            "high_repeat_delta": l["high_repeat_rate"] - r["high_repeat_rate"],
            "edit_rate_delta": l["edit_rate"] - r["edit_rate"],
            "edit_delay_delta": (l["median_edit_delay_after_locate"] - r["median_edit_delay_after_locate"]
                                 if l["median_edit_delay_after_locate"] is not None
                                 and r["median_edit_delay_after_locate"] is not None else None),
        })
    return {"left": left, "right": right, "tasks": rows}


def summarize_comparison(comp: dict, *, baseline: bool | None = None) -> dict:
    rows = [r for r in comp["tasks"] if baseline is None or r["baseline_found_idle"] == baseline]
    def mean(key):
        values = [r[key] for r in rows if r[key] is not None]
        return sum(values) / len(values) if values else None
    return {
        "left": comp["left"],
        "right": comp["right"],
        "baseline_found_idle": baseline,
        "tasks": len(rows),
        "mean_solve_delta": mean("solve_delta"),
        "mean_found_idle_delta": mean("found_idle_delta"),
        "mean_high_repeat_delta": mean("high_repeat_delta"),
        "mean_edit_rate_delta": mean("edit_rate_delta"),
        "mean_edit_delay_delta": mean("edit_delay_delta"),
        "tasks_left_better": sum(r["solve_delta"] > 0 for r in rows),
        "tasks_equal": sum(r["solve_delta"] == 0 for r in rows),
        "tasks_right_better": sum(r["solve_delta"] < 0 for r in rows),
    }


def markdown(report: dict) -> str:
    lines = [
        "# E 干预的任务级离线复核",
        "",
        "这是一项只读取已有运行记录的免费复核，没有重新调用模型、Docker 或网络，也没有修改原始运行目录。",
        "历史上 `candidate_snapshot_failed` 被宽松口径算作有效；本报告把它排除。所有比较都是事后描述，不能当作新的独立验证。",
        "",
        "## 组级复核",
        "",
        "| 组别 | 可评名额 | 解出 | 定位后不编辑 | 高重复 | 有编辑 | 定位后首次编辑中位延迟 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for g, a in report["groups"].items():
        delay = "—" if a["median_edit_delay_after_locate"] is None else f"{a['median_edit_delay_after_locate']:.1f} 轮"
        lines.append(f"| {g} | {a['audit_valid']} | {a['solved']} | {a['found_idle']} | {a['high_repeat']} | "
                     f"{a['edited']} | {delay} |")

    lines += [
        "",
        "## E 与 U 的任务级比较",
        "",
        f"两组都有两次可评运行的题目数为 **{report['complete_tasks_eu']}**。下面按 U 组是否至少出现过一次“定位后不编辑”分层；这个分层来自结果本身，所以只用于解释，不能视为预先定义的检验。",
        "",
        "| U 中是否出现定位后不编辑 | 题数 | E−U 解出率差 | E−U 定位后不编辑差 | E−U 高重复差 | E−U 有编辑率差 | E−U 首次编辑延迟差 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for s in report["eu_summaries"]:
        def pct(v): return "—" if v is None else f"{100*v:+.1f} 个百分点"
        delay = "—" if s["mean_edit_delay_delta"] is None else f"{s['mean_edit_delay_delta']:+.1f} 轮"
        label = "是" if s["baseline_found_idle"] is True else "否" if s["baseline_found_idle"] is False else "不分层"
        lines.append(f"| {label} | {s['tasks']} | {pct(s['mean_solve_delta'])} | {pct(s['mean_found_idle_delta'])} | "
                     f"{pct(s['mean_high_repeat_delta'])} | {pct(s['mean_edit_rate_delta'])} | {delay} |")

    lines += [
        "",
        "## 如何理解",
        "",
        "- 这里的“定位后不编辑”是运行级指标：运行中出现了定位请求，但没有任何编辑调用；它不是对模型内部状态的直接观察。",
        "- `tests` 字段只表示调用过测试命令的次数，不记录每次测试是否通过。因此本报告使用最终 `pass` 作为解出结果，不把它写成独立的“验证成功率”。",
        "- E 同时改变了可见结果解释、上下文结构和后续提示，任务级差异可以告诉我们收益集中在哪里，不能单独证明哪一部分机制造成收益。",
        "- 如果 E 的优势主要集中在 U 的 `found_idle` 题，下一轮才值得做“结果解释内容”与“只保留可见提示结构”的小型消融；如果优势分散且稳定，项目可以直接把 E 作为候选低成本默认策略，停止机制实验。",
        "",
        "## 原始证据",
        "",
        "- `docs/analysis/fix13/runs.json`：U、E、UT 运行记录。",
        "- `docs/analysis/fix13-et/runs.json`：ET 运行记录。",
        "- `docs/analysis/fix13-et/FIX-13-ET-RESULTS.zh-CN.md`：已有组级 ET 报告。",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    rows = load_rows()
    groups = {g: aggregate([r for r in rows if r["group"] == g]) for g in ("U", "E", "UT", "ET")}
    eu = task_aggregates(rows, ("U", "E"))
    eut = task_aggregates(rows, ("U", "E", "UT"))
    # Use the larger U/E-complete set for the E-vs-U descriptive comparison.
    comp = compare(eu, "E", "U")
    report = {
        "schema": "fix13.e-task-audit.v1",
        "groups": groups,
        "complete_tasks_eu": len(eu),
        "complete_tasks_u_e_ut": len(eut),
        "eu_summaries": [summarize_comparison(comp, baseline=b) for b in (True, False, None)],
        "eu_task_rows": comp["tasks"],
        "evidence_boundary": "post-hoc descriptive analysis on the fixed 60-task development corpus; no independent validation",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "E-TASK-AUDIT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "E-TASK-AUDIT.zh-CN.md").write_text(markdown(report), encoding="utf-8")
    print(json.dumps({"complete_tasks_eu": len(eu), "summaries": report["eu_summaries"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
