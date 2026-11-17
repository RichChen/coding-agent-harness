"""FIX-14 runner bindings. No Docker, network, or model calls."""

import pytest

from scripts.rebench import run_fix14 as runner


def test_runner_uses_two_new_factorial_cells():
    assert runner.config_report()["ok"]
    assert runner.GROUPS == ("T8", "O32")
    assert runner.EXPECTED_RUNS == 120


def test_child_spec_preserves_group_and_config():
    spec = runner.child_spec({"id": "task"}, "T8", runner.OUT / "x", mock=True)
    assert spec["group"] == "T8" and spec["mock"] is True
    with pytest.raises(ValueError, match="unknown FIX-14 group"):
        runner.child_spec({"id": "task"}, "UT", runner.OUT / "x", mock=True)
