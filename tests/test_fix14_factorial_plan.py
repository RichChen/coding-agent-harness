"""FIX-14 2x2 plan tests. No Docker, network, or model calls."""

from scripts.rebench.fix14_factorial_plan import GROUPS, build_plan


def base_plan():
    tasks = {"easy": [{"instance_id": f"task-{i}", "role": "primary"} for i in range(1, 16)],
             "other": [{"instance_id": f"task-{i}", "role": "primary"} for i in range(16, 31)]}
    return {"tasks": tasks}


def test_plan_has_two_new_factorial_cells_and_two_repeats():
    plan = build_plan(base_plan())
    assert len(plan["run_order"]) == 120
    assert set(plan["rules"]["groups"]) == set(GROUPS)
    for block in range(1, 31):
        rows = [row for row in plan["run_order"] if row["block"] == block]
        assert len(rows) == 4
        assert {(row["group"], row["repeat"]) for row in rows} == {
            (group, repeat) for group in GROUPS for repeat in (1, 2)
        }


def test_plan_is_deterministic():
    assert build_plan(base_plan())["run_order"] == build_plan(base_plan())["run_order"]
