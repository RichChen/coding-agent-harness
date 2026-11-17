"""Offline FIX-13 accounting and pre-specified task-paired comparisons. No provider or Docker calls."""
from __future__ import annotations

import json
import re
import statistics
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.rebench import run_fix13 as launch  # noqa: E402
from scripts.analysis.fix12_no_edit import classify  # noqa: E402
from scripts.analysis.fix12_trajectory.extract import assistant_messages  # noqa: E402
from scripts.analysis.reread._load import payload, read_events  # noqa: E402

OUT = ROOT / "docs/analysis/fix13"
REFS = ROOT / "runs/fix13-preflight/references.json"
TEST = re.compile(r"\bpytest\b|\bunittest\b|\btox\b|\bnox\b")


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def paired(values, costs, strata):
    """Task vectors remain intact within strata for every bootstrap draw."""
    rng = np.random.default_rng(20261117)
    indices = np.concatenate([rng.choice(np.flatnonzero(strata == s), (10000, sum(strata == s)))
                              for s in sorted(set(strata))], axis=1)
    draws = values[indices].mean(axis=1)
    cost_draws = costs[indices].sum(axis=1)
    solve_draws = (2 * values[indices]).sum(axis=1)
    result = []
    sign_rng = np.random.default_rng(20261118)
    signs = sign_rng.choice((-1, 1), (100000, len(values)))
    for left, right, primary in ((1, 0, True), (2, 0, True), (3, 0, True),
                                 (2, 1, False), (1, 3, False), (2, 3, False)):
        diff = values[:, left] - values[:, right]
        observed = float(diff.mean())
        samples = draws[:, left] - draws[:, right]
        p = (int(np.sum(np.abs((signs * diff).mean(axis=1)) >= abs(observed) - 1e-12)) + 1) / 100001
        cost_keep = cost_draws[:, right] > 0
        cost_ratios = cost_draws[cost_keep, left] / cost_draws[cost_keep, right]
        solved_keep = cost_keep & (solve_draws[:, left] > 0) & (solve_draws[:, right] > 0)
        solved_ratios = ((cost_draws[solved_keep, left] / solve_draws[solved_keep, left])
                         / (cost_draws[solved_keep, right] / solve_draws[solved_keep, right]))
        result.append({"comparison": f"{launch.GROUPS[left]}-{launch.GROUPS[right]}", "primary": primary,
                       "difference": observed, "ci95": np.quantile(samples, (0.025, 0.975)).tolist(),
                       "p_raw": p if primary else None,
                       "cost_per_run_ratio_ci95": np.quantile(cost_ratios, (0.025, 0.975)).tolist() if len(cost_ratios) else None,
                       "cost_ratio_zero_denominator_share": float(1 - cost_keep.mean()),
                       "cost_per_solved_ratio_ci95": np.quantile(solved_ratios, (0.025, 0.975)).tolist() if len(solved_ratios) else None,
                       "cost_per_solved_ratio_undefined_share": float(1 - solved_keep.mean())})
    running = 0
    for rank, item in enumerate(sorted((r for r in result if r["primary"]), key=lambda r: r["p_raw"])):
        running = max(running, min(1, (3 - rank) * item["p_raw"]))
        item["p_holm"] = running
    cost_per_solved = {}
    for g, group in enumerate(launch.GROUPS):
        keep = solve_draws[:, g] > 0
        samples = cost_draws[keep, g] / solve_draws[keep, g]
        cost_per_solved[group] = {"ci95": np.quantile(samples, (0.025, 0.975)).tolist() if len(samples) else None,
                                  "bootstrap_zero_denominator_share": float(1 - keep.mean())}
    return {"tasks": len(values), "bootstrap_draws": 10000, "sign_flip_draws": 100000,
            "comparisons": result, "cost_per_solved_run": cost_per_solved,
            "intervals": "ordinary marginal 95% intervals, with Holm adjustment applied to the three primary p-values",
            "sign_flip_assumption": "exchangeability of each task-level paired difference under the null"}


def detail(slot, ref):
    path = launch.legacy.slot_dir(launch.OUT, slot)
    classify.WORK = ROOT / "runs/fix13-prep/work"
    spec = dict(task=slot["task"], group=slot["group"], repeat="r" + str(slot["repeat"]), run_dir=path)
    row = classify.run_row(spec, ref)
    summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
    records = launch.legacy.settled_records(path)
    events = read_events(path)
    assistants = assistant_messages(path, events)
    calls = [payload(path, event) for event in events if event["event_type"] == "tool.intent"]
    keys = [json.dumps([c["name"], c.get("arguments")], sort_keys=True) for c in calls]
    streak = longest = 0
    previous = None
    for key in keys:
        streak = streak + 1 if key == previous else 1
        longest = max(longest, streak)
        previous = key
    eligible = [m for m in assistants[1:] if m.get("tool_calls")]
    words = [len((m.get("content") or "").split()) for m in eligible]
    reasoning = [(r.get("telemetry") or {}).get("reasoning_tokens") or 0 for r in records]
    reminders = [payload(path, e) for e in events if e["event_type"] == "repeat_call_reminder"]
    if not reminders:
        saved = next((e for e in reversed(events) if e["event_type"] == "state.saved"), None)
        reminders = [e for e in payload(path, saved).get("context_events", []) if e.get("event") == "repeat_call_reminder"] if saved else []
    return {**row, "valid": launch.legacy.counted_status(summary) != "infra_error", "cost_cny": summary["cost_cny"],
            "longest_identical_streak": longest,
            "tests": sum(c["name"] == "run_command"
                         and bool(TEST.search(str((c.get("arguments") or {}).get("command", "")))) for c in calls),
            "reminder_triggered": bool(reminders), "reminder": reminders[:1],
            "explanation_eligible_turns": len(eligible), "visible_nonempty_turns": sum(n > 0 for n in words),
            "visible_within40_turns": sum(0 < n <= 40 for n in words),
            "reasoning_nonzero_requests": sum(n > 0 for n in reasoning), "reasoning_tokens": sum(reasoning),
            "mean_reasoning_tokens_per_request": statistics.mean(reasoning) if reasoning else None,
            "length_responses": sum((r.get("provider_metadata") or {}).get("finish_reason") == "length" for r in records),
            "cache_hit_tokens": sum((r.get("telemetry") or {}).get("prompt_cache_hit_tokens") or 0 for r in records),
            "prompt_tokens": sum((r.get("telemetry") or {}).get("prompt_tokens") or 0 for r in records),
            "completion_tokens": sum((r.get("telemetry") or {}).get("completion_tokens") or 0 for r in records),
            "timing": summary.get("timing"), "candidate_changed_files": summary.get("changed_files"),
            "model": summary.get("model"), "telemetry_problems": launch.inspect_run(path, slot["group"])["problems"]}


def markdown(report):
    batch = report["batch"]
    lines = ["# FIX-13 四组比较结果", "", "## 本轮测到的结果", "",
             f"预定 30 道新题、四组各两次，共 240 名额。实际完成 {report['completed_slots']}/240，"
             f"四组名额均可判定的题目为 {report['complete_tasks']}/30。",
             f"全流程标准峰价费用为 {batch.get('spent_cny')} 元，包含列入批次的身份冒烟、退役和重试。"
             "账户实际账单尚未对账。",
             f"停批原因记录为 `{batch.get('halt_reason')}`。", "",
             "| 组别 | 有效名额 | 解出运行 | 至少一次解出的题 | 全尝试标准费用/元 | 每解出运行费用/元 |",
             "|---|---|---|---|---|---|"]
    for group, r in report["groups"].items():
        per = f"{float(r['cost_per_solved_run_cny']):.4f}" if r["cost_per_solved_run_cny"] is not None else "未定义"
        lines.append(f"| {group} | {r['valid_slots']}/60 | {r['solved']}/60 | {r['at_least_once_solved_tasks']}/30 | "
                     f"{float(r['cost_cny_all_attempts']):.6f} | {per} |")
    lines += ["", "U 为原宽松配置，R 加一次同调用提醒，E 要求工具轮先给一句可见解释，UT 使用 thinking high 和 32768 输出额度。",
              "", "## 按题配对比较", ""]
    if report["paired"]:
        lines += [f"以下使用 {report['complete_tasks']} 个完整题目，每题保留四组各两次的整体向量。"
                  "在 easy/other 层内配对 bootstrap 10000 次，符号翻转 100000 次。",
                  "普通 95% 区间按单项报告，三项主比较的 p 值经 Holm 校正。符号翻转采用零假设下题级配对差可交换的假设。", "",
                  "| 比较 | 解出差/百分点 | 普通95%区间/百分点 | Holm p | 类型 |", "|---|---|---|---|---|"]
        for r in report["paired"]["comparisons"]:
            lo, hi = r["ci95"]
            p = f"{r['p_holm']:.5f}" if r["primary"] else "—"
            lines.append(f"| {r['comparison']} | {100*r['difference']:+.1f} | [{100*lo:+.1f}, {100*hi:+.1f}] | {p} | "
                         + ("主比较" if r["primary"] else "探索") + " |")
    else:
        lines.append("当前没有四组名额均可判定的完整题目。")
    if report["complete_tasks"] < 30:
        lines += ["", "本轮为不完整比较。缺失题目列在 report.json，各组未知名额按全未解出至全解出给出界限，完整案例结果需要同时考虑这一缺失。"]
    lines += ["", "## 重复、定位后未编辑和可见说明", "",
              "| 组别 | 高重复运行 | 从未调用编辑 | 最终未改源码 | 定位后未编辑 | R提醒触发 |",
              "|---|---|---|---|---|---|"]
    for group, r in report["groups"].items():
        idle = r["failure_classes"].get("found_idle", 0)
        lines.append(f"| {group} | {r['high_repeat']} | {r['no_edit_calls']} | {r['no_final_source_change']} | {idle} | {r['reminder_triggers']} |")
    e = report["groups"]["E"]
    ut = report["groups"]["UT"]
    lines += ["", f"E 的 {e['explanation_eligible_turns']} 个合资格工具轮中，"
              f"{e['visible_nonempty_turns']} 轮有可见文本，{e['visible_within40_turns']} 轮为 1–40 词。"
              "语义遵从性仍需等量抽取各组轮次复核，当前只报告可见文字计数。",
              f"UT 的 {ut['requests']} 次请求中，{ut['reasoning_nonzero_requests']} 次 reasoning token 非零。"
              f"每运行平均每请求 reasoning token 的中位数为 {ut['reasoning_run_mean_median']}。",
              "失败分类沿用冻结的 gold 参照和原分类规则，去组别人工复核尚待完成，分类保留为描述性证据。", "",
              "## 可以讨论的推测与证据边界", "",
              "本轮比较的是这些完整提示配置在同一批题目上的表现。R 的触发后表现与 E 的遵从者表现属于选择后的描述，主比较保留原分配组。",
              "行为指标与解出差需要分别解读。重复减少或定位后更常编辑，可以支持继续研究行动选择，但收益归因仍需要结合解出与费用。",
              "E 同时改变可见文字、历史结构和缓存。UT 同时改变 thinking 与输出额度。四组无法单独分离内部思考机制或一句话解释的具体作用路径。",
              "新 30 题用于这次预先确定的比较，旧 30 题用于开发。后续按固定 60 题成绩反复调方法时，应作为开发结果报告。公开任务也无法证明模型训练未见。",
              "", "## 原始证据", "",
              "- 正式批次与全部尝试位于 `runs/fix13`。",
              "- 无模型预检、离线参照、身份冒烟及就绪绑定位于 `runs/fix13-preflight`。",
              "- 逐运行数据位于 `docs/analysis/fix13/runs.json`，完整统计位于 `docs/analysis/fix13/report.json`。", ""]
    return "\n".join(lines)


def main():
    slots, _ = launch.load_inputs()
    refs = json.loads(REFS.read_text(encoding="utf-8"))["tasks"]
    attempts = launch.existing_attempts(launch.OUT, slots, mock=False)
    batch = json.loads((launch.OUT / "batch-report.json").read_text(encoding="utf-8"))
    rows = []
    for slot in slots:
        if (launch.legacy.slot_dir(launch.OUT, slot) / "summary.json").is_file():
            rows.append(detail(slot, refs[slot["task"]]))
            save(OUT / "runs.json", rows)
    groups = {}
    for group in launch.GROUPS:
        arm = [r for r in rows if r["group"] == group]
        solved = sum(r["status"] == "pass" for r in arm)
        valid = sum(r["valid"] for r in arm)
        cost = sum((r["cost"] for r in attempts.values() if r["group"] == group), Decimal(0))
        failures = [r for r in arm if r["valid"] and r["status"] != "pass"]
        groups[group] = {"completed_slots": len(arm), "valid_slots": valid, "planned_slots": 60, "solved": solved,
                         "solve_rate_bounds": [solved / 60, (solved + 60 - valid) / 60],
                         "cost_cny_all_attempts": str(cost), "cost_per_solved_run_cny": str(cost / solved) if solved else None,
                         "at_least_once_solved_tasks": len({r["task"] for r in arm if r["status"] == "pass"}),
                         "failure_classes": dict(Counter(r.get("cls") for r in failures)),
                         "found_idle_all_allocated": sum(r.get("cls") == "found_idle" for r in arm) / 60,
                         "found_idle_failed": sum(r.get("cls") == "found_idle" for r in failures) / len(failures) if failures else None,
                         "high_repeat": sum((r["tail_repeat"] or 0) >= 0.5 for r in arm),
                         "no_edit_calls": sum(r["edit_calls"] == 0 for r in arm), "no_final_source_change": sum(not r["source_change"] for r in arm),
                         "reminder_triggers": sum(r["reminder_triggered"] for r in arm),
                         "explanation_eligible_turns": sum(r["explanation_eligible_turns"] for r in arm),
                         "visible_nonempty_turns": sum(r["visible_nonempty_turns"] for r in arm),
                         "visible_within40_turns": sum(r["visible_within40_turns"] for r in arm),
                         "length_responses": sum(r["length_responses"] for r in arm),
                         "reasoning_nonzero_requests": sum(r["reasoning_nonzero_requests"] for r in arm),
                         "reasoning_run_mean_median": classify.median([r["mean_reasoning_tokens_per_request"] for r in arm]),
                         "requests": sum(r["requests"] for r in arm), "cache_hit_tokens": sum(r["cache_hit_tokens"] for r in arm),
                         "prompt_tokens": sum(r["prompt_tokens"] for r in arm), "completion_tokens": sum(r["completion_tokens"] for r in arm),
                         "stop_reasons": dict(Counter(r["stop"] for r in arm))}
    by_task = {task: [r for r in rows if r["task"] == task] for task in refs}
    complete = [task for task, rs in by_task.items() if len(rs) == 8 and all(r["valid"] for r in rs)]
    ingest = json.loads(launch.INGEST.read_text(encoding="utf-8"))
    strata = {s["final"]: s["stratum"] for s in ingest["slots"]}
    values = np.array([[sum(r["status"] == "pass" for r in by_task[t] if r["group"] == g) / 2 for g in launch.GROUPS] for t in complete])
    costs = np.array([[float(sum((r["cost"] for r in attempts.values() if r["summary"].get("task_id") == t and r["group"] == g), Decimal(0)))
                       for g in launch.GROUPS] for t in complete])
    comparisons = paired(values, costs, np.array([strata[t] for t in complete])) if complete else None
    report = {"schema": "fix13.analysis.v1", "planned_slots": 240, "completed_slots": len(rows), "complete_tasks": len(complete),
              "missing_or_invalid_tasks": sorted(set(refs) - set(complete)), "groups": groups, "paired": comparisons,
              "batch": batch, "classification_review": "pending blinded manual review; taxonomy remains descriptive",
              "explanation_review": "visible word counts only; semantic compliance requires equal-arm sample review",
              "evidence_boundary": "first pre-specified comparison on these 30 tasks; public data cannot establish training non-exposure"}
    save(OUT / "report.json", report)
    (OUT / "FIX-13-RESULTS.zh-CN.md").write_text(markdown(report), encoding="utf-8")
    print(json.dumps({"completed_slots": len(rows), "complete_tasks": len(complete), "groups": groups}, ensure_ascii=False))


if __name__ == "__main__":
    main()
