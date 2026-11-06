"""Fit and check the calibrated_v1 admission estimate on requests.jsonl. Read-only: no model calls.

calibrated_v1 estimates ceil(ascii_bytes / D) + non_ascii_bytes + allowance, where allowance is the
default protocol allowance (512 + 32 per message + 128 per tool). A request is underestimated when the
estimate is below the provider prompt_tokens, i.e. when D > ascii / (prompt_tokens - non_ascii - allowance).
D is the smallest such ratio over all paired requests, times MARGIN, rounded down to 0.05.

Writes admission_summary.json next to this script.
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from src.runtime.window import CALIBRATED_ASCII_BYTES_PER_TOKEN  # noqa: E402

MARGIN = 0.9
MODEL_WINDOW = 1_000_000  # DeepSeek official context length
QUANTILES = (0.0, 0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99, 1.0)


def allowance(row) -> int:
    return 512 + 32 * row["n_messages"] + 128 * row["n_tools"]


def default_estimate(row) -> int:
    return row["ascii_bytes"] + row["non_ascii_bytes"] + allowance(row)


def calibrated(row, d: float) -> int:
    return math.ceil(row["ascii_bytes"] / d) + row["non_ascii_bytes"] + allowance(row)


def implied(row):
    room = row["prompt_tokens"] - row["non_ascii_bytes"] - allowance(row)
    return row["ascii_bytes"] / room if room > 0 else None


def quantiles(values):
    values = sorted(values)
    if not values:
        return None
    return {f"p{int(q * 100)}": round(values[min(int(q * (len(values) - 1) + 0.5), len(values) - 1)], 4) for q in QUANTILES}


def fit(rows) -> float:
    ratios = [r for r in map(implied, rows) if r is not None]
    return min(ratios)


def round_down(d: float) -> float:
    return math.floor(d * 20) / 20


def accuracy(rows, estimate) -> dict:
    ratio = [estimate(r) / r["prompt_tokens"] for r in rows]
    error = [estimate(r) - r["prompt_tokens"] for r in rows]
    under = [r for r in rows if estimate(r) < r["prompt_tokens"]]
    worst = min(rows, key=lambda r: estimate(r) / r["prompt_tokens"])
    return {
        "requests": len(rows),
        "ratio_estimate_over_prompt_tokens": quantiles(ratio),
        "error_tokens": quantiles(error),
        "underestimated": len(under),
        "underestimated_rate": round(len(under) / len(rows), 6) if rows else None,
        "worst_case": {"run": worst["run"], "step": worst["step"], "prompt_tokens": worst["prompt_tokens"],
                       "estimate": estimate(worst), "ratio": round(estimate(worst) / worst["prompt_tokens"], 4)},
    }


def main(path: Path) -> None:
    rows = [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]
    paired = [r for r in rows if r["kind"] == "prepared"]
    rejected = [r for r in rows if r["kind"] == "rejected"]
    by_exp = defaultdict(list)
    for r in paired:
        by_exp[r["experiment"]].append(r)

    d_min = fit(paired)
    d = round_down(d_min * MARGIN)

    held_out = {}
    for exp in sorted(by_exp):
        train = [r for r in paired if r["experiment"] != exp]
        d_train = round_down(fit(train) * MARGIN)
        test = by_exp[exp]
        held_out[exp] = {"d_fitted_on_others": d_train, "underestimated": sum(calibrated(r, d_train) < r["prompt_tokens"] for r in test),
                         "requests": len(test)}

    limits = sorted({r["max_tokens"] for r in rows if r.get("max_tokens")})
    result = {
        "schema": "fix2.admission-summary.v1",
        "source_rows": {"prepared_paired": len(paired), "rejected": len(rejected),
                        "by_experiment": {e: len(v) for e, v in sorted(by_exp.items())},
                        "runs": len({r["run"] for r in rows}),
                        "models": sorted({r["model"] or "unknown" for r in rows})},
        "implied_ascii_bytes_per_token": quantiles([x for x in map(implied, paired) if x is not None]),
        "rows_any_d_safe": sum(implied(r) is None for r in paired),
        "d_max_no_underestimate": round(d_min, 4),
        "margin": MARGIN,
        "d_chosen": d,
        "d_in_window_py": CALIBRATED_ASCII_BYTES_PER_TOKEN,
        "held_out_by_experiment": held_out,
        "default_utf8_bytes": {"all": accuracy(paired, default_estimate),
                               **{e: accuracy(v, default_estimate) for e, v in sorted(by_exp.items())}},
        "calibrated_v1": {"all": accuracy(paired, lambda r: calibrated(r, d)),
                          **{e: accuracy(v, lambda r: calibrated(r, d)) for e, v in sorted(by_exp.items())}},
        "rejected_requests": [
            {"run": r["run"], "step": r["step"], "rebuilt_matches": r["rebuilt_matches"],
             "default_estimate": r["admission_input_tokens"], "rebuilt_default_estimate": default_estimate(r),
             "calibrated_estimate": calibrated(r, d), "reserved_output_tokens": r["reserved_output_tokens"],
             "max_tokens": r["max_tokens"], "previous_prompt_tokens": r.get("previous_prompt_tokens"),
             "admitted_by_calibrated": calibrated(r, d) + (r["reserved_output_tokens"] or 0) <= r["max_tokens"]}
            for r in rejected],
        "window_check": {
            "configured_limits": limits,
            "model_window": MODEL_WINDOW,
            "max_prompt_tokens_seen": max(r["prompt_tokens"] for r in paired),
            "byte_bound_per_limit": {str(lim): math.floor(d * lim) for lim in limits},
            "bound_below_model_window": all(d * lim < MODEL_WINDOW for lim in limits),
        },
    }
    out = HERE / "admission_summary.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "requests.jsonl")
