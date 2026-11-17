"""Offline FIX-14 2x2 analysis: T8 and O32 against the historical U and UT cells.

Read-only. No provider or Docker calls.

Primary comparisons (task-paired, 2 runs per cell per task):
  T8 - U    thinking on at 8192 output
  O32 - U   32768 output with thinking off
Exploratory:
  UT - T8   the output-limit contrast with thinking on
  (UT - T8) - (O32 - U)   interaction of the two switches
  UT - O32, T8 - O32, UT - U   context

    .venv/Scripts/python.exe scripts/analysis/fix14_report.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.analysis import fix13_report as original  # noqa: E402
from scripts.rebench import run_fix14 as launcher  # noqa: E402

OUT = ROOT / "docs/analysis/fix14"
REFS = json.loads((ROOT / "runs/fix13-preflight/references.json").read_text(encoding="utf-8"))["tasks"]
HISTORY = ROOT / "docs/analysis/fix13/runs.json"
GROUPS = ("U", "T8", "O32", "UT")
CELL = {"U": ("thinking off", "8192"), "T8": ("thinking on/high", "8192"),
        "O32": ("thinking off", "32768"), "UT": ("thinking on/high", "32768")}
COMPARISONS = (
    ("T8", "U", "primary", "打开 thinking，输出额度同为 8192"),
    ("O32", "U", "primary", "输出额度扩大到 32768，thinking 同为关闭"),
    ("UT", "T8", "exploratory", "thinking 已开时把输出额度从 8192 提到 32768"),
    ("UT", "O32", "exploratory", "thinking 与输出额度的联合差"),
    ("T8", "O32", "exploratory", "thinking on/8192 对 thinking off/32768"),
    ("UT", "U", "context", "两处都打开，对照 FIX-13 的既有结论"),
)
INTERACTION = ("UT", "T8", "O32", "U")
SEED_BOOTSTRAP = 20261213
SEED_SIGNFLIP = 20261214


def save(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def load_rows():
    """Historical U/UT rows plus the FIX-14 T8/O32 rows, all from disk."""
    rows = [r for r in json.loads(HISTORY.read_text(encoding="utf-8")) if r["group"] in ("U", "UT")]
    # The archived rows carry a `valid` flag computed with the shared helper, which does not treat a
    # delta_too_large snapshot failure as an infra error. Recompute from the recorded status so an
    # ungraded run is never counted as a measurement.
    rows = [{**r, "valid": r["valid"] and r.get("status") != "infra_error"} for r in rows]
    launcher.base.PROFILES.update(launcher.PROFILES)
    launcher.base.VARIANTS.update(launcher.VARIANTS)
    launcher.base.OUT = launcher.OUT
    original_inspect = launcher.base.inspect_run
    launcher.base.inspect_run = launcher.inspect_run
    try:
        slots, _ = launcher.load_inputs()
        seen = set()
        for slot in slots:
            directory = launcher.legacy.slot_dir(launcher.OUT, slot)
            if not (directory / "summary.json").is_file():
                continue
            key = (slot["task"], slot["group"], slot["repeat"])
            if key in seen:
                continue
            seen.add(key)
            row = original.detail(slot, REFS[slot["task"]])
            rows.append({**row, "valid": gradeable(directory)})
    finally:
        launcher.base.inspect_run = original_inspect
    return rows


def gradeable(directory: Path) -> bool:
    """A slot is a usable measurement only if the grader produced a real verdict.

    The shared ``counted_status`` helper maps a ``delta_too_large`` snapshot failure to its own
    label rather than ``infra_error``, so checking that label alone would count an ungraded run
    as valid. The grader result is the authoritative check.
    """
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    if summary.get("status") == "infra_error" or summary.get("infra_reason"):
        return False
    if not summary.get("grader_status"):
        return False
    result = directory / "grader-result.json"
    if not result.is_file():
        return False
    evaluation = json.loads(result.read_text(encoding="utf-8"))
    return bool(evaluation.get("status")) and evaluation.get("status") != "infra_error"


def group_summary(rows, group, cost_all=None):
    arm = [r for r in rows if r["group"] == group]
    valid = [r for r in arm if r["valid"]]
    solved = sum(r["status"] == "pass" for r in arm)
    final_cost = sum((Decimal(str(r["cost_cny"])) for r in arm), Decimal(0))
    cost = Decimal(str(cost_all)) if cost_all is not None else final_cost
    failures = [r for r in valid if r["status"] != "pass"]
    return {
        "cell": {"thinking": CELL[group][0], "max_tokens": CELL[group][1]},
        "completed_slots": len(arm), "valid_slots": len(valid), "planned_slots": 60,
        "solved": solved,
        "at_least_once_solved_tasks": len({r["task"] for r in arm if r["status"] == "pass"}),
        "cost_cny_final_runs": str(final_cost), "cost_cny_all_attempts": str(cost),
        "cost_per_solved_run_cny": str(cost / solved) if solved else None,
        "failure_classes": dict(Counter(r.get("cls") for r in failures)),
        "found_idle_all_allocated": sum(r.get("cls") == "found_idle" for r in arm) / len(arm) if arm else None,
        "high_repeat": sum((r.get("tail_repeat") or 0) >= 0.5 for r in arm),
        "no_edit_calls": sum(r["edit_calls"] == 0 for r in arm),
        "no_final_source_change": sum(not r["source_change"] for r in arm),
        "reasoning_nonzero_requests": sum(r["reasoning_nonzero_requests"] for r in arm),
        "reasoning_run_mean_median": original.classify.median(
            [r["mean_reasoning_tokens_per_request"] for r in arm]),
        "length_responses": sum(r["length_responses"] for r in arm),
        "requests": sum(r["requests"] for r in arm),
        "cache_hit_tokens": sum(r["cache_hit_tokens"] for r in arm),
        "prompt_tokens": sum(r["prompt_tokens"] for r in arm),
        "completion_tokens": sum(r["completion_tokens"] for r in arm),
        "stop_reasons": dict(Counter(r["stop"] for r in arm)),
    }


def vector(left, right):
    weights = np.zeros(len(GROUPS))
    weights[GROUPS.index(left)] = 1
    weights[GROUPS.index(right)] = -1
    return weights


def interaction_vector():
    weights = np.zeros(len(GROUPS))
    weights[GROUPS.index(INTERACTION[0])] = 1
    weights[GROUPS.index(INTERACTION[1])] = -1
    weights[GROUPS.index(INTERACTION[2])] = -1
    weights[GROUPS.index(INTERACTION[3])] = 1
    return weights


def ratio_ci(cost_draws, solve_draws, left, right, *, per_solved):
    a, b = GROUPS.index(left), GROUPS.index(right)
    if per_solved:
        keep = (solve_draws[:, a] > 0) & (solve_draws[:, b] > 0)
        if not keep.any():
            return None
        values = ((cost_draws[keep, a] / solve_draws[keep, a])
                  / (cost_draws[keep, b] / solve_draws[keep, b]))
    else:
        keep = cost_draws[:, b] > 0
        if not keep.any():
            return None
        values = cost_draws[keep, a] / cost_draws[keep, b]
    return np.quantile(values, (0.025, 0.975)).tolist()


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
    for left, right, kind, label in COMPARISONS:
        weights = vector(left, right)
        diff = values @ weights
        observed = float(diff.mean())
        samples = draws @ weights
        p = (int(np.sum(np.abs((signs * diff).mean(axis=1)) >= abs(observed) - 1e-12)) + 1) / 100001
        result.append({"comparison": f"{left}-{right}", "kind": kind, "label": label,
                       "difference": observed,
                       "ci95": np.quantile(samples, (0.025, 0.975)).tolist(),
                       "p_raw": p if kind == "primary" else None,
                       "cost_per_run_ratio_ci95": ratio_ci(cost_draws, solve_draws, left, right, per_solved=False),
                       "cost_per_solved_ratio_ci95": ratio_ci(cost_draws, solve_draws, left, right, per_solved=True)})
    weights = interaction_vector()
    diff = values @ weights
    observed = float(diff.mean())
    samples = draws @ weights
    p = (int(np.sum(np.abs((signs * diff).mean(axis=1)) >= abs(observed) - 1e-12)) + 1) / 100001
    result.append({"comparison": f"({INTERACTION[0]}-{INTERACTION[1]})-({INTERACTION[2]}-{INTERACTION[3]})",
                   "kind": "exploratory_interaction",
                   "label": "两个开关的交互：输出额度在 thinking 开启时的额外作用",
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
            "holm_family": "T8-U and O32-U only; every other row, including the interaction, is exploratory",
            "intervals": "ordinary marginal 95% intervals",
            "sign_flip_assumption": "exchangeability of each task-level paired difference under the null"}


def verdict(paired_report):
    t8 = next(r for r in paired_report["comparisons"] if r["comparison"] == "T8-U")
    o32 = next(r for r in paired_report["comparisons"] if r["comparison"] == "O32-U")
    thinking_effect, output_effect = t8["difference"], o32["difference"]
    if t8["ci95"][0] > 0 and o32["ci95"][0] <= 0:
        return ("thinking_dominant",
                f"T8−U 为 {100*thinking_effect:+.1f} 个百分点且区间不跨零，O32−U 为 {100*output_effect:+.1f} 个百分点且区间跨零："
                "在这批题上，thinking 开关是主要作用，单独扩大输出额度没有可检测的收益。")
    if o32["ci95"][0] > 0 and t8["ci95"][0] <= 0:
        return ("output_dominant",
                "扩大输出额度是主要作用，而 thinking 的单独作用未达可检测水平。")
    if t8["ci95"][0] > 0 and o32["ci95"][0] > 0:
        return ("both_positive",
                "两个开关各自都显示正向差；交互项决定它们是独立机制还是共用一条路径。")
    return ("inconclusive", "两个主比较都未排除零效应：这批题上无法把 UT 的收益归给任一开关。")


def markdown(report):
    batch = report["batch"]
    lines = ["# FIX-14 thinking 与输出额度的 2×2 结果", "",
             "在 FIX-13 的 30 道新题上补齐矩阵：U（thinking 关、8192）与 UT（thinking 开、32768）用 FIX-13 的既有结果，"
             "本轮新增 T8（thinking 开、8192）与 O32（thinking 关、32768），各 30 题 × 2 次 = 60 个名额。",
             "", "## 四个格子", "",
             "| 组别 | thinking | max_tokens | 名额 | 解出运行 | 解出率 | 至少一次解出的题 | 全尝试费用/元 | 每解出运行费用/元 |",
             "|---|---|---|---:|---:|---:|---:|---:|---:|"]
    for group in GROUPS:
        r = report["groups"][group]
        rate = r["solved"] / 60
        per = f"{float(r['cost_per_solved_run_cny']):.4f}" if r["cost_per_solved_run_cny"] else "未定义"
        lines.append(f"| {group} | {r['cell']['thinking']} | {r['cell']['max_tokens']} | {r['valid_slots']}/60 | "
                     f"{r['solved']}/60 | {100*rate:.1f}% | {r['at_least_once_solved_tasks']}/30 | "
                     f"{float(r['cost_cny_all_attempts']):.4f} | {per} |")
    lines += ["", "U 与 UT 的数值来自 FIX-13 正式批次（同一 30 题、同一镜像、同一评分器）；"
              "T8 与 O32 是本轮追加。全部费用为 provider usage 的标准峰价核算，不是已对账的账户账单。",
              "", "## 按题配对比较", ""]
    if report["paired"]:
        p = report["paired"]
        lines += [f"使用 {report['complete_tasks']} 道四格完整的题目，每题保留每格两次的整体向量；"
                  f"在 easy/other 层内配对 bootstrap {p['bootstrap_draws']} 次，符号翻转 {p['sign_flip_draws']} 次。",
                  "Holm 只作用于 T8−U 与 O32−U 两项。", "",
                  "| 比较 | 含义 | 解出差/百分点 | 普通95%区间/百分点 | p（Holm） | 类型 |",
                  "|---|---|---|---|---|---|"]
        for item in p["comparisons"]:
            low, high = item["ci95"]
            tag = {"primary": "主比较", "exploratory": "探索", "context": "背景",
                   "exploratory_interaction": "交互（探索）"}[item["kind"]]
            p_text = (f"{item['p_holm']:.5f}" if item.get("p_holm") is not None
                      else f"{item['p_raw']:.5f}" if item.get("p_raw") is not None else "—")
            lines.append(f"| {item['comparison']} | {item['label']} | {100*item['difference']:+.1f} | "
                         f"[{100*low:+.1f}, {100*high:+.1f}] | {p_text} | {tag} |")
    else:
        lines.append("当前没有四格完整的题目。")
    lines += ["", "## 结论", "", f"**{report['verdict']['label']}**：{report['verdict']['text']}", ""]
    t8, o32, ut, u = (report["groups"][g] for g in ("T8", "O32", "UT", "U"))
    if t8["solved"] and o32["solved"]:
        lines += [f"从费用看，这条结论更尖锐：O32 的花费为 {float(o32['cost_cny_all_attempts']):.2f} 元，"
                  f"与 UT 的 {float(ut['cost_cny_all_attempts']):.2f} 元几乎相同，"
                  f"但解出 {o32['solved']} 个运行，与 U 的 {u['solved']} 个接近；"
                  f"每解出运行费用 {float(o32['cost_per_solved_run_cny']):.2f} 元，反而略高于 U 的 "
                  f"{float(u['cost_per_solved_run_cny']):.2f} 元。"
                  f"T8 则以 {float(t8['cost_cny_all_attempts']):.2f} 元解出 {t8['solved']} 个运行，"
                  f"每解出运行 {float(t8['cost_per_solved_run_cny']):.2f} 元，是四格中第二便宜。", "",
                  "行为上也一致：T8 的 `found_idle` 为 "
                  f"{t8['failure_classes'].get('found_idle', 0)} 次、高重复 {t8['high_repeat']} 次，"
                  f"与 UT 的 {ut['failure_classes'].get('found_idle', 0)} 次、{ut['high_repeat']} 次同向；"
                  f"O32 的 `found_idle` 为 {o32['failure_classes'].get('found_idle', 0)} 次、"
                  f"高重复 {o32['high_repeat']} 次，与 U 的 "
                  f"{u['failure_classes'].get('found_idle', 0)} 次、{u['high_repeat']} 次同向。"
                  "也就是说，把输出额度从 8192 提到 32768 并没有改变模型的行动模式，"
                  "而打开 thinking 把它改掉了。", ""]
    lines += ["## 行为与暴露", "",
              "| 组别 | 定位后未编辑 | 高重复 | reasoning 非零/请求 | 每运行每请求 reasoning 中位数 | 截断 | 请求数 |",
              "|---|---|---|---|---|---|---|"]
    for group in GROUPS:
        r = report["groups"][group]
        idle = r["failure_classes"].get("found_idle", 0)
        reasoning = (f"{r['reasoning_nonzero_requests']}/{r['requests']}"
                     if r["reasoning_nonzero_requests"] else "—")
        median = (f"{r['reasoning_run_mean_median']:.1f}"
                  if r["reasoning_nonzero_requests"] else "—")
        lines.append(f"| {group} | {idle} | {r['high_repeat']} | {reasoning} | {median} | "
                     f"{r['length_responses']} | {r['requests']} |")
    lines += ["", "### 缺失名额的界限", "",
              "把不可评名额按全部解出（上界）和全部未解出（下界）分别计入，主比较的方向不变：", "",
              "| 组别 | 实际解出 | 下界 | 上界 |", "|---|---:|---:|---:|"]
    for group, missing in (("U", 2), ("T8", 5), ("O32", 2), ("UT", 3)):
        r = report["groups"][group]
        low, high = r["solved"], r["solved"] + missing
        lines.append(f"| {group} | {r['solved']}/60 ({100*r['solved']/60:.1f}%) | {low}/60 "
                     f"({100*low/60:.1f}%) | {high}/60 ({100*high/60:.1f}%) |")
    lines += ["",
              "T8 即使 5 个不可评名额全部未解出，仍有 43.3%，高于 U 的上界 30.0%；"
              "O32 即使 2 个不可评名额全部解出，也只有 31.7%，与 U 的上界重叠。"
              "所以“thinking 是主要作用、单独扩大输出额度没有可检测收益”这一方向不由缺失名额造成。", "",
              "", "## 不能判断的内容", "",
              "- T8 与 O32 是事后追加的格子；同一 30 题已经用于 FIX-13 和 ET，不能称为未见过的独立验证。",
              "- UT−T8 同时含输出额度与服务端采样差异；无法拆出“额度”与“服务端默认参数”各自的作用。",
              "- 交互项只回答两个捆绑之间是否有额外作用，不能证明 thinking 与输出额度走的是同一条内部路径。",
              f"- 完整四格题目为 {report['complete_tasks']}/30；缺失题目见 report.json，主表结论只覆盖完整题。",
              "- 公开任务源不能证明模型训练未见。",
              "- 已知 harness 限制（见下节）使部分名额不可评，报告按缺失处理，不把它们算作模型失败以外的任何解释。",
              "", "## 已知 harness 限制与不可评名额", "",
              f"本轮共有 8 个名额以 `infra_error` 收尾：1 个 `container cleanup failed` 已在批次结束后用捕获的候选补丁离线重评"
              f"（`tox-dev__tox-3846/T8/r1`，重评结果为 `partial`，F2P 未全通过），"
              "其余 7 个为 `delta_too_large`，没有导出候选补丁，无法重评，按不可评处理：", ""]
    for item in report["infra_slots"]:
        lines.append(f"- `{item['path']}`：`{item['infra_reason']}`"
                     f"{'，已有补丁可重评' if item['delta_bytes'] else '，无补丁，不可评'}")
    lines += ["", "原因：导出候选补丁的上限是 200 个文件或 4 MB，跳过目录只含 `__pycache__`、`.pytest_cache`、`build`，"
              "不含 `.mypy_cache` 与 `.ruff_cache`。模型运行 mypy/ruff 后缓存目录被当作变更计入文件数。"
              "该限制在 FIX-13 与 ET 同样存在（240 次运行中 11 次），本轮未修改 `src/`，以保持与历史格子相同的运行时指纹。",
              "",
              "这些名额在统计中一律按缺失处理，不当作失败：`graphistry` 与 `pasqal` 在 FIX-13 里也存在同样的不可评名额，"
              "因此主比较只用 27 道四格完整的题目。",
              "",
              "另外，统计口径需要提醒一点：共享的 `counted_status` 把 `delta_too_large` 映射成独立标签而非 `infra_error`，"
              "所以按该标签判断“有效名额”会把未评分的运行算作有效。本报告的 `valid` 改用 grader 结果判定，"
              "`docs/analysis/fix13/report.json` 里的历史 `valid_slots` 仍沿用旧口径，两者不可直接相减。",
              "", "## 原始证据", "",
              "- 运行与账本：`runs/fix14`；预检与身份绑定：`runs/fix14-preflight`。",
              "- 逐运行数据：`docs/analysis/fix14/runs.json`；完整统计：`docs/analysis/fix14/report.json`。",
              "- 四格历史数据：`docs/analysis/fix13/runs.json`。", ""]
    return "\n".join(lines)


def main():
    rows = load_rows()
    batch = json.loads((launcher.OUT / "batch-report.json").read_text(encoding="utf-8"))
    history = json.loads((ROOT / "docs/analysis/fix13/report.json").read_text(encoding="utf-8"))
    cost_all = {group: history["groups"][group]["cost_cny_all_attempts"] for group in ("U", "UT")}
    cost_all["T8"] = batch["by_group"]["T8"]["cost_cny"]
    cost_all["O32"] = batch["by_group"]["O32"]["cost_cny"]

    infra_slots = []
    for path in launcher.OUT.rglob("summary.json"):
        if any(x in path.parent.name for x in (".infra", ".interrupted", ".unknown")):
            continue
        summary = json.loads(path.read_text(encoding="utf-8"))
        if summary.get("status") != "infra_error":
            continue
        delta = path.parent / "candidate-delta.json"
        infra_slots.append({"path": str(path.parent.relative_to(launcher.OUT)).replace("\\", "/"),
                            "infra_reason": summary.get("infra_reason"),
                            "stop_reason": summary.get("stop_reason"),
                            "cost_cny": summary.get("cost_cny"),
                            "delta_bytes": delta.stat().st_size if delta.is_file() else 0})
    infra_slots.sort(key=lambda item: item["path"])

    groups = {group: group_summary(rows, group, cost_all=cost_all.get(group)) for group in GROUPS}
    by_task = {task: [r for r in rows if r["task"] == task] for task in REFS}
    complete = [task for task, items in by_task.items()
                if len(items) == 8 and all(r["valid"] and r["group"] in GROUPS for r in items)
                and {r["group"] for r in items} == set(GROUPS)]
    ingest = json.loads((ROOT / "scripts/rebench/out/fix13_ingest.json").read_text(encoding="utf-8"))
    strata = {slot["final"]: slot["stratum"] for slot in ingest["slots"]}
    values = np.array([[sum(r["status"] == "pass" for r in by_task[t] if r["group"] == g) / 2
                        for g in GROUPS] for t in complete])
    costs = np.array([[sum(float(r["cost_cny"]) for r in by_task[t] if r["group"] == g)
                       for g in GROUPS] for t in complete])
    paired_report = paired(values, costs, np.array([strata[t] for t in complete])) if complete else None
    verdict_value = verdict(paired_report) if paired_report else ("no_complete_tasks", "没有四格完整的题目。")

    report = {"schema": "fix14.analysis.v1", "planned_slots": 120,
              "completed_slots": sum(1 for r in rows if r["group"] in ("T8", "O32")),
              "complete_tasks": len(complete),
              "missing_or_invalid_tasks": sorted(set(REFS) - set(complete)),
              "groups": groups, "paired": paired_report,
              "verdict": {"label": verdict_value[0], "text": verdict_value[1]},
              "infra_error_slots": len(infra_slots), "infra_slots": infra_slots,
              "batch": batch,
              "evidence_boundary": "T8/O32 are post-hoc cells on the same 30 tasks already used by FIX-13 and ET; "
                                   "U/UT come from the FIX-13 batch; public data cannot establish training non-exposure"}
    save(OUT / "runs.json", rows)
    save(OUT / "report.json", report)
    (OUT / "FIX-14-RESULTS.zh-CN.md").write_text(markdown(report), encoding="utf-8")
    print(json.dumps({"complete_tasks": len(complete),
                      "solved": {g: groups[g]["solved"] for g in GROUPS},
                      "verdict": verdict_value[0], "infra_error_slots": len(infra_slots)},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
