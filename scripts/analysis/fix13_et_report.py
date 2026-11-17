"""Offline ET addendum accounting and task-paired comparisons. No provider or Docker calls."""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.analysis import fix13_report as original  # noqa: E402
from scripts.rebench import run_fix13 as base  # noqa: E402
from scripts.rebench import run_fix13_et as et  # noqa: E402

OUT = ROOT / "docs/analysis/fix13-et"
REFS = json.loads((ROOT / "runs/fix13-preflight/references.json").read_text(encoding="utf-8"))["tasks"]
GROUPS = ("U", "E", "UT", "ET")
PRIMARY = (("ET", "UT"), ("ET", "E"))
COMPARISONS = (
    ("ET", "UT", "primary"),
    ("ET", "E", "primary"),
    ("ET", "U", "secondary"),
    ("E", "U", "context"),
    ("UT", "U", "context"),
)
SEED_BOOTSTRAP = 20261201
SEED_SIGNFLIP = 20261202


def save(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def load_rows():
    rows = [r for r in json.loads((ROOT / "docs/analysis/fix13/runs.json").read_text(encoding="utf-8"))
            if r["group"] in ("U", "E", "UT")]
    base.PROFILES[et.GROUP] = et.PROFILE
    base.VARIANTS[et.GROUP] = et.VARIANT
    original_inspect = base.inspect_run
    base.inspect_run = lambda path, group: et.inspect_et(path) if group == et.GROUP else original_inspect(path, group)
    base.OUT = et.OUT
    slots, _ = et.load_slots()
    et_rows = []
    for slot in slots:
        directory = et.legacy.slot_dir(et.OUT, slot)
        if (directory / "summary.json").is_file():
            et_rows.append(original.detail(slot, REFS[slot["task"]]))
    return rows + et_rows


def group_summary(rows, group, *, all_attempts_cost=None):
    arm = [r for r in rows if r["group"] == group]
    valid = [r for r in arm if r["valid"]]
    solved = sum(r["status"] == "pass" for r in arm)
    final_cost = sum((Decimal(str(r["cost_cny"])) for r in arm), Decimal(0))
    cost = Decimal(str(all_attempts_cost)) if all_attempts_cost is not None else final_cost
    failures = [r for r in valid if r["status"] != "pass"]
    return {
        "completed_slots": len(arm),
        "valid_slots": len(valid),
        "planned_slots": 60,
        "solved": solved,
        "at_least_once_solved_tasks": len({r["task"] for r in arm if r["status"] == "pass"}),
        "cost_cny_final_runs": str(final_cost),
        "cost_cny_all_attempts": str(cost),
        "cost_per_solved_run_cny": str(cost / solved) if solved else None,
        "failure_classes": dict(Counter(r.get("cls") for r in failures)),
        "found_idle_all_allocated": sum(r.get("cls") == "found_idle" for r in arm) / len(arm) if arm else None,
        "found_idle_failed": (sum(r.get("cls") == "found_idle" for r in failures) / len(failures)
                              if failures else None),
        "high_repeat": sum((r.get("tail_repeat") or 0) >= 0.5 for r in arm),
        "no_edit_calls": sum(r["edit_calls"] == 0 for r in arm),
        "no_final_source_change": sum(not r["source_change"] for r in arm),
        "explanation_eligible_turns": sum(r["explanation_eligible_turns"] for r in arm),
        "visible_nonempty_turns": sum(r["visible_nonempty_turns"] for r in arm),
        "visible_within40_turns": sum(r["visible_within40_turns"] for r in arm),
        "length_responses": sum(r["length_responses"] for r in arm),
        "reasoning_nonzero_requests": sum(r["reasoning_nonzero_requests"] for r in arm),
        "reasoning_run_mean_median": original.classify.median(
            [r["mean_reasoning_tokens_per_request"] for r in arm]),
        "requests": sum(r["requests"] for r in arm),
        "cache_hit_tokens": sum(r["cache_hit_tokens"] for r in arm),
        "prompt_tokens": sum(r["prompt_tokens"] for r in arm),
        "completion_tokens": sum(r["completion_tokens"] for r in arm),
        "stop_reasons": dict(Counter(r["stop"] for r in arm)),
    }


def weights(left, right):
    vector = np.zeros(len(GROUPS))
    vector[GROUPS.index(left)] = 1
    vector[GROUPS.index(right)] = -1
    return vector


def interaction_weights():
    vector = np.zeros(len(GROUPS))
    vector[GROUPS.index("ET")] = 1
    vector[GROUPS.index("UT")] = -1
    vector[GROUPS.index("E")] = -1
    vector[GROUPS.index("U")] = 1
    return vector


def paired(values, costs, strata):
    rng = np.random.default_rng(SEED_BOOTSTRAP)
    indices = np.concatenate([rng.choice(np.flatnonzero(strata == s), (10000, int(sum(strata == s))))
                              for s in sorted(set(strata))], axis=1)
    draws = values[indices].mean(axis=1)
    cost_draws = costs[indices].sum(axis=1)
    solve_draws = (2 * values[indices]).sum(axis=1)
    sign_rng = np.random.default_rng(SEED_SIGNFLIP)
    signs = sign_rng.choice((-1, 1), (100000, len(values)))
    result = []
    for left, right, kind in COMPARISONS:
        vector = weights(left, right)
        diff = values @ vector
        observed = float(diff.mean())
        samples = draws @ vector
        p = (int(np.sum(np.abs((signs * diff).mean(axis=1)) >= abs(observed) - 1e-12)) + 1) / 100001
        result.append({"comparison": f"{left}-{right}", "kind": kind,
                       "difference": observed,
                       "ci95": np.quantile(samples, (0.025, 0.975)).tolist(),
                       "p_raw": p if kind == "primary" else None,
                       "cost_per_run_ratio_ci95": cost_ratio(cost_draws, left, right),
                       "cost_per_solved_ratio_ci95": cost_per_solved_ratio(cost_draws, solve_draws, left, right)})
    interaction = interaction_weights()
    diff = values @ interaction
    observed = float(diff.mean())
    samples = draws @ interaction
    p = (int(np.sum(np.abs((signs * diff).mean(axis=1)) >= abs(observed) - 1e-12)) + 1) / 100001
    result.append({"comparison": "(ET-UT)-(E-U)", "kind": "exploratory_interaction",
                   "difference": observed, "ci95": np.quantile(samples, (0.025, 0.975)).tolist(),
                   "p_raw": p, "p_holm": None})
    running = 0
    primaries = sorted((r for r in result if r["kind"] == "primary"), key=lambda r: r["p_raw"])
    for rank, item in enumerate(primaries):
        running = max(running, min(1, (len(primaries) - rank) * item["p_raw"]))
        item["p_holm"] = running
    return {"tasks": len(values), "bootstrap_draws": 10000, "sign_flip_draws": 100000,
            "seeds": {"bootstrap": SEED_BOOTSTRAP, "sign_flip": SEED_SIGNFLIP},
            "comparisons": result,
            "holm_family": "ET-UT and ET-E only; the other rows and the interaction are exploratory",
            "intervals": "ordinary marginal 95% intervals",
            "sign_flip_assumption": "exchangeability of each task-level paired difference under the null"}


def cost_ratio(cost_draws, left, right):
    left_index, right_index = GROUPS.index(left), GROUPS.index(right)
    keep = cost_draws[:, right_index] > 0
    if not keep.any():
        return None
    return np.quantile(cost_draws[keep, left_index] / cost_draws[keep, right_index], (0.025, 0.975)).tolist()


def cost_per_solved_ratio(cost_draws, solve_draws, left, right):
    left_index, right_index = GROUPS.index(left), GROUPS.index(right)
    keep = (solve_draws[:, left_index] > 0) & (solve_draws[:, right_index] > 0)
    if not keep.any():
        return None
    ratio = ((cost_draws[keep, left_index] / solve_draws[keep, left_index])
             / (cost_draws[keep, right_index] / solve_draws[keep, right_index]))
    return np.quantile(ratio, (0.025, 0.975)).tolist()


def decision(paired_report):
    item = next(r for r in paired_report["comparisons"] if r["comparison"] == "ET-UT")
    difference, (low, high) = item["difference"], item["ci95"]
    if difference >= 0.15 and low > 0:
        return ("supports_additivity",
                "ET 比 UT 高至少 15 个百分点且区间不跨零：支持明显叠加，可讨论把 E 加到 UT。")
    if abs(difference) <= 0.10:
        return ("compatible_with_overlap",
                "ET 与 UT 的差在 ±10 个百分点内：与重叠/替代相容，但不能证明两者机制相同。")
    if difference <= -0.10:
        return ("interference_signal",
                "ET 比 UT 低至少 10 个百分点：提示干扰，先检查 E 的合规率和输出截断。")
    return ("inconclusive", "区间较宽且未落入预定的叠加、重叠或干扰区：证据不足，保持不确定。")


def markdown(report):
    batch = report["batch"]
    lines = ["# FIX-13 ET 追加组结果", "",
             "本组是 FIX-13 四组正式比较之后的事后追加探索组：在同一批 30 道题上，"
             "只跑 ET（UT + 固定一句结果解释），不复跑 U/R/E/UT。它不是原预注册检验族的一部分。",
             "", "## 完成情况与费用", "",
             f"ET 完成 {report['completed_slots']}/60 个名额，有效 {report['valid_slots']}/60；"
             f"四组与 ET 均有两次有效运行的题目为 {report['complete_tasks']}/30。",
             f"ET 全流程标准峰价费用 {batch.get('known_settled_cny')} 元（其中最终名额 "
             f"{report['groups']['ET']['cost_cny_final_runs']} 元，3 次基础设施退役尝试合计约 0.97 元）；"
             f"停批原因 `{batch.get('halt_reason')}`。"
             "这是 provider usage 的标准峰价核算，不是已对账的账户账单。", "",
             "| 组别 | 有效名额 | 解出运行 | 至少一次解出的题 | 全尝试标准费用/元 | 每解出运行费用/元 |",
             "|---|---|---|---|---|---|"]
    for group, r in report["groups"].items():
        per = f"{float(r['cost_per_solved_run_cny']):.4f}" if r["cost_per_solved_run_cny"] else "未定义"
        lines.append(f"| {group} | {r['valid_slots']}/60 | {r['solved']}/60 | "
                     f"{r['at_least_once_solved_tasks']}/30 | {float(r['cost_cny_all_attempts']):.6f} | {per} |")
    et_group = report["groups"]["ET"]
    lines += ["", "U/R/E/UT 的数值来自原正式批次，ET 是本次追加；四组原报告不改写。"
              f"ET 的全尝试费用含 3 次基础设施退役尝试，最终名额本身为 "
              f"{float(et_group['cost_cny_final_runs']):.6f} 元。",
              "", "## 按题配对比较", ""]
    if report["paired"]:
        p = report["paired"]
        lines += [f"使用 {report['complete_tasks']} 道完整题目，每题保留两组各两次的整体向量；"
                  f"在 easy/other 层内配对 bootstrap {p['bootstrap_draws']} 次，符号翻转 {p['sign_flip_draws']} 次。",
                  f"Holm 只作用于 ET−UT 与 ET−E 两项；其余比较和交互项为探索性。", "",
                  "| 比较 | 解出差/百分点 | 普通95%区间/百分点 | p（Holm） | 类型 |", "|---|---|---|---|---|"]
        for item in p["comparisons"]:
            low, high = item["ci95"]
            tag = {"primary": "主比较", "secondary": "次要", "context": "背景",
                   "exploratory_interaction": "交互（探索）"}[item["kind"]]
            p_text = (f"{item['p_holm']:.5f}" if item.get("p_holm") is not None
                      else f"{item['p_raw']:.5f}" if item.get("p_raw") is not None else "—")
            lines.append(f"| {item['comparison']} | {100 * item['difference']:+.1f} | "
                         f"[{100 * low:+.1f}, {100 * high:+.1f}] | {p_text} | {tag} |")
    else:
        lines.append("当前没有四组与 ET 全部可判定的完整题目。")
    lines += ["", "## 行为与说明", "",
              "| 组别 | 高重复 | 从未编辑 | 定位后未编辑 | 说明轮/合资格轮 | reasoning 非零/请求 | 请求数 |",
              "|---|---|---|---|---|---|---|"]
    for group in ("U", "E", "UT", "ET"):
        r = report["groups"][group]
        compliance = (f"{r['visible_nonempty_turns']}/{r['explanation_eligible_turns']}"
                      if r["explanation_eligible_turns"] else "—")
        reasoning = (f"{r['reasoning_nonzero_requests']}/{r['requests']}"
                     if r["reasoning_nonzero_requests"] else "—")
        idle = r["failure_classes"].get("found_idle", 0)
        lines.append(f"| {group} | {r['high_repeat']} | {r['no_edit_calls']} | {idle} | "
                     f"{compliance} | {reasoning} | {r['requests']} |")
    lines += ["", "## 预定判断", "",
              f"**{report['decision']['label']}**：{report['decision']['text']}", ""]
    interaction = next((r for r in (report["paired"] or {}).get("comparisons", [])
                        if r["comparison"] == "(ET-UT)-(E-U)"), None)
    if interaction:
        low, high = interaction["ci95"]
        e_group, et_group, ut_group = (report["groups"][g] for g in ("E", "ET", "UT"))
        lines += [f"交互项为 {100 * interaction['difference']:+.1f} 个百分点，"
                  f"普通 95% 区间 [{100 * low:+.1f}, {100 * high:+.1f}]（探索性 p={interaction['p_raw']:.5f}）："
                  "E 的收益在 thinking 开启时基本消失，ET 也几乎没有在 UT 之上增加解出。"
                  "按本轮预定的判断规则，这是“重叠/替代”方向的证据，而不是“UT 加 E 更强”。",
                  f"ET 的说明合规率为 {et_group['visible_nonempty_turns']}/{et_group['explanation_eligible_turns']}"
                  f"（{100 * et_group['visible_nonempty_turns'] / et_group['explanation_eligible_turns']:.1f}%），"
                  f"低于 E 的 {e_group['visible_nonempty_turns']}/{e_group['explanation_eligible_turns']}"
                  f"（{100 * e_group['visible_nonempty_turns'] / e_group['explanation_eligible_turns']:.1f}%）；"
                  f"reasoning 非零请求为 {et_group['reasoning_nonzero_requests']}/{et_group['requests']}"
                  f"（{100 * et_group['reasoning_nonzero_requests'] / et_group['requests']:.1f}%），"
                  f"接近 UT 的 {ut_group['reasoning_nonzero_requests']}/{ut_group['requests']}"
                  f"（{100 * ut_group['reasoning_nonzero_requests'] / ut_group['requests']:.1f}%）。"
                  "也就是说，ET 确实同时保留了两条路径，但额外的可见说明没有转化为额外解出。", ""]
    relations = report["set_relations"]
    if relations["ET_subset_UT"]:
        extra = ("E 的题集也全部包含在 ET 里。" if relations["E_subset_ET"] else "")
        if not relations["ET_only_new_tasks"]:
            extra += "ET 没有解出任何 E 与 UT 都未解出的题。"
        lines += [f"按题看，ET 的 {len(report['task_sets']['ET'])} 道至少解出一次的题全部包含在 "
                  f"UT 的 {len(report['task_sets']['UT'])} 道题里。{extra}", ""]
    e_cost = float(report["groups"]["E"]["cost_cny_all_attempts"])
    ut_cost = float(report["groups"]["UT"]["cost_cny_all_attempts"])
    e_per = float(report["groups"]["E"]["cost_per_solved_run_cny"])
    ut_per = float(report["groups"]["UT"]["cost_per_solved_run_cny"])
    lines += [f"实际含义：如果目标是达到 UT 水平的解出率，本组数据支持用 E 单独替代 UT，"
              f"而不是在 UT 上再加 E。E 的全尝试费用为 {e_cost:.2f} 元、每解出运行 {e_per:.2f} 元，"
              f"约为 UT（{ut_cost:.2f} 元、{ut_per:.2f} 元）的 "
              f"{100 * e_cost / ut_cost:.0f}% / {100 * e_per / ut_per:.0f}%。", ""]
    lines += ["## 不能判断的内容", "",
              "- 这是事后追加组；同一 30 题已经看过四组结果，不能称为未见过的独立验证。",
              "- ET 每个题区组只有两个名额，实际并发为 2（原批次为 3）；这是批次级调度差异，"
              "不改变单次运行的提示、模型参数、限额、工具环境或评分，但可能影响运行时长和缓存。",
              "- 与原批次相隔约一天；身份 fingerprint、价格页和题级配对降低了时间漂移风险，但没有消除它。",
              "- ET 的 T 是“thinking high + 32768”的捆绑，E 是“提示 + 可见文本 + 历史 + 缓存”的捆绑；"
              "交互项只能解释为两个捆绑之间。",
              "- 60 次运行、30 题、每题 2 次；预定的功效估计约为 ±15–16 个百分点，"
              "本次 ET−UT 的普通区间为 [−8.3, +3.3]，ET−E 为 [−5.0, +10.0]。"
              "区间足以排除大的叠加，但仍不能确认很小的差异。",
              "- E 的说明在 thinking 模式下是否稳定，需看本次合规率；合规率低会限制对结果的解释。",
              "- 公开任务源不能证明模型训练未见。", "",
              "## 原始证据", "",
              "- 运行与账本：`runs/fix13-et`。",
              "- 预检、身份和就绪绑定：`runs/fix13-et-preflight`。",
              "- 逐运行数据：`docs/analysis/fix13-et/runs.json`；统计：`docs/analysis/fix13-et/report.json`。",
              "- 原四组报告保持原样：`docs/analysis/fix13/FIX-13-RESULTS.zh-CN.md`。", ""]
    return "\n".join(lines)


def main():
    rows = load_rows()
    et_rows = [r for r in rows if r["group"] == "ET"]
    if len(et_rows) != 60 or any(not r["valid"] for r in et_rows):
        raise SystemExit(f"ET has {len(et_rows)}/60 runs, "
                         f"{sum(r['valid'] for r in et_rows)} valid; refuse to report before completion")
    batch = json.loads((et.OUT / "batch-report.json").read_text(encoding="utf-8"))
    original = json.loads((ROOT / "docs/analysis/fix13/report.json").read_text(encoding="utf-8"))
    costs_all = {group: original["groups"][group]["cost_cny_all_attempts"] for group in ("U", "E", "UT")}
    costs_all["ET"] = batch["by_group"]["ET"]["cost_cny"]
    groups = {group: group_summary(rows, group, all_attempts_cost=costs_all[group]) for group in GROUPS}
    by_task = {task: [r for r in rows if r["task"] == task] for task in REFS}
    complete = [task for task, items in by_task.items()
                if len(items) == 8 and all(r["valid"] for r in items)
                and {r["group"] for r in items} == set(GROUPS)]
    ingest = json.loads((ROOT / "scripts/rebench/out/fix13_ingest.json").read_text(encoding="utf-8"))
    strata = {slot["final"]: slot["stratum"] for slot in ingest["slots"]}
    values = np.array([[sum(r["status"] == "pass" for r in by_task[t] if r["group"] == g) / 2
                        for g in GROUPS] for t in complete])
    costs = np.array([[sum(float(r["cost_cny"]) for r in by_task[t] if r["group"] == g)
                       for g in GROUPS] for t in complete])
    paired_report = paired(values, costs, np.array([strata[t] for t in complete])) if complete else None
    decision_value = decision(paired_report) if paired_report else ("no_complete_tasks", "没有完整题目。")
    task_sets = {group: {task for task in REFS
                         if any(r["status"] == "pass" for r in by_task[task] if r["group"] == group)}
                 for group in GROUPS}
    report = {"schema": "fix13et.analysis.v1", "planned_slots": 60, "completed_slots": len(et_rows),
              "valid_slots": sum(r["valid"] for r in et_rows), "complete_tasks": len(complete),
              "missing_or_invalid_tasks": sorted(set(REFS) - set(complete)),
              "groups": groups, "paired": paired_report,
              "task_sets": {group: sorted(task_sets[group]) for group in GROUPS},
              "set_relations": {"ET_subset_UT": task_sets["ET"] <= task_sets["UT"],
                                "E_subset_ET": task_sets["E"] <= task_sets["ET"],
                                "ET_only_new_tasks": sorted(task_sets["ET"]
                                                            - (task_sets["E"] | task_sets["UT"]))},
              "decision": {"label": decision_value[0], "text": decision_value[1]},
              "batch": batch,
              "evidence_boundary": "post-hoc ET addendum on the same 30 exposed tasks; "
                                   "not part of the original pre-registered family"}
    save(OUT / "runs.json", rows)
    save(OUT / "report.json", report)
    (OUT / "FIX-13-ET-RESULTS.zh-CN.md").write_text(markdown(report), encoding="utf-8")
    print(json.dumps({"completed_slots": len(et_rows), "complete_tasks": len(complete),
                      "et_solved": groups["ET"]["solved"], "decision": decision_value[0]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
