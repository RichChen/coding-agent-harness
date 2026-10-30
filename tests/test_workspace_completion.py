"""P3 through the original loop/session, with deterministic P2 boundary fixtures."""
import asyncio
import json
import runpy
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.harness.factory import build_agent, load_config
from src.llm.client import LLMResponse, ToolCall
from src.runtime.session import RunSession, compatibility
from src.runtime.workspace_io import inspect_workspace_session, export_workspace_session
from src.tracing.store import StoreError
from src.validation.contracts import PublicValidationSpec
from src.validation.replica import ReplicaResult
from src.workspace.contracts import blob_sha256, canonical_json
from src.workspace.control import CaptureError
from src.workspace.docker_transport import CommandResult

H = runpy.run_path(str(Path(__file__).parent / "fixtures/public_validation/support.py"))
GOOD = b"def add(a, b):\n    return a + b\n"
BAD = b"def add(a, b):\n    return a - b\n"


class Replies:
    model, base_url, max_tokens, temperature, thinking = "scripted-fixture", "offline", 64, 0, None
    def __init__(self, rows): self.rows, self.calls, self.messages = rows, 0, []
    async def chat(self, messages, tools):
        self.messages.append(messages)
        row = self.rows[self.calls]
        self.calls += 1
        if callable(row): row = row()
        return LLMResponse(text=row if isinstance(row, str) else None,
            tool_calls=[] if isinstance(row, str) else row,
            usage=dict(prompt_tokens=1, completion_tokens=1, total_tokens=2, provenance="provider"))


def write(body, number="write"):
    return [ToolCall(number, "write_file", dict(path="pkg/calc.py", content=body.decode()))]


class Transport:
    name, container_id, context, image_id, image_digest = "fixture-editor", "fixture-id", "fixture", "sha256:"+"1"*64, "sha256:"+"1"*64
    def __init__(self):
        self.files = dict(H["BASE_FILES"])
        self.closed = False
        self.cleanup_ok = True
        self.commands = 0
    async def start(self, control): control.remaining(None)
    async def execute(self, argv, control, *, stdin=None, env=None):
        control.remaining(None)
        self.commands += 1
        payload = json.loads(stdin)
        assert payload["preserve_mode"] is True
        path = payload["params"]["path"]
        if payload["name"] == "write_file":
            self.files[path] = payload["params"]["content"].encode()
            return CommandResult(0, b"file updated", b"")
        return CommandResult(0, self.files[path], b"")
    async def close(self, control):
        self.closed = True
        return self.cleanup_ok


class Provider:
    def __init__(self, coordinator, store, **kwargs):
        self.coordinator, self.store = coordinator, store
        self.base_entries = H["build_capture"]()[0].entries
        self.last_capture = None
        self.calls = 0
        self.fail = False
    async def capture(self, control):
        control.remaining(None)
        self.calls += 1
        if self.fail: raise CaptureError("capture_io_error")
        cap, reader = H["build_capture"](self.coordinator._transport.files)
        refs = {key: self.store.put(data) for key, data in reader.blobs.items()}
        cap = replace(cap, capture_id=str(uuid4()),
            changed=tuple((p, refs[r.key]) for p, r in cap.changed),
            corpus_blobs=tuple((p, refs[r.key]) for p, r in cap.corpus_blobs),
            barrier_receipt=refs[cap.barrier_receipt.key])
        self.last_capture = cap
        return cap


class Executor:
    def __init__(self): self.calls = []
    async def execute(self, request, started):
        self.calls.append(request)
        started("fixture-replica-"+request.operation_id)
        failed = any(e.path=="pkg/calc.py" and e.sha256==blob_sha256(BAD) for e in request.capture.entries)
        failed = failed or getattr(self, "fail_candidates", False) and len(self.calls) > 1
        packet = H["receipt"](request.environment, request.operation_id, failed=failed)
        return ReplicaResult("failed" if failed else "succeeded", 1 if failed else 0,
            canonical_json(packet), b"public fixture assertion failed" if failed else b"public fixture passed", b"",
            request.environment.environment_id, request.capture.manifest_id, request.capture.manifest_id,
            getattr(self, "candidate_cleanup", "cleaned") if len(self.calls)>1 else "cleaned")


class Profile:
    def __init__(self):
        self.transport = Transport()
        self.policy = SimpleNamespace(rules_id=H["build_capture"]()[0].rules_id)
    def new_workspace(self): return self.transport
    async def probe_environment(self, coordinator, control): return H["environment"](pytest_version="8.4.2"), {}
    def build_spec(self, capture, environment):
        return H["spec"](capture, adapter="pytest8-v1", coverage="regression_only",
                         known_fixture_failures=(), target_evidence=None)


def setup(tmp_path, rows, *, max_calls=10, context_limit=None):
    config = load_config(Path(__file__).parents[1] / "configs/workspace/sphinx-bm25.yaml")
    config["runtime"]["budget"]["max_model_calls"] = max_calls
    if context_limit is not None: config["runtime"]["context"]["max_tokens"] = context_limit
    workspace = tmp_path / "anchor"
    workspace.mkdir()
    model = Replies(rows)
    loop = build_agent(config, workspace, llm_client=model)
    controller = loop.workspace_controller
    profile, executor = Profile(), Executor()
    controller.profile, controller.provider_factory = profile, Provider
    controller.executor_factory = lambda store, p: executor
    expected = compatibility(workspace=workspace, code_id="p3-fixture", config=config,
                             backend=loop.execution_backend.capabilities)
    session = RunSession.create(tmp_path / "session", task_id="p3", expected=expected,
                                timeout_seconds=600, artifact_item_limit_bytes=8*1024**2)
    return loop, session, model, profile, executor


@pytest.mark.asyncio
async def test_fail_feedback_fix_pass_export_in_same_loop_and_budget(tmp_path):
    loop, session, model, profile, executor = setup(tmp_path, [write(BAD), "done", write(GOOD, "fix"), "done"])
    with session:
        result = await session.run(loop, "fixture")
        assert result.workspace_v1["development_validation"]["status"] == "passed"
        assert result.workspace_v1["delivery"]["status"] == "exported"
        assert result.workspace_v1["independent_evaluation"] == dict(status="not_evaluated", solved=None)
        assert len(executor.calls) == 3 and model.calls == result.model_budget["calls_started"] == 4
        assert len(session.state.workspace["feedback"]) == 1
        assert any(m["role"]=="user" and "[Harness public validation observation]" in m.get("content", "")
                   for m in model.messages[-1])
        events = session.store.read_events()
        assert events[-1]["event_type"] == "run.finished"
        assert sum(e["event_type"]=="run.finished" for e in events) == 1
        assert not any(e["event_type"]=="execution.snapshot" for e in events)
    assert profile.transport.closed
    output = tmp_path / "export"
    manifest = export_workspace_session(tmp_path / "session", output)
    assert manifest["final"] and manifest["record_id"]
    assert (output / "delta/pkg/calc.py").read_bytes() == GOOD
    # Verify the check's dependency chain from the export alone.
    from src.validation.contracts import BaselineBinding, ValidationRecord, ValidationEnvironment, ValidationLimits
    from src.workspace.contracts import FileEntry, WorkspaceCapture
    spec_data = json.loads((output / "spec.json").read_bytes())
    spec_data["limits"] = ValidationLimits(**spec_data["limits"])
    spec_data["protected_manifest"] = tuple(FileEntry.from_dict(v) for v in spec_data["protected_manifest"])
    spec = PublicValidationSpec(**spec_data)
    env = ValidationEnvironment(**json.loads((output / "environment.json").read_bytes()))
    binding = BaselineBinding(**json.loads((output / "binding.json").read_bytes()))
    baseline = ValidationRecord.from_dict(json.loads((output / "baseline-validation.json").read_bytes()))
    final = ValidationRecord.from_dict(json.loads((output / "validation.json").read_bytes()))
    assert final.check_id == binding.check_id and final.environment_id == env.environment_id
    assert binding.spec_id == spec.spec_id and binding.record_id == baseline.record_id
    assert any(WorkspaceCapture.from_json(p.read_bytes()).candidate_id == binding.base_candidate_id
               for p in (output / "captures").iterdir())
    for record in (baseline, final):
        for ref in record.evidence:
            raw = (output / "blobs" / ref.sha256).read_bytes()
            assert len(raw) == ref.size and blob_sha256(raw) == ref.sha256
    for ref in [*manifest["records"].values(), *(p["capture_ref"] for p in manifest["captures"].values())]:
        raw = (output / "blobs" / ref["sha256"]).read_bytes()
        assert blob_sha256(raw) == ref["sha256"] and len(raw) == ref["size_bytes"]


@pytest.mark.asyncio
async def test_pass_tool_reuses_at_end_and_seal_blocks_all_new_commands(tmp_path):
    rows = [write(GOOD), [ToolCall("validate", "validate_patch", {})], "done"]
    loop, session, model, profile, executor = setup(tmp_path, rows)
    with session:
        result = await session.run(loop, "fixture")
        assert result.workspace_v1["development_validation"]["status"] == "passed"
        assert len(executor.calls) == 2 and not session.state.workspace["feedback"]
        assert any(e["event_type"]=="validation.reused" for e in session.store.read_events())
        commands = profile.transport.commands
        from src.execution.base import ExecutionRequest
        blocked = await loop.execution_backend.execute(ExecutionRequest(argv=["write-after-seal"]))
        assert blocked.error_kind == "workspace_sealed" and profile.transport.commands == commands


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [False, True])
async def test_same_failed_candidate_never_gets_duplicate_automatic_feedback(tmp_path, active):
    rows = [write(BAD)] + ([[ToolCall("validate", "validate_patch", {})]] if active else ["done"]) + ["done"]
    loop, session, model, profile, executor = setup(tmp_path, rows)
    with session:
        result = await session.run(loop, "fixture")
        assert result.workspace_v1["development_validation"]["status"] == "behavior_failed"
        assert len(session.state.workspace["feedback"]) == (0 if active else 1)
        assert len(executor.calls) == 2 and model.calls == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["budget", "context"])
async def test_model_or_context_hard_stop_uses_host_reserve_without_more_model_calls(tmp_path, stop):
    loop, session, model, profile, executor = setup(tmp_path, [write(GOOD)],
        max_calls=1, context_limit=64 if stop=="context" else None)
    with session:
        result = await session.run(loop, "fixture")
        assert len(executor.calls) == 2
        assert model.calls == (0 if stop=="context" else 1)
        assert result.workspace_v1["run"]["status"] == ("context_exhausted" if stop=="context" else "budget_exhausted")
        assert result.workspace_v1["development_validation"]["status"] == "passed"
        assert not session.state.workspace["feedback"]


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["cancel", "deadline", "async_cancel"])
async def test_stop_does_not_start_capture_validation_or_model_and_marks_unknown(tmp_path, stop):
    loop, session, model, profile, executor = setup(tmp_path, [])
    loop.cancel_event = asyncio.Event()
    def stop_now():
        if stop=="cancel": loop.cancel_event.set()
        elif stop=="async_cancel": raise asyncio.CancelledError()
        else: loop.workspace_controller.run_deadline = time.monotonic()-1
        return "done"
    model.rows = [stop_now]
    with session:
        result = await session.run(loop, "fixture")
        assert loop.workspace_controller.provider.calls == 1 and len(executor.calls) == 1 and model.calls == 1
        assert result.workspace_v1["delivery"]["final_candidate_unknown"]
        assert result.workspace_v1["development_validation"]["status"] == "not_run"
    assert profile.transport.closed


@pytest.mark.asyncio
async def test_cleanup_failure_cannot_report_complete_delivery(tmp_path):
    loop, session, _, profile, _ = setup(tmp_path, ["done"])
    profile.transport.cleanup_ok = False
    with session:
        result = await session.run(loop, "fixture")
        assert not result.success and result.workspace_v1["delivery"]["status"] == "incomplete"
        assert result.workspace_v1["run"]["reason"] == "cleanup_failed"


@pytest.mark.asyncio
async def test_seal_commit_failure_still_cleans_and_unsealed_record_can_export(tmp_path, monkeypatch):
    loop, session, _, profile, _ = setup(tmp_path, [write(GOOD), "done"])
    original = session.append
    def fail_seal(kind, payload, **kwargs):
        if kind=="completion.sealed": raise StoreError("injected seal failure")
        return original(kind, payload, **kwargs)
    monkeypatch.setattr(session, "append", fail_seal)
    with session:
        with pytest.raises(StoreError, match="seal failure"):
            await session.run(loop, "fixture")
        assert profile.transport.closed and session.state.terminal is None
        assert session.state.workspace["records"] and not session.state.workspace.get("sealed")
    output = tmp_path / "historical-export"
    value = export_workspace_session(tmp_path / "session", output)
    assert not value["final"] and value["record_id"] and (output / "validation.json").is_file()
    assert value["report"]["run"]["status"] == "interrupted"
    assert inspect_workspace_session(tmp_path / "session")["terminal"] is None


@pytest.mark.asyncio
async def test_explicit_checks_cannot_spend_last_candidate_slot(tmp_path):
    rows = [write(BAD), [ToolCall("v1", "validate_patch", {})], write(GOOD, "w2"),
            [ToolCall("v2", "validate_patch", {})], write(GOOD+b"# final\n", "w3"),
            [ToolCall("v3", "validate_patch", {})], "done"]
    loop, session, model, _, executor = setup(tmp_path, rows)
    with session:
        result = await session.run(loop, "fixture")
        assert len(executor.calls) == 4 and model.calls == 7  # Baseline + three candidates.
        assert result.workspace_v1["development_validation"]["status"] == "passed"
        assert executor.calls[-1].capture.candidate_id == result.workspace_v1["delivery"]["candidate_id"]
        assert any(e["event_type"]=="validation.not_run" and
                   e["payload"]["reason"]=="final_slot_reserved" for e in session.store.read_events())
        assert not session.state.workspace["feedback"]


@pytest.mark.asyncio
async def test_two_feedback_rounds_stop_at_final_slot(tmp_path):
    rows = [write(BAD), "done", write(BAD+b"# second\n", "w2"), "done",
            write(BAD+b"# third\n", "w3"), "done"]
    loop, session, model, _, executor = setup(tmp_path, rows)
    executor.fail_candidates = True
    with session:
        result = await session.run(loop, "fixture")
        assert len(executor.calls) == 4 and model.calls == 6
        assert len(session.state.workspace["feedback"]) == 2
        assert loop.workspace_controller.ledger.remaining_candidates == 0
        assert result.workspace_v1["development_validation"]["status"] == "behavior_failed"


@pytest.mark.asyncio
async def test_capture_failure_never_searches_previous_index(tmp_path, monkeypatch):
    loop, session, model, _, executor = setup(tmp_path, [])
    def fail_after_first_retrieval():
        controller = loop.workspace_controller
        controller.provider.fail = True
        def stale_search(*args): pytest.fail("stale index was searched after failed capture")
        monkeypatch.setattr(controller.index, "search", stale_search)
        return [ToolCall("retrieve2", "retrieve_code", dict(query="add", k=1))]
    model.rows = [[ToolCall("retrieve1", "retrieve_code", dict(query="add", k=1))], fail_after_first_retrieval, "done"]
    with session:
        result = await session.run(loop, "fixture")
        assert len(executor.calls) == 1
        assert result.workspace_v1["delivery"]["final_candidate_unknown"]
        assert result.workspace_v1["development_validation"]["status"] == "not_run"


@pytest.mark.asyncio
async def test_early_artifact_capacity_failure_is_reported_without_side_effects(tmp_path):
    loop, session, model, profile, _ = setup(tmp_path, ["done"])
    loop.workspace_controller.config["final_artifact_reserve_bytes"] = 64*1024**2
    with session:
        result = await session.run(loop, "fixture")
        assert result.workspace_v1["run"]["reason"] == "artifact_budget_exhausted"
        assert model.calls == 0 and loop.workspace_controller.coordinator is None
        assert session.state.terminal is not None
        assert not profile.transport.closed  # The transport was never created/started.


@pytest.mark.asyncio
async def test_record_commit_failure_leaves_pending_evidence_without_reexecution(tmp_path, monkeypatch):
    loop, session, model, profile, executor = setup(tmp_path, [write(GOOD), "done"])
    original = session.append
    def fail_commit(kind, payload, **kwargs):
        if kind=="validation.committed" and payload["record"]["trigger"]!="baseline":
            raise StoreError("injected record commit failure")
        return original(kind, payload, **kwargs)
    monkeypatch.setattr(session, "append", fail_commit)
    expected = dict(session.state.manifest)
    with session:
        with pytest.raises(StoreError, match="record commit failure"):
            await session.run(loop, "fixture")
        assert profile.transport.closed and len(executor.calls) == 2
        assert session.state.workspace["pending_validation"] and session.state.terminal is None
    # Inspection/export do not validate expiry or reconcile a pending operation.
    view = inspect_workspace_session(tmp_path / "session")
    assert view["workspace"]["pending_validation"]
    value = export_workspace_session(tmp_path / "session", tmp_path / "historical")
    assert not value["final"] and value["pending"] and value["record_id"] is None
    with pytest.raises(StoreError, match="continuation is unsupported"):
        RunSession.resume(tmp_path / "session", expected=expected)
    assert len(executor.calls) == 2 and model.calls == 2


@pytest.mark.asyncio
async def test_replica_cleanup_failure_marks_delivery_incomplete(tmp_path):
    loop, session, _, profile, executor = setup(tmp_path, [write(GOOD), "done"])
    executor.candidate_cleanup = "cleanup_failed"
    with session:
        result = await session.run(loop, "fixture")
        assert result.workspace_v1["development_validation"]["status"] == "inconclusive"
        assert result.workspace_v1["delivery"]["status"] == "incomplete" and not result.success
        assert not result.workspace_v1["run"]["cleanup_ok"] and profile.transport.closed


@pytest.mark.asyncio
async def test_nested_evidence_corruption_rejected_before_export_writes(tmp_path):
    loop, session, _, _, _ = setup(tmp_path, [write(GOOD), "done"])
    with session:
        await session.run(loop, "fixture")
        records = list(session.state.workspace["records"].values())
        ref = next(r for p in records for r in p["record"]["evidence"] if r["size"])
    path = tmp_path / "session/artifacts" / ref["key"]
    path.write_bytes(b"x" * ref["size"])
    with pytest.raises(StoreError, match="hash mismatch"):
        inspect_workspace_session(tmp_path / "session")
    destination = tmp_path / "bad-export"
    with pytest.raises(StoreError, match="hash mismatch"):
        export_workspace_session(tmp_path / "session", destination)
    assert not destination.exists()


def test_native_publish_journal_spill_and_command_staging_share_final_reserve(tmp_path):
    from src.runtime.workspace_io import SessionWorkspaceArtifacts
    _, session, _, _, _ = setup(tmp_path, [])
    with session:
        bridge = SessionWorkspaceArtifacts(session, final_reserve=8192)
        session.artifacts.limit_bytes = session.artifacts.used_bytes()+8192+4096
        with pytest.raises(StoreError, match="disk quota"):
            session.artifacts.publish(b"a"*5000)
        with pytest.raises(StoreError, match="disk quota"):
            session.append("plugin.state", dict(name="spill", value="x"*18000))
        staging = tmp_path / "session/command-output/fixture"
        staging.mkdir(parents=True)
        (staging / "stdout.bin").write_bytes(b"b"*4096)
        with pytest.raises(StoreError, match="reserve would be exhausted"):
            bridge.require_capacity(1)
        bridge.finalizing = True
        assert bridge.read(bridge.put(b"final evidence")) == b"final evidence"


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["output", "artifact"])
async def test_ordinary_budget_exhaustion_uses_original_final_reserve(tmp_path, resource):
    loop, session, model, profile, executor = setup(tmp_path, [])
    def exhaust_ordinary():
        controller = loop.workspace_controller
        if resource == "output":
            loop.run_output_meter.bytes_kept = loop.run_output_meter.limit_bytes-1
        else:
            remaining = session.artifacts.limit_bytes-session.artifacts.used_bytes()-controller.storage.final_reserve
            staging = tmp_path / "session/command-output/exhaustion"
            staging.mkdir(parents=True, exist_ok=True)
            (staging / "stdout.bin").write_bytes(b"x"*(remaining-128))
        return [ToolCall("retrieve", "retrieve_code", dict(query="add", k=1))]
    model.rows = [exhaust_ordinary]
    with session:
        result = await session.run(loop, "fixture")
        assert model.calls == 1 and len(executor.calls) == 2 and profile.transport.closed
        assert result.workspace_v1["development_validation"]["status"] == "passed"
        assert result.workspace_v1["run"]["reason"] == ("run_output_limit" if resource=="output" else "artifact_budget_exhausted")
        assert not result.success and session.state.workspace["sealed"]
        assert session.store.read_events()[-1]["event_type"] == "run.finished"


@pytest.mark.asyncio
async def test_failed_baseline_finishes_without_candidate_validation_or_model(tmp_path):
    loop, session, model, profile, executor = setup(tmp_path, ["done"])
    original = executor.execute
    async def baseline_timeout(request, started):
        packet = await original(request, started)
        return replace(packet, execution_status="timed_out", exit_code=None, report=b"")
    executor.execute = baseline_timeout
    with session:
        result = await session.run(loop, "fixture")
        assert len(executor.calls) == 1 and model.calls == 0 and profile.transport.closed
        assert result.workspace_v1["run"]["reason"].startswith("baseline_unavailable:")
        assert result.workspace_v1["development_validation"]["status"] == "not_run"
        assert session.store.read_events()[-1]["event_type"] == "run.finished"


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["step", "tokens", "soft_deadline"])
async def test_other_hard_stops_share_host_finalizer(tmp_path, stop):
    loop, session, model, profile, executor = setup(tmp_path, [write(GOOD)])
    if stop == "step": loop.budget.max_steps = 1
    elif stop == "tokens": loop.model_ledger.max_tokens = 1
    else:
        def expire_agent_allocation():
            loop.deadline.run_deadline_mono = time.monotonic()-1
            return write(GOOD)
        model.rows = [expire_agent_allocation]
    with session:
        result = await session.run(loop, "fixture")
        assert model.calls == (0 if stop=="tokens" else 1) and len(executor.calls) == 2
        assert result.workspace_v1["development_validation"]["status"] == "passed"
        assert result.workspace_v1["run"]["status"] == ("deadline" if stop=="soft_deadline" else "budget_exhausted")
        assert profile.transport.closed and not session.state.workspace["feedback"]


@pytest.mark.asyncio
async def test_scripted_sphinx_demo_fault_and_correction_cover_multiline_return():
    import ast
    from scripts.run_workspace import ScriptedSphinxDemo
    demo = ScriptedSphinxDemo()
    await demo.chat([], [])
    await demo.chat([], [])
    original = "    return posixpath.normpath(\n        posixpath.join('/' + basedocname, '..', docname))[1:]"
    source = "def docname_join(basedocname, docname):\n"+original+"\n\n"
    message = dict(role="tool", tool_call_id="demo-read", content="\n".join(
        f"{i}|{line}" for i,line in enumerate(source.splitlines(),63)))
    fault = (await demo.chat([message], [])).tool_calls[0].arguments
    assert fault["search"] == original
    broken = source.replace(fault["search"], fault["replace"])
    ast.parse(broken)
    await demo.chat([], [])
    feedback = dict(role="user", content="[Harness public validation observation]")
    correction = (await demo.chat([feedback], [])).tool_calls[0].arguments
    restored = broken.replace(correction["search"], correction["replace"])
    ast.parse(restored)
    assert original in restored and "__p3_scripted_mechanism_fault__" not in restored
    assert demo.feedback_observed and demo.calls == 5


@pytest.mark.asyncio
async def test_scripted_fault_changes_behavior_covered_by_frozen_public_check():
    from scripts.run_workspace import ScriptedSphinxDemo
    demo = ScriptedSphinxDemo()
    first = await demo.chat([], [])
    assert first.tool_calls[0].arguments["query"] == "display_chunk"
    await demo.chat([], [])
    source = "def display_chunk(chunk):\n    if isinstance(chunk, (list, tuple)):\n        if len(chunk) == 1:\n            return str(chunk[0])\n        return '%s .. %s' % (chunk[0], chunk[-1])\n    return str(chunk)\n\n"
    message = dict(role="tool", tool_call_id="demo-read", content="\n".join(
        f"{i}|{line}" for i,line in enumerate(source.splitlines(),640)))
    call = (await demo.chat([message], [])).tool_calls[0].arguments
    namespace = {}
    exec(source.replace(call["search"], call["replace"]), namespace)
    assert namespace["display_chunk"]("hello") != "hello"  # tests/test_util.py's public assertion.


@pytest.mark.asyncio
async def test_event_bus_terminal_and_close_follow_durable_finish_and_cleanup(tmp_path):
    from src.tracing.events import EventBus
    loop, session, _, profile, _ = setup(tmp_path, ["done"])
    class CheckedBus(EventBus):
        def emit(self, event, **payload):
            if event == "run_end":
                assert session.state.terminal is not None and profile.transport.closed
            super().emit(event, **payload)
        def close(self):
            assert session.state.terminal is not None and profile.transport.closed
            super().close()
    bus = CheckedBus(tmp_path / "trace")
    loop.event_bus = bus
    with session:
        await session.run(loop, "fixture")
        assert bus._file is None
    events = [json.loads(line) for line in bus.path.read_text(encoding="utf-8").splitlines()]
    assert events[-1]["event"] == "run_end" and sum(v["event"]=="run_end" for v in events)==1


@pytest.mark.asyncio
async def test_feedback_cannot_borrow_final_output_reserve(tmp_path, monkeypatch):
    loop, session, model, _, executor = setup(tmp_path, [write(BAD), "done"])
    controller = loop.workspace_controller
    original = controller.can_feedback
    def almost_full(message, messages):
        loop.run_output_meter.bytes_kept = controller.output_total-controller.output_reserve-1
        return original(message, messages)
    monkeypatch.setattr(controller, "can_feedback", almost_full)
    with session:
        result = await session.run(loop, "fixture")
        assert model.calls == 2 and len(executor.calls) == 2
        assert not session.state.workspace["feedback"]
        assert result.workspace_v1["development_validation"]["status"] == "behavior_failed"


@pytest.mark.asyncio
async def test_scripted_acceptance_rejects_green_run_without_failure_and_feedback(tmp_path):
    from scripts.run_workspace import scripted_demo_acceptance
    loop, session, _, _, _ = setup(tmp_path, ["done"])
    with session:
        result = await session.run(loop, "fixture")
        assert result.workspace_v1["development_validation"]["status"] == "passed"
    exported = export_workspace_session(tmp_path / "session", tmp_path / "export")
    audit = scripted_demo_acceptance(tmp_path / "session", exported)
    assert not audit["accepted"] and not audit["checks"]["failed_then_passed"]
    assert not audit["checks"]["single_host_feedback"]


@pytest.mark.asyncio
async def test_expired_terminal_session_still_allows_readonly_inspection_and_export(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    import src.runtime.recovery as recovery
    loop, session, model, _, executor = setup(tmp_path, ["done"])
    with session:
        await session.run(loop, "fixture")
        expected = dict(session.state.manifest)
    class FutureClock(datetime):
        @classmethod
        def now(cls, tz=None): return cls(2100, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(recovery, "datetime", FutureClock)
    with pytest.raises(StoreError, match="absolute expiry reached"):
        RunSession.resume(tmp_path / "session", expected=expected)
    assert inspect_workspace_session(tmp_path / "session")["terminal"] is not None
    assert export_workspace_session(tmp_path / "session", tmp_path / "expired-export")["final"]
    assert model.calls == 1 and len(executor.calls) == 2
