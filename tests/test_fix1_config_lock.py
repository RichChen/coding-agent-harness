"""Every config under configs/ keeps the config_hash and runtime fingerprint recorded before fix plan 1."""
import copy
import json
from pathlib import Path

import pytest

from src.eval.contracts import canonical_hash
from src.harness.factory import build_agent, load_config
from src.runtime.session import runtime_fingerprint
from src.tracing.store import canonical, digest

ROOT = Path(__file__).resolve().parents[1]
# Recorded at 7aa535f with the environment below.
RECORDED = json.loads((ROOT / "tests" / "fixtures" / "fix1_config_hashes.json").read_text(encoding="utf-8"))
ENV = {"LLM_API_KEY": "fix1-test-key", "LLM_BASE_URL": "https://offline.invalid/v1",
       "LLM_MODEL": "fix1-model", "LLM_MODEL_B": "fix1-model-b", "DEEPSEEK_MODEL": "fix1-flash"}
MASKING = "configs/workspace/p4-sphinx-deepseek-delayed-mask.yaml"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)


def fingerprint(config, tmp_path):
    return runtime_fingerprint(build_agent(config, tmp_path))


@pytest.mark.parametrize("rel", sorted(RECORDED))
def test_config_hash_and_fingerprint_unchanged(rel, tmp_path):
    expected = RECORDED[rel]
    config = load_config(ROOT / rel)
    assert canonical_hash(config) == expected["canonical_hash"]
    assert digest(canonical(config).encode()) == expected["session_config_hash"]
    if not expected["runtime_fingerprint"].startswith("unbuildable"):
        assert fingerprint(config, tmp_path) == expected["runtime_fingerprint"]


@pytest.mark.parametrize("section, key, value", [
    ("context", "protection_rules", "v2"),
    ("command", "observation_bytes_kept", "persisted"),
    ("context", "admission_estimate", "calibrated_v1"),
])
def test_enabling_a_new_option_changes_hash_and_fingerprint(section, key, value, tmp_path):
    base = load_config(ROOT / MASKING)
    assert key not in (base["runtime"]["context"] if section == "context" else base["policy"]["command"])
    changed = copy.deepcopy(base)
    (changed["runtime"]["context"] if section == "context" else changed["policy"]["command"])[key] = value
    assert canonical_hash(changed) != canonical_hash(base)
    assert fingerprint(changed, tmp_path / "on") != fingerprint(base, tmp_path / "off")


def test_observation_bytes_kept_reaches_every_trailer_tool(tmp_path):
    config = load_config(ROOT / "configs/docker.yaml")
    config["tools"]["enabled"] = ["read_file", "write_file", "run_command", "search_code"]
    names = {"read_file", "write_file", "run_command", "search_code"}
    tools = {t.name: t for t in build_agent(config, tmp_path / "off").tools.tools()}
    assert {n: tools[n].observation_bytes_kept for n in names} == dict.fromkeys(names, "legacy")
    config["policy"]["command"]["observation_bytes_kept"] = "persisted"
    tools = {t.name: t for t in build_agent(config, tmp_path / "on").tools.tools()}
    assert {n: tools[n].observation_bytes_kept for n in names} == dict.fromkeys(names, "persisted")
