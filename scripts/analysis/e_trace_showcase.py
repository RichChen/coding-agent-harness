"""Render a task-level U/N/E trace summary from an explicit FIX-15 export.

This is a read-only presentation helper.  It uses the already classified row
fields in ``runs.json`` and does not open raw session directories, re-grade a
task, or call a model/Docker.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

GROUPS = ("U-new", "N", "E-new")
FIELDS = (
    "repeat", "status", "solved", "valid", "located_request",
    "first_edit_request", "edit_calls", "tail_repeat", "requests", "cost_cny",
)


def load_rows(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, Mapping) else payload
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        raise ValueError("input must be a JSON array or an object with rows")
    return [dict(row) for row in rows]


def select_task(rows: list[Mapping[str, Any]], task_id: str | None = None) -> str:
    by_task: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        task = row.get("task")
        group = row.get("group")
        if isinstance(task, str) and group in GROUPS:
            by_task[task].add(group)
    complete = sorted(task for task, groups in by_task.items() if set(GROUPS) <= groups)
    if task_id is not None:
        if task_id not in complete:
            raise ValueError("requested task must have U-new, N and E-new rows")
        return task_id
    if not complete:
        raise ValueError("input has no task represented in all three groups")
    return complete[0]


def task_rows(rows: list[Mapping[str, Any]], task_id: str) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {group: [] for group in GROUPS}
    for row in rows:
        if row.get("task") == task_id and row.get("group") in result:
            result[str(row["group"])].append(dict(row))
    for group in GROUPS:
        result[group].sort(key=lambda row: str(row.get("repeat", "")))
    return result


def _value(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    if value is None:
        return "—"
    if field == "tail_repeat":
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return str(value)
    if field == "cost_cny":
        try:
            return f"{float(value):.3f}"
        except (TypeError, ValueError):
            return str(value)
    if field == "solved" or field == "valid":
        return "是" if bool(value) else "否"
    return str(value)


def render_markdown(path: str | Path, task_id: str, grouped: Mapping[str, list[Mapping[str, Any]]]) -> str:
    lines = [
        "# E 线逐题轨迹展示",
        "",
        f"任务：`{task_id}`",
        "",
        "下表把同一道题在 U-new、N、E-new 中的两次运行并列展示。字段来自 FIX-15 的显式运行摘要，",
        "不是完整的逐步对话回放；它只用于展示定位、编辑和重复行为如何变化。",
        "",
        "| 组别 | 重复 | 状态 | 解出 | 有效 | 定位请求 | 首次编辑请求 | 编辑调用 | 尾部重复率 | 总请求 | 费用（元） |",
        "|---|---|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for group in GROUPS:
        for row in grouped[group]:
            lines.append(
                f"| {group} | {_value(row, 'repeat')} | {_value(row, 'status')} | "
                f"{_value(row, 'solved')} | {_value(row, 'valid')} | {_value(row, 'located_request')} | "
                f"{_value(row, 'first_edit_request')} | {_value(row, 'edit_calls')} | "
                f"{_value(row, 'tail_repeat')} | {_value(row, 'requests')} | {_value(row, 'cost_cny')} |"
            )
    lines += [
        "",
        "## 如何读这张表",
        "",
        "`located_request` 表示离线分类器认为模型已经定位到目标的位置，`first_edit_request` 表示第一次编辑调用，",
        "`edit_calls` 表示编辑类工具调用次数，`tail_repeat` 表示轨迹尾部重复程度。它们是行为摘要，不是模型内部思维的直接观测。",
        "",
        "任务由脚本按任务 ID 排序后选择第一个同时包含三组记录的任务；也可以用 `--task` 指定任务，避免把展示样例误当成随机抽样。",
        "",
        "证据来源：`docs/analysis/fix15/runs.json`。命令不会读取原始 session，不会重新评分，不会启动模型或 Docker。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a read-only task-level U/N/E trace summary")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--task", help="Explicit task ID; default is the first complete task by ID")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        rows = load_rows(args.input)
        task = select_task(rows, args.task)
        report = render_markdown(args.input, task, task_rows(rows, task))
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
