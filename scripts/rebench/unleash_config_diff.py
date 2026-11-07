"""Every difference between the unleash pilot configs, checked against the declared list.

Each profile is loaded and validated, then expanded to every schema field (defaults included). Three pairs are
compared: A against b0-v2, U against A, U+T against U. Each pair's differences must equal the declared set in
docs/analysis/UNLEASH-PILOT-DESIGN.zh-CN.md (table 2.1 and revisions R2-R4, R6), value for value.

    unleash_config_diff.py [--out docs/analysis/fix12/unleash-config-diff.json]

Exit status 1 when any pair has an undeclared or missing difference. The API key is never read: it is replaced
by a placeholder before the configs are loaded.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.config.schema import HarnessConfig  # noqa: E402
from src.harness.factory import load_config  # noqa: E402

B0_V2 = ROOT / "configs" / "sprint" / "b0-v2.yaml"
PROFILES = {g: ROOT / "configs" / "rebench" / f"unleash-{g}.yaml" for g in ("a", "u", "ut")}
OUT = ROOT / "docs" / "analysis" / "fix12" / "unleash-config-diff.json"

SHARED = {  # every group, on top of b0-v2
    "retry.stop_on_unknown_usage": (False, True),
    "tracing.request_telemetry": (False, True),
    "tracing.session_artifact_bytes": (64 * 1024 * 1024, 512 * 1024 * 1024),
    "tracing.session_artifact_item_bytes": (2 * 1024 * 1024, 8 * 1024 * 1024),
    "policy.command.export_keep_original_files": (False, True),
    "runtime.budget.hide_remaining_calls": (False, True),
}
UNLEASH = {  # U against A
    "runtime.max_steps": (60, 120),
    "runtime.step_timeout_seconds": (300, 900),
    "runtime.total_timeout_seconds": (2400, 10800),
    "runtime.context.max_tokens": (96000, 1100000),
    "runtime.context.compact_token_threshold": (None, 880000),
    "runtime.context.admission_estimate": ("utf8_bytes", "calibrated_v1"),
    "runtime.context.compact_trigger_estimate": ("bytes_div_4", "admission"),
    "runtime.budget.max_model_calls": (40, 100),
}
THINKING = {  # U+T against U
    "llm.thinking": ("disabled", "enabled"),
    "llm.reasoning_effort": (None, "high"),
    "llm.max_tokens": (8192, 32768),
}
PAIRS = (("b0-v2", "a", SHARED), ("a", "u", UNLEASH), ("u", "ut", THINKING))


def expand(model: BaseModel, prefix: str = "") -> dict:
    """Every field of the validated config, defaults included, keyed by dotted path."""
    out = {}
    for name in type(model).model_fields:
        value = getattr(model, name)
        key = prefix + name
        if isinstance(value, BaseModel):
            out.update(expand(value, key + "."))
        else:
            out[key] = value
    return out


def load(path: Path) -> dict:
    return expand(HarnessConfig.model_validate(load_config(path)))


def compare(before: dict, after: dict) -> dict:
    keys = sorted(set(before) | set(after))
    return {key: (before.get(key), after.get(key)) for key in keys if before.get(key) != after.get(key)}


def check(configs: dict) -> dict:
    pairs = []
    for left, right, declared in PAIRS:
        actual = compare(configs[left], configs[right])
        undeclared = {k: v for k, v in actual.items() if k not in declared}
        missing = {k: v for k, v in declared.items() if actual.get(k) != tuple(v)}
        pairs.append({"before": left, "after": right,
                      "differences": {k: list(v) for k, v in actual.items()},
                      "undeclared": {k: list(v) for k, v in undeclared.items()},
                      "declared_but_not_found": {k: list(v) for k, v in missing.items()},
                      "ok": not undeclared and not missing})
    return {"schema": "unleash-pilot.config-diff.v1",
            "profiles": {g: p.relative_to(ROOT).as_posix() for g, p in {"b0-v2": B0_V2, **PROFILES}.items()},
            "fields_compared": len(configs["a"]), "pairs": pairs, "ok": all(p["ok"] for p in pairs),
            "launcher_shared": "per-run CNY cap, settlement and pricing are set by scripts/rebench/run_unleash.py, "
                               "the same for every group"}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    os.environ["LLM_API_KEY"] = "config-diff-placeholder"
    configs = {"b0-v2": load(B0_V2), **{g: load(p) for g, p in PROFILES.items()}}
    report = check(configs)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    for pair in report["pairs"]:
        print(pair["before"], "->", pair["after"], "ok" if pair["ok"] else "MISMATCH", len(pair["differences"]))
    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
