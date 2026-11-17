"""Read-only import helpers for stable legacy result exports.

The importer intentionally accepts an explicit JSON array of run summaries
(the shape used by ``runs.json`` exports).  It never walks run directories and
never rewrites source evidence.  Raw rows remain embedded in each receipt.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from .contracts import RunReceipt, TaskSpec, normalize_legacy_result


def load_run_rows(path: str | Path) -> list[dict[str, Any]]:
    """Load an explicit JSON array or ``{"rows": [...]}`` export."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, Mapping) else payload
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        raise ValueError("legacy export must be a JSON array of objects or an object with rows")
    return [dict(row) for row in rows]


def normalize_run_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    task_kind: str = "swebench",
    adapter: str = "legacy",
    grader_kind: str = "legacy",
    grader_version: str = "legacy",
    runner_version: str = "legacy",
    model: str | None = None,
) -> list[RunReceipt]:
    """Wrap rows without recomputing their status or changing raw fields."""
    receipts: list[RunReceipt] = []
    for index, raw in enumerate(rows):
        task_id = raw.get("task") or raw.get("task_id") or f"legacy-{index + 1}"
        group = str(raw.get("group", "legacy"))
        repeat = str(raw.get("repeat", index + 1))
        task = TaskSpec(
            task_id=str(task_id),
            kind=task_kind,
            repo_id=str(task_id).split("-", 1)[0] if "-" in str(task_id) else None,
            execution_profile="docker-linux" if task_kind != "mini" else "host",
            grader_profile=grader_kind,
            metadata={"legacy_group": group},
        )
        receipts.append(normalize_legacy_result(
            raw,
            task=task,
            run_id=f"{task_id}/{group}/{repeat}",
            adapter=adapter,
            grader_kind=grader_kind,
            grader_version=grader_version,
            config_name=group,
            model=model,
            intervention_policy=group,
            runner_version=runner_version,
            provenance={"source": "legacy_export", "row_index": index},
        ))
    return receipts


def summarize_receipts(receipts: Iterable[RunReceipt]) -> list[dict[str, Any]]:
    """Return a small group summary suitable for a report table."""
    groups: dict[str, list[RunReceipt]] = defaultdict(list)
    for receipt in receipts:
        groups[receipt.run.config_name].append(receipt)
    result = []
    for group, items in sorted(groups.items()):
        costs = [float(item.cost["cost_cny"]) for item in items if "cost_cny" in item.cost]
        calls = [int(item.cost["model_calls"]) for item in items if "model_calls" in item.cost]
        result.append({
            "group": group,
            "runs": len(items),
            "pass": sum(item.status == "pass" for item in items),
            "partial": sum(item.status == "partial" for item in items),
            "invalid_or_error": sum(item.status in {"invalid_env", "error", "unknown"} for item in items),
            "mean_cost_cny": sum(costs) / len(costs) if costs else None,
            "mean_model_calls": sum(calls) / len(calls) if calls else None,
        })
    return result
