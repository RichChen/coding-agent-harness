"""Real capture/retrieval module demonstration; Docker is explicitly opt-in."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.demo_workspace_retrieval import DemoError, PROBE_PATH, run_demo
from src.workspace.control import CaptureError


@pytest.mark.asyncio
async def test_demo_rejects_existing_output_before_starting_docker(tmp_path, monkeypatch):
    import scripts.demo_workspace_retrieval as demo

    def forbidden(**kwargs):
        pytest.fail("existing output must be rejected before Docker construction")

    monkeypatch.setattr(demo, "DockerWorkspace", forbidden)
    sentinel = tmp_path / "keep.txt"
    sentinel.write_text("existing output", encoding="utf-8")
    with pytest.raises(DemoError, match="output_directory_exists"):
        await run_demo(tmp_path)
    assert list(tmp_path.iterdir()) == [sentinel]
    assert sentinel.read_text(encoding="utf-8") == "existing output"


@pytest.mark.asyncio
async def test_demo_reports_start_failure_and_confirms_cleanup(tmp_path, monkeypatch, capsys):
    import scripts.demo_workspace_retrieval as demo

    class UnavailableDaemon:
        closed = False

        async def start(self, control):
            raise CaptureError("unsupported_daemon")

        async def close(self, control):
            self.closed = True
            return True

    transport = UnavailableDaemon()
    monkeypatch.setattr(demo, "DockerWorkspace", lambda **kwargs: transport)
    output = tmp_path / "failure-report"
    report = await run_demo(output)
    assert report["status"] == "failed" and report["reason"] == "unsupported_daemon"
    assert report["cleanup"]["ok"] and transport.closed
    assert json.loads((output / "result.json").read_text(encoding="utf-8")) == report
    assert json.loads(capsys.readouterr().out)["reason"] == "unsupported_daemon"


@pytest.mark.skipif(os.environ.get("WORKSPACE_DOCKER_TESTS") != "1", reason="opt-in existing Sphinx Docker image")
def test_real_sphinx_capture_bm25_probe_lifecycle(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/demo_workspace_retrieval.py"
    output = tmp_path / "new-demo-output"
    completed = subprocess.run([sys.executable, str(script), "--output-dir", str(output)],
                               cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=330)
    print(completed.stdout)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    result = output / "result.json"
    print("DEMO_REPORT=" + str(result))
    report = json.loads(result.read_text(encoding="utf-8"))
    assert report["status"] == "passed" and report["cleanup"]["ok"]
    assert report["validation_run"] is False and report["model_calls"] == 0 and report["mode"] == "bm25"
    stages = report["stages"]
    assert [s["phase"] for s in stages] == ["baseline", "unchanged", "added", "modified", "deleted"]
    assert [s["indexed_files"] for s in stages] == [534, 534, 535, 535, 534]
    assert stages[0]["candidate_id"] == stages[1]["candidate_id"] == stages[-1]["candidate_id"]
    for identity in ("corpus_id", "index_id"):
        assert stages[0][identity] == stages[1][identity] == stages[-1][identity]
        assert len({stages[0][identity], stages[2][identity], stages[3][identity]}) == 3
    assert all(PROBE_PATH not in [h["path"] for h in q["hits"]] for q in stages[-1]["queries"])
    assert all(q["inspected_hits"] == 534 for q in stages[-1]["queries"])
