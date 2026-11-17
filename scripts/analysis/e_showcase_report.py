"""Create a read-only, interview-friendly FIX-15 E-line summary.

The input is an explicit exported JSON row file such as
``docs/analysis/fix15/runs.json``.  The script never walks run directories,
re-grades tasks, or calls a model/Docker.
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import OrderedDict
from pathlib import Path
from typing import Any, Mapping

GROUPS = ("U-new", "N", "E-new")


def load_rows(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, Mapping) else payload
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        raise ValueError("input must be a JSON array or an object with rows")
    return [dict(row) for row in rows]


def summarize(rows: list[Mapping[str, Any]]) -> OrderedDict[str, dict[str, Any]]:
    grouped: OrderedDict[str, list[Mapping[str, Any]]] = OrderedDict(
        (group, []) for group in GROUPS
    )
    for row in rows:
        group = row.get("group")
        if group in grouped:
            grouped[group].append(row)
    if any(not grouped[group] for group in GROUPS):
        raise ValueError("input must contain U-new, N and E-new rows")

    result: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for group, items in grouped.items():
        valid = [item for item in items if item.get("valid", True)]
        result[group] = {
            "runs": len(items),
            "valid_runs": len(valid),
            "solved": sum(bool(item.get("solved")) for item in items),
            "partial_or_pass": sum(item.get("status") in {"partial", "pass"} for item in items),
            "found_idle": sum(item.get("cls") == "found_idle" for item in valid),
            "no_edit": sum(int(item.get("edit_calls", 0)) == 0 for item in valid),
            "high_repeat": sum(float(item.get("tail_repeat", 0)) >= 0.5 for item in valid),
            "edit_rate": sum(int(item.get("edit_calls", 0)) > 0 for item in valid) / len(valid),
            "mean_requests": statistics.mean(float(item.get("requests", 0)) for item in items),
            "mean_cost_cny": statistics.mean(float(item.get("cost_cny", 0)) for item in items),
        }
    return result


def render_markdown(path: str | Path, summary: Mapping[str, Mapping[str, Any]]) -> str:
    lines = [
        "# E 线展示：工具循环中的行动干预",
        "",
        "这份材料读取 FIX-15 的显式运行导出，不调用模型、不启动 Docker，也不重新评分。",
        "U-new 是无干预组，N 只要求写出下一步，E-new 要求先解释上一轮结果再说明下一步。",
        "",
        "## 一眼可见的结果",
        "",
        "| 组别 | 解出 | 可评分名额 | 找到后不编辑 | 从未编辑 | 高重复 | 可评分编辑率 | 平均模型调用 | 平均费用（元） |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for group, row in summary.items():
        lines.append(
            f"| {group} | {row['solved']}/{row['runs']} ({row['solved']/row['runs']:.1%}) "
            f"| {row['valid_runs']}/{row['runs']} | {row['found_idle']} | {row['no_edit']} | {row['high_repeat']} "
            f"| {row['edit_rate']:.1%} | {row['mean_requests']:.1f} "
            f"| {row['mean_cost_cny']:.3f} |"
        )
    lines += [
        "",
        "## 如何解释",
        "",
        "在这批固定题和这个模型上，干预最稳定地改变了行动过程：U-new 中找到目标后不编辑和高重复都很多，N 明显减少，E-new 进一步减少。",
        "最终解出率则是 U-new 28.3%、N 38.3%、E-new 40.0%；E 相对 N 的增量很小，不能据此声称 E 已经稳定提高普遍解出率。",
        "",
        "这支持一个较窄但有价值的 Harness 结论：在观察结果和下一次工具调用之间加入可见的行动约束，可能减少模型停在已找到状态或重复循环的情况。它不能单独证明模型执行了某种内部推理，也不能把差异唯一归因于提示词。",
        "",
        "## 证据边界",
        "",
        "- 数据来自 FIX-15 的 30 道固定题、单一模型、每题两次运行。",
        "- N 有一个不可评分的候选快照异常；表中行为计数排除了该无效名额，解出率主表保留全部名额。",
        "- 行为分类是离线轨迹分类，不是新的模型调用或独立验证。",
        "- 原始结果见 `docs/analysis/fix15/runs.json` 和 `docs/analysis/fix15/FIX-15-RESULTS.zh-CN.md`。",
        "",
        f"输入：`{Path(path)}`。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a read-only FIX-15 E showcase report")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        report = render_markdown(args.input, summarize(load_rows(args.input)))
        if args.output:
            args.output.write_text(report + "\n", encoding="utf-8")
        else:
            print(report)
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
