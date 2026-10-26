"""CR-2 harness changes: b0-v2 stays byte-identical, retrieve_code is opt-in and needs a retrieval service."""
import json
from pathlib import Path

import pytest

from scripts import cr2_compat
from src.harness.factory import build_agent, load_config
from src.tools.retrieve import TaskRetrieval

ROOT = Path(__file__).resolve().parents[1]
B0 = ROOT / "configs" / "sprint" / "b0-v2.yaml"
T = ROOT / "configs" / "retrieval" / "cr2-t.yaml"


def _pool_available() -> bool:
    try:
        from scripts.run_a5_main import accepted_tasks, load_targets

        task = accepted_tasks(load_targets())[0]
        return (ROOT / task["private"]).is_file()
    except (Exception, SystemExit):
        return False


def test_b0v2_agent_identical_to_baseline():
    baseline = json.loads(cr2_compat.FIXTURE.read_text(encoding="utf-8"))
    assert cr2_compat.agent_snapshot() == baseline["agent"]


@pytest.mark.skipif(not _pool_available(), reason="A5 pool private files are not in this checkout")
def test_b0v2_prompts_identical_to_baseline():
    baseline = json.loads(cr2_compat.FIXTURE.read_text(encoding="utf-8"))
    assert len(baseline["prompts"]) == 56
    assert cr2_compat.prompt_snapshot() == baseline["prompts"]


def test_cr2_t_is_b0v2_plus_retrieve_code():
    b0 = B0.read_text(encoding="utf-8").splitlines()
    t = T.read_text(encoding="utf-8").splitlines()
    assert len(b0) == len(t)
    diff = [(a, b) for a, b in zip(b0, t) if a != b]
    assert len(diff) == 1
    old, new = diff[0]
    assert new == old.replace("]", ", retrieve_code]") and new.strip().startswith("enabled:")
    b0_cfg, t_cfg = load_config(B0), load_config(T)
    assert t_cfg["tools"]["enabled"] == b0_cfg["tools"]["enabled"] + ["retrieve_code"]
    t_cfg["tools"]["enabled"] = b0_cfg["tools"]["enabled"]
    assert t_cfg == b0_cfg


def test_no_existing_config_enables_retrieve_code():
    for path in (ROOT / "configs").rglob("*.yaml"):
        if path != T:
            assert "retrieve_code" not in path.read_text(encoding="utf-8"), path


def _agent(profile, tmp_path, **kwargs):
    config = load_config(profile)
    config["llm"]["api_key"] = "test"
    return build_agent(config, tmp_path, **kwargs)


def test_retrieve_code_requires_service(tmp_path):
    with pytest.raises(ValueError, match="retrieve_code"):
        _agent(T, tmp_path)


def test_t_tools_are_b0_plus_retrieve_code_in_docker_mode(tmp_path):
    retrieval = TaskRetrieval(None, tmp_path / "calls.jsonl")
    t_agent = _agent(T, tmp_path, retrieval=retrieval)
    b0_agent = _agent(B0, tmp_path, retrieval=retrieval)
    names = lambda agent: [s["function"]["name"] for s in agent.tools.schemas(agent.enabled_tools)]  # noqa: E731
    assert names(t_agent) == names(b0_agent) + ["retrieve_code"]
    assert t_agent.system_prompt == b0_agent.system_prompt
    assert t_agent.tools.get("retrieve_code").retrieval is retrieval
    assert cr2_compat.agent_snapshot()["schemas_json"] == json.dumps(
        b0_agent.tools.schemas(b0_agent.enabled_tools), ensure_ascii=False)


def test_tool_error_inventory_knows_retrieve_code():
    from src.eval import tool_errors

    assert "retrieve_code" in tool_errors.HARNESS_TOOLS
    assert tool_errors.tool_schemas()["retrieve_code"]["required"] == ["query"]
