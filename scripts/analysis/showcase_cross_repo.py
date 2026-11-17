"""Build a read-only cross-repository coverage report from a task manifest.

This script inspects task metadata only. It never opens run directories and
never invokes an agent, Docker, or a grader. The formal-group table is the
reviewed evidence summary from the project matrix and is kept explicit so the
report cannot silently reinterpret historical results.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover - environment error
    raise SystemExit("PyYAML is required to read the task manifest") from exc


FORMAL_GROUPS = [
    {"batch": "FIX-13", "groups": "U/R/E/UT", "runs": 240, "solved": "16/60; 18/60; 26/60; 28/60", "source": "docs/analysis/fix13/FIX-13-RESULTS.zh-CN.md"},
    {"batch": "FIX-13 ET", "groups": "ET", "runs": 60, "solved": "27/60", "source": "docs/analysis/fix13-et/FIX-13-ET-RESULTS.zh-CN.md"},
    {"batch": "FIX-14", "groups": "T8/O32", "runs": 120, "solved": "26/60; 17/60", "source": "docs/analysis/fix14/FIX-14-RESULTS.zh-CN.md"},
    {"batch": "FIX-15", "groups": "U-new/N/E-new", "runs": 180, "solved": "17/60; 23/60; 24/60", "source": "docs/analysis/fix15/FIX-15-RESULTS.zh-CN.md"},
]


def load_manifest(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        raise ValueError("task manifest must contain a tasks list")
    return data


def summarize_manifest(data: dict[str, Any], source: str) -> dict[str, Any]:
    tasks = data["tasks"]
    rows = []
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise ValueError(f"task {index} is not an object")
        repo = task.get("repo")
        task_id = task.get("id")
        if not isinstance(repo, str) or not repo.strip():
            raise ValueError(f"task {index} has no repository")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError(f"task {index} has no id")
        rows.append({"id": task_id, "repo": repo})
    counts = Counter(row["repo"] for row in rows)
    return {
        "source": source,
        "task_count": len(rows),
        "unique_repo_count": len(counts),
        "duplicate_repos": {repo: n for repo, n in sorted(counts.items()) if n > 1},
        "repos": sorted(counts),
        "tasks": rows,
        "formal_groups": FORMAL_GROUPS,
    }


def as_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# 跨仓库评测展示报告",
        "",
        f"任务清单：`{summary['source']}`",
        "",
        f"该清单包含 **{summary['task_count']} 道任务，覆盖 {summary['unique_repo_count']} 个不同仓库**。",
        "任务数和仓库数分开统计；本清单中重复仓库数为 {0}。".format(len(summary["duplicate_repos"])),
        "",
        "## 已有正式结果",
        "",
        "下表只整理已经审阅过的结果，不重新计算跨批次统计，也不把不同批次合并成新的显著性检验。",
        "",
        "| 批次 | 组 | 正式运行数 | 各组解出运行 | 来源 |",
        "|---|---|---:|---|---|",
    ]
    for row in summary["formal_groups"]:
        lines.append(f"| {row['batch']} | {row['groups']} | {row['runs']} | {row['solved']} | `{row['source']}` |")
    lines.extend([
        "",
        "## 仓库清单",
        "",
        "下面是去重后的仓库身份。每个仓库在 FIX-13 任务清单中各有一条任务；这不等于所有仓库都经过同等数量的独立任务验证。",
        "",
        "| 仓库 |",
        "|---|",
    ])
    lines.extend(f"| `{repo}` |" for repo in summary["repos"])
    lines.extend([
        "",
        "## 可以展示什么",
        "",
        "- 已验证的多仓库范围是 30 个经过准备的 Python/SWE-bench 风格仓库，而不是单一仓库实验。",
        "- FIX-13、ET、FIX-14、FIX-15 在这批任务上形成了可追溯的多组比较，结果保留了每批次自己的 runner、评分器和有效性边界。",
        "- 这个范围足以支持“面向多个仓库的可复现评测 Harness”这一项目表述。",
        "",
        "## 仍不能宣称的范围",
        "",
        "- 不能宣称任意 GitHub 仓库即插即用；新仓库仍需基线提交、固定镜像、测试路径、校准和独立 grader。",
        "- 当前实际验证集中在 Python/pytest 和 SWE-bench 风格任务；JavaScript、Go、Rust 等语言没有得到同等验证。",
        "- 这 30 道题已经参与开发和正式实验；后续固定题库上的调参结果属于开发/验证证据，不是新的未见测试集。",
        "- 运行数量、仓库数量和跨批次组间统计不能互相替代；跨批次结果只作背景比较。",
        "",
        "## 证据入口",
        "",
        "- 总矩阵：`docs/analysis/ALL-EXPERIMENT-MATRIX.zh-CN.md`",
        "- FIX-13 结果：`docs/analysis/fix13/FIX-13-RESULTS.zh-CN.md`",
        "- FIX-15 结果：`docs/analysis/fix15/FIX-15-RESULTS.zh-CN.md`",
        "",
    ])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a read-only cross-repository coverage report.")
    parser.add_argument("--tasks", type=Path, required=True, help="YAML task manifest")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        summary = summarize_manifest(load_manifest(args.tasks), str(args.tasks))
        output = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) if args.format == "json" else as_markdown(summary)
        if args.output:
            args.output.write_text(output + "\n", encoding="utf-8")
        else:
            print(output)
        return 0
    except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
        print(f"showcase_cross_repo: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
