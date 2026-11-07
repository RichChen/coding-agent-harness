"""Session artifact quotas can be raised for long runs; defaults and config hashes stay as they were."""
import pytest
from pydantic import ValidationError

from src.config.schema import TracingConfig
from src.runtime.session import RunSession
from tests.fixtures.recovery_worker import expected

MiB = 1024 * 1024


def test_defaults_are_unchanged_and_left_out_of_the_dump():
    config = TracingConfig()
    assert config.session_artifact_bytes == 64 * MiB
    assert config.session_artifact_item_bytes == 2 * MiB
    dumped = config.model_dump()
    assert "session_artifact_bytes" not in dumped and "session_artifact_item_bytes" not in dumped


def test_raised_quotas_appear_in_the_dump():
    dumped = TracingConfig(session_artifact_bytes=512 * MiB, session_artifact_item_bytes=8 * MiB).model_dump()
    assert dumped["session_artifact_bytes"] == 512 * MiB
    assert dumped["session_artifact_item_bytes"] == 8 * MiB


@pytest.mark.parametrize("fields", [
    {"session_artifact_bytes": 4 * MiB},
    {"session_artifact_item_bytes": 1024},
    {"session_artifact_bytes": 32 * MiB, "session_artifact_item_bytes": 64 * MiB},
])
def test_quotas_below_the_floor_or_inverted_are_rejected(fields):
    with pytest.raises(ValidationError):
        TracingConfig(**fields)


def test_session_create_keeps_the_old_quotas_by_default(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with RunSession.create(tmp_path / "run", task_id="t", expected=expected(workspace)) as session:
        assert session.artifacts.limit_bytes == 64 * MiB
        assert session.artifacts.item_limit_bytes == 2 * MiB
        quotas = session.state.manifest["quotas"]
        assert quotas["artifact_bytes"] == 64 * MiB and quotas["artifact_item_bytes"] == 2 * MiB


def test_raised_quotas_are_recorded_and_survive_resume(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with RunSession.create(tmp_path / "run", task_id="t", expected=expected(workspace),
                           artifact_limit_bytes=256 * MiB, artifact_item_limit_bytes=8 * MiB) as session:
        assert session.artifacts.limit_bytes == 256 * MiB
        assert session.state.manifest["quotas"]["artifact_bytes"] == 256 * MiB
    with RunSession.resume(tmp_path / "run", expected=expected(workspace)) as session:
        assert session.artifacts.limit_bytes == 256 * MiB
        assert session.artifacts.item_limit_bytes == 8 * MiB


def test_runner_passes_only_configured_quotas():
    from scripts.run_swe_task import session_limits

    assert session_limits({"tracing": {}}) == {}
    assert session_limits({}) == {}
    assert session_limits({"tracing": {"session_artifact_bytes": 256 * MiB, "session_artifact_item_bytes": 8 * MiB}}) == {
        "artifact_limit_bytes": 256 * MiB, "artifact_item_limit_bytes": 8 * MiB}
