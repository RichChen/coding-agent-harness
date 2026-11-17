import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "unified_eval_report.py"


def test_unified_eval_report_cli_json_is_read_only(tmp_path):
    source = tmp_path / "rows.json"
    source.write_text(json.dumps([
        {"task": "mini-1", "group": "U", "repeat": "r1", "solved": True,
         "cost_cny": 0.2, "model_calls": 4},
        {"task": "mini-1", "group": "U", "repeat": "r2", "status": "fail",
         "cost_cny": 0.4, "model_calls": 8},
        {"task": "mini-1", "group": "E", "repeat": "r1", "status": "partial",
         "cost_cny": 0.1, "model_calls": 2},
    ]), encoding="utf-8")
    before = source.read_bytes()
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(source), "--task-kind", "mini",
         "--adapter", "mini", "--grader-kind", "builtin", "--format", "json"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["rows"] == 3
    assert payload["filters"]["task_kind"] == "mini"
    assert {row["group"] for row in payload["groups"]} == {"E", "U"}
    assert source.read_bytes() == before


def test_unified_eval_report_cli_mock_manifest_json(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"entries": [
        {"task": {"id": "mini-1", "kind": "mini"}, "adapter": "mini",
         "grader": {"kind": "builtin", "version": "v1"},
         "config_name": "demo", "mock_result": {"status": "pass", "cost_cny": 0.1}},
        {"task": {"id": "repo-1", "kind": "repo_pytest"}, "adapter": "repo_pytest",
         "grader": "repo_pytest", "config_name": "demo", "mock_result": {"status": "partial"}},
    ]}), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--manifest", str(manifest), "--mock", "--format", "json"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["source_kind"] == "manifest_mock"
    assert payload["rows"] == 2
    assert {row["group"] for row in payload["groups"]} == {"demo"}


def test_manifest_requires_explicit_mock(tmp_path):
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("entries:\n  - task: {id: mini-1, kind: mini}\n    adapter: mini\n    grader: builtin\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--manifest", str(manifest)],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2
    assert "requires --mock" in result.stderr


def test_real_mini_requires_config_without_calling_model(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"entries": [
        {"task": {"id": "mini-1", "kind": "mini"},
         "adapter": "mini", "grader": "builtin"},
    ]}), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--manifest", str(manifest), "--real-mini"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2
    assert "requires --config" in result.stderr
