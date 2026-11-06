"""ExecutionResult.bytes_kept counts each kept byte once (fix plan 1, F4).

bytes_kept is the bytes persisted to the per-tool artifact files, as the
workspace backend already reports; bytes_kept_memory is the in-memory preview.
The model-visible trailer keeps the old sum unless
policy.command.observation_bytes_kept is "persisted".
"""
import asyncio
import sys

import pytest

from src.config.schema import CommandConfig
from src.execution.base import ExecutionRequest, ExecutionStatus, OutputQuotas
from src.execution.docker import DockerExecutionBackend
from src.execution.local import LocalExecutionBackend
from src.tools.base import ToolContext
from src.tools.command import RunCommandTool

OUT, ERR = b"o" * 300, b"e" * 120


def script(out, err):
    return f"import sys; sys.stdout.write({out.decode()!r}); sys.stderr.write({err.decode()!r})"


async def run_local(tmp_path, out, err, *, artifacts=True, quotas=None):
    request = ExecutionRequest(argv=[sys.executable, "-c", script(out, err)], cwd=tmp_path,
                               artifact_dir=tmp_path / "art" if artifacts else None, quotas=quotas or OutputQuotas())
    result = await LocalExecutionBackend("legacy_host").execute(request)
    assert result.status == ExecutionStatus.SUCCEEDED
    return result


class Process:
    def __init__(self, out, err):
        self.stdout, self.stderr, self.stdin = asyncio.StreamReader(), asyncio.StreamReader(), None
        self.stdout.feed_data(out)
        self.stderr.feed_data(err)
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        self.returncode = 0

    def kill(self):
        pass

    async def wait(self):
        return 0


async def run_docker(tmp_path, monkeypatch, out, err, *, artifacts=True):
    async def spawn(*args, **kwargs):
        return Process(out, err)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    backend = DockerExecutionBackend()
    backend.active = True
    result = await backend.execute(ExecutionRequest(argv=["true"], artifact_dir=tmp_path / "art" if artifacts else None))
    assert result.status == ExecutionStatus.SUCCEEDED
    return result


@pytest.fixture(params=["local", "docker"])
def run(request, tmp_path, monkeypatch):
    def go(out, err, **kwargs):
        if request.param == "local":
            return asyncio.run(run_local(tmp_path, out, err, **kwargs))
        return asyncio.run(run_docker(tmp_path, monkeypatch, out, err, **kwargs))
    return go


def test_single_stream_is_counted_once(run):
    result = run(OUT, b"")
    assert result.bytes_seen == len(OUT)
    assert result.bytes_kept == len(OUT)
    assert result.bytes_kept_memory == len(OUT)


def test_two_streams_are_each_counted_once(run):
    result = run(OUT, ERR)
    assert result.bytes_seen == len(OUT) + len(ERR)
    assert result.bytes_kept == len(OUT) + len(ERR)
    assert result.bytes_kept_memory == len(OUT) + len(ERR)


def test_without_artifacts_nothing_is_persisted(run):
    result = run(OUT, ERR, artifacts=False)
    assert result.bytes_kept == 0
    assert result.bytes_kept_memory == len(OUT) + len(ERR)


def test_memory_cap_does_not_reduce_persisted_bytes(tmp_path):
    quotas = OutputQuotas(memory_per_stream_bytes=50)
    result = asyncio.run(run_local(tmp_path, OUT, ERR, quotas=quotas))
    assert result.truncated
    assert result.bytes_kept == len(OUT) + len(ERR)
    assert result.bytes_kept_memory == 100


def trailer_value(text):
    return int(text.rsplit("bytes_kept=", 1)[1].split()[0])


def test_trailer_keeps_the_old_sum_unless_persisted_is_selected(run):
    result = run(OUT, ERR)
    total = len(OUT) + len(ERR)
    assert trailer_value(result.observation_text()) == 2 * total
    assert trailer_value(result.observation_text(trailer_bytes_kept="legacy")) == 2 * total
    assert trailer_value(result.observation_text(trailer_bytes_kept="persisted")) == total


@pytest.mark.parametrize("mode, shown", [(None, 2), ("legacy", 2), ("persisted", 1)])
def test_run_command_tool_reports_corrected_bytes_and_gated_trailer(tmp_path, mode, shown):
    kwargs = {} if mode is None else {"observation_bytes_kept": mode}
    tool = RunCommandTool(prefer_argv=True, execution_profile="legacy_host", **kwargs)
    ctx = ToolContext(workspace_root=tmp_path, call_id="c1", extra={"artifact_dir": str(tmp_path / "art")})
    result = asyncio.run(tool.execute({"argv": [sys.executable, "-c", script(OUT, ERR)]}, ctx))
    total = len(OUT) + len(ERR)
    assert result.bytes_kept == total
    assert trailer_value(result.output) == shown * total


def test_option_is_omitted_from_dump_unless_enabled():
    assert "observation_bytes_kept" not in CommandConfig().model_dump()
    assert "observation_bytes_kept" not in CommandConfig(observation_bytes_kept="legacy").model_dump()
    assert CommandConfig(observation_bytes_kept="persisted").model_dump()["observation_bytes_kept"] == "persisted"
    with pytest.raises(ValueError):
        CommandConfig(observation_bytes_kept="both")
