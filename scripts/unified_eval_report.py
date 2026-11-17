"""Summarize an explicitly supplied legacy export through the unified envelope.

This command is deliberately read-only with respect to the evidence source: it
opens only ``--input`` and never scans ``runs/`` or invokes an execution
backend.  The optional ``--output`` is a newly generated report file.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

# Allow ``python scripts/unified_eval_report.py`` from the repository root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.eval.unified import (
    ADAPTER_KINDS,
    AdapterRegistry,
    ManifestEntry,
    MiniAdapter,
    MockAdapter,
    load_manifest_rows,
    load_run_rows,
    normalize_run_rows,
    summarize_receipts,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Summarize an explicit export or mock manifest without running evaluation."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path,
                        help="Explicit JSON file: an array of rows or {rows: [...] }.")
    source.add_argument("--manifest", type=Path,
                        help="Explicit JSON/YAML manifest; requires --mock.")
    parser.add_argument("--mock", action="store_true",
                        help="Use in-memory MockAdapter for --manifest only.")
    parser.add_argument("--real-mini", action="store_true",
                        help="Run explicit mini manifest entries through EvalRunner; requires --config.")
    parser.add_argument("--config", type=Path,
                        help="YAML Harness config for --real-mini.")
    parser.add_argument("--task-kind", choices=("mini", "repo_pytest", "swebench"),
                        default="swebench")
    parser.add_argument("--adapter", default="legacy")
    parser.add_argument("--grader-kind", default="legacy")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown",
                        dest="output_format")
    parser.add_argument("--output", type=Path,
                        help="Optional new output path. Defaults to stdout.")
    return parser


def _markdown(summary: dict) -> str:
    filters = summary["filters"]
    lines = [
        "# Unified evaluation summary",
        "",
        f"Input: `{summary['input']}`",
        f"Task kind: `{filters['task_kind']}`; adapter: `{filters['adapter']}`; "
        f"grader: `{filters['grader_kind']}`",
        "",
        "| Group | Runs | Pass | Partial | Invalid/error | Mean cost (CNY) | Mean model calls |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summary["groups"]:
        cost = "" if row["mean_cost_cny"] is None else f"{row['mean_cost_cny']:.6g}"
        calls = "" if row["mean_model_calls"] is None else f"{row['mean_model_calls']:.6g}"
        lines.append(
            f"| {row['group']} | {row['runs']} | {row['pass']} | {row['partial']} | "
            f"{row['invalid_or_error']} | {cost} | {calls} |"
        )
    lines.extend([
        "",
        "This report reads only the explicit source; it does not scan run directories "
        "or invoke a model, Docker, real adapter, or grader.",
        "",
    ])
    return "\n".join(lines)


def build_summary(args: argparse.Namespace) -> dict:
    if args.manifest is not None:
        if args.mock and args.real_mini:
            raise ValueError("--mock and --real-mini are mutually exclusive")
        if args.real_mini:
            if args.config is None:
                raise ValueError("--real-mini requires --config")
            from src.harness.factory import build_agent, load_config
            from src.tracing.logger import TraceLogger

            config = load_config(args.config)

            def agent_factory(agent_config):
                workspace = Path(tempfile.mkdtemp(prefix="unified_mini_"))
                tracing_cfg = agent_config.get("tracing", {})
                tracer = TraceLogger(output_dir=tracing_cfg.get("output_dir", "traces"))
                return build_agent(agent_config, workspace, tracer=tracer)

            rows = load_manifest_rows(args.manifest)
            adapter = MiniAdapter(agent_factory, config=config,
                                  dataset_path=config.get("metadata", {}).get(
                                      "dataset_path", "datasets/mini_tasks.json"))
            receipts = []
            for index, raw in enumerate(rows, start=1):
                entry = ManifestEntry.from_mapping(raw)
                if entry.adapter != "mini":
                    raise ValueError("--real-mini only accepts manifest entries with adapter=mini")
                receipts.append(adapter.execute(
                    entry, run_id=str(raw.get("run_id", f"{entry.task.task_id}/real-{index}"))
                ))
            source_path = args.manifest
            source_kind = "manifest_real_mini"
            filter_task_kind = "mini"
            filter_adapter = "mini"
            filter_grader = "per_entry"
        else:
            if not args.mock:
                raise ValueError("--manifest requires --mock or --real-mini")
            rows = load_manifest_rows(args.manifest)
            registry = AdapterRegistry({name: MockAdapter(name) for name in ADAPTER_KINDS})
            receipts = []
            for index, raw in enumerate(rows, start=1):
                entry = ManifestEntry.from_mapping(raw)
                mock_result = raw.get("mock_result", raw.get("result", {"status": "unknown"}))
                if not isinstance(mock_result, dict):
                    raise ValueError(f"manifest entry {index} result must be an object")
                receipts.append(registry.execute(
                    entry,
                    run_id=str(raw.get("run_id", f"{entry.task.task_id}/mock-{index}")),
                    raw_result=mock_result,
                ))
            source_path = args.manifest
            source_kind = "manifest_mock"
            filter_task_kind = "manifest"
            filter_adapter = "mock"
            filter_grader = "per_entry"
    else:
        rows = load_run_rows(args.input)
        receipts = normalize_run_rows(
            rows, task_kind=args.task_kind, adapter=args.adapter,
            grader_kind=args.grader_kind,
        )
        source_path = args.input
        source_kind = "legacy_input"
        filter_task_kind = args.task_kind
        filter_adapter = args.adapter
        filter_grader = args.grader_kind
    return {
        "schema": "eval.unified.report.v1",
        "input": str(source_path),
        "source_kind": source_kind,
        "filters": {
            "task_kind": filter_task_kind,
            "adapter": filter_adapter,
            "grader_kind": filter_grader,
        },
        "rows": len(receipts),
        "groups": summarize_receipts(receipts),
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = build_summary(args)
        text = (json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
                if args.output_format == "json" else _markdown(summary))
        if args.output:
            args.output.write_text(text + "\n", encoding="utf-8")
        else:
            print(text)
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"unified_eval_report: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
