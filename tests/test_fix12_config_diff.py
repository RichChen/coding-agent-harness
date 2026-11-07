import copy

import pytest

from scripts.rebench import unleash_config_diff as cd


@pytest.fixture(scope="module")
def configs():
    mp = pytest.MonkeyPatch()
    mp.setenv("LLM_API_KEY", "config-diff-placeholder")
    try:
        yield {"b0-v2": cd.load(cd.B0_V2), **{g: cd.load(p) for g, p in cd.PROFILES.items()}}
    finally:
        mp.undo()


def test_profiles_match_declared_differences(configs):
    report = cd.check(configs)
    assert report["ok"], [p for p in report["pairs"] if not p["ok"]]
    assert [len(p["differences"]) for p in report["pairs"]] == [len(cd.SHARED), len(cd.UNLEASH), len(cd.THINKING)]


def test_undeclared_difference_is_caught(configs):
    perturbed = copy.deepcopy(configs)
    perturbed["u"]["runtime.context.keep_recent_tool_results"] = 99
    report = cd.check(perturbed)
    assert not report["ok"]
    assert "runtime.context.keep_recent_tool_results" in report["pairs"][1]["undeclared"]


def test_missing_declared_difference_is_caught(configs):
    perturbed = copy.deepcopy(configs)
    perturbed["ut"]["llm.reasoning_effort"] = None
    report = cd.check(perturbed)
    assert not report["ok"]
    assert "llm.reasoning_effort" in report["pairs"][2]["declared_but_not_found"]
