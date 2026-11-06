"""Non-preregistered description for A6-R: stop_reason and status by arm, plus the 402 reruns.

Descriptive only. No tests, no rates, no solved-by-arm summaries beyond what each listed run shows.
Reads a6-retry-runs.json (stop_reason, status, model_calls) and, optionally, the harness list of
slots retired after provider HTTP 402.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.analyze_a6_retry import load_runs  # noqa: E402
from scripts.handoff_sha import handoff_sha256  # noqa: E402

ARMS = ("r0", "r1")
LISTED = ("model_parse_error", "context_overflow")
# Stop labels the harness writes since its failure-label fix (846e3a0). Before it, CNY-cap stops
# were recorded as model_parse_error and provider failures had no stop_reason of their own.
PAID_STOPS = ("budget_exhausted", "cny_hard_cap", "cny_call_cap")
PROVIDER_STOPS = ("provider_network", "provider_incomplete", "model_call_error")


def _is_new_label(reason: object) -> bool:
    return isinstance(reason, str) and (
        reason in PAID_STOPS or reason in PROVIDER_STOPS or reason.startswith("provider_http_")
    )


def _slot(row: dict) -> str:
    return f"{row['task_id']}/{row['arm']}/{row['first']}/r{row['k']}"


def _table(rows: list[dict], key: str) -> list[str]:
    counts = {arm: Counter(str(row.get(key)) for row in rows if row["arm"] == arm) for arm in ARMS}
    values = sorted(
        set(counts["r0"]) | set(counts["r1"]),
        key=lambda v: (-sum(c[v] for c in counts.values()), v),
    )
    lines = [f"| {key} | R0 | R1 |", "| --- | --- | --- |"]
    for value in values:
        lines.append(f"| {value} | {counts['r0'][value]} | {counts['r1'][value]} |")
    lines.append(f"| 合计 | {sum(counts['r0'].values())} | {sum(counts['r1'].values())} |")
    return lines


def _listing(rows: list[dict], reason: str) -> list[str]:
    chosen = sorted((row for row in rows if row.get("stop_reason") == reason), key=_slot)
    if not chosen:
        return ["无"]
    lines = ["| 槽位 | 组 | model_calls | 最终 status |", "| --- | --- | --- | --- |"]
    for row in chosen:
        lines.append(
            f"| {_slot(row)} | {row['arm'].upper()} | {row.get('model_calls')} | {row.get('status')} |"
        )
    return lines


def _retired(path: Path) -> list[str]:
    items = json.loads(path.read_text(encoding="utf-8"))
    by_arm = {
        arm: sorted(item["slot"] for item in items if item["slot"].split("/")[1] == arm)
        for arm in ARMS
    }
    lines = [
        f"- 退役记录 `{path.name}`：{len(items)} 个槽位，R0 {len(by_arm['r0'])} 个，R1 {len(by_arm['r1'])} 个。"
    ]
    for arm in ARMS:
        lines.append(f"- {arm.upper()}：" + "、".join(by_arm[arm]))
    return lines


def render(runs_path: Path, retired_path: Path | None) -> str:
    payload = load_runs(runs_path)
    rows = payload["runs"]
    lines = [
        "# A6-R stop_reason 和 status 按组分布（非预注册，描述性）",
        "",
        f"输入：`a6-retry-runs.json`，handoff_sha256 `{handoff_sha256(runs_path)}`，{len(rows)} 行。",
        "只读每行的 `stop_reason`、`status`、`model_calls`。不做检验。",
        "",
        "## stop_reason",
        "",
        *_table(rows, "stop_reason"),
        "",
        "## status",
        "",
        *_table(rows, "status"),
    ]
    for reason in LISTED:
        lines.extend(["", f"## stop_reason = {reason} 的运行", "", *_listing(rows, reason)])
    new_labels = sorted({row["stop_reason"] for row in rows if _is_new_label(row.get("stop_reason"))})
    for reason in new_labels:
        kind = "人民币上限停机" if reason in PAID_STOPS else "服务商错误停机"
        lines.extend(["", f"## stop_reason = {reason} 的运行（{kind}）", "", *_listing(rows, reason)])
    if retired_path is not None:
        lines.extend(["", "## provider 402 退役的槽位", "", *_retired(retired_path)])
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--retired", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(args.runs, args.retired), encoding="utf-8")
    print(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
