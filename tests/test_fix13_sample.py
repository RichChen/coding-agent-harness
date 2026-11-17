"""Local FIX-13 draw boundaries. No network, Docker, or model."""
from scripts.rebench.fix13_sample import build_plan


def candidate_pool():
    rows = []
    for group in ("easy", "other"):
        for index in range(40):
            rows.append({"instance_id": f"{group}__task-{index}", "repo": f"owner/{group}-{index}",
                         "created_at": "2026-01-01", "month": "2026-01", "patch_files": 1,
                         "patch_bytes": 100 if group == "easy" else 3000, "num_modified_files": 1,
                         "f2p": 1, "p2p": 0, "pre_install": False, "python": "3.12", "docker_image": "fixture"})
    return rows


def test_exposed_repository_is_excluded_case_insensitively():
    pool = candidate_pool()
    pool[1]["repo"] = "OWNER/EASY-1"
    plan = build_plan(pool, {"owner/easy-1", "owner/other-2"})
    assert all(row["repo"].casefold() not in {"owner/easy-1", "owner/other-2"}
               for rows in plan["tasks"].values() for row in rows)


def test_draw_is_stable_and_primary_repositories_are_unique():
    first = build_plan(candidate_pool(), set())
    second = build_plan(candidate_pool(), set())
    assert first == second
    primary = [r for rows in first["tasks"].values() for r in rows if r["role"] == "primary"]
    assert len(primary) == 30
    assert len({r["repo"].casefold() for r in primary}) == 30
    assert all(sum(r["role"] == "primary" for r in rows) == 15 for rows in first["tasks"].values())


def test_first_five_are_preassigned_blocks_and_have_eight_slots_each():
    plan = build_plan(candidate_pool(), set())
    assert len(plan["run_order"]) == 240
    for block, task in enumerate(plan["first_five"], 1):
        slots = [s for s in plan["run_order"] if s["block"] == block]
        assert {s["instance_id"] for s in slots} == {task["instance_id"]}
        assert {(s["group"], s["repeat"]) for s in slots} == {
            (group, repeat) for group in ("U", "R", "E", "UT") for repeat in (1, 2)}
