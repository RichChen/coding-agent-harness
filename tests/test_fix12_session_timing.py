"""Revision R8: each run reports the wall time spent recording the session."""
from src.runtime import session as session_module
from src.runtime.session import RunSession
from tests.fixtures.recovery_worker import expected


def test_append_seconds_accumulates_every_append(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with RunSession.create(tmp_path / "run", task_id="t", expected=expected(workspace)) as session:
        assert session.append_seconds == 0.0
        clock = iter([10.0, 10.5, 20.0, 21.25])
        monkeypatch.setattr(session_module.time, "perf_counter", lambda: next(clock))
        session.append("plugin.state", {"name": "a", "state": {}})
        session.save_loop({"messages": [], "step_number": 1})
        assert session.append_seconds == 1.75
