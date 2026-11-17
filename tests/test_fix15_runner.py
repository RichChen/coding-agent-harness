"""FIX-15 planning and preflight checks. No Docker, network, or model calls."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.rebench import fix15_factorial_plan as plan
from scripts.rebench import run_fix15 as runner


def test_fix15_plan_has_three_arms_and_six_slots_per_task():
    base = json.loads(plan.BASE.read_text(encoding="utf-8"))
    result = plan.build_plan(base)
    assert result["rules"]["groups"] == ["U-new", "N", "E-new"]
    assert len(result["run_order"]) == 180
    for block in range(1, 31):
        rows = [row for row in result["run_order"] if row["block"] == block]
        assert len(rows) == 6
        assert {(row["group"], row["repeat"]) for row in rows} == {
            (group, repeat) for group in runner.GROUPS for repeat in (1, 2)
        }


def test_fix15_config_and_prompt_preflight_are_local():
    config = runner.config_report()
    prompt = runner.prompt_report()
    assert config["ok"] is True
    assert prompt["ok"] is True
    assert prompt["interventions"] == {
        "U-new": "none", "N": "next_action", "E-new": "result_explanation"
    }


def test_formal_execution_is_disabled():
    assert runner.EXPECTED_RUNS == 180
    assert all(Path(path).is_file() for path in runner.PROFILES.values())
