"""Opt-in single-process sessions; host state lives OUTSIDE the agent workspace.

Raw observations are private evidence, not a public trace. Local process groups
do not isolate host files from arbitrary shell code; isolation remains H3 work.
"""
from __future__ import annotations

import dataclasses
import enum
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.runtime.recovery import (file_hash, reconcile_file, reduce_events,
                                  validate_resume, workspace_identity)
from src.tools.base import ToolResult
from src.tracing.artifacts import ArtifactStore, ArtifactQuotaError
from src.tracing.store import EventStore, StoreError, canonical, digest, utc_now


def plain(value):
    if dataclasses.is_dataclass(value):
        return plain(dataclasses.asdict(value))
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def compatibility(*, workspace, code_id, config, backend):
    # Persist only a hash, never the configuration (which may contain credentials).
    return dict(workspace=workspace_identity(workspace), code_id=code_id,
                config_hash=digest(canonical(config).encode()), backend=plain(backend), schema_version="0.1")


def source_fingerprint(repository: Path):
    """Includes uncommitted Python/config-schema changes; a HEAD SHA alone is insufficient."""
    repository = Path(repository).resolve()
    paths = sorted((repository / "src").rglob("*.py")) + [repository / "pyproject.toml"]
    return digest(canonical([(p.relative_to(repository).as_posix(), digest(p.read_bytes())) for p in paths]).encode())


def runtime_fingerprint(loop):
    """Defense against accidentally constructing a different loop after resume."""
    tools = []
    for tool in loop.tools.tools():
        if loop.enabled_tools is not None and tool.name not in loop.enabled_tools:
            continue
        info = dict(schema=tool.to_schema(), implementation=type(tool).__qualname__, side_effect=tool.side_effect.value)
        for key in ("timeout_seconds", "env_whitelist", "prefer_argv", "output_quotas"):
            if hasattr(tool, key):
                info[key] = plain(getattr(tool, key))
        if hasattr(tool, "backend"):
            info["backend"] = tool.backend.capabilities
        policy = getattr(tool, "command_policy", None)
        if policy:
            info["policy"] = {k: getattr(policy, k) for k in ("allow_all", "allowed_prefixes", "blocked_patterns")}
        tools.append(info)
    hook = None
    if loop.hooks:
        hook = dict(type=type(loop.hooks).__qualname__, keep_recent=getattr(loop.hooks, "keep_recent", None),
                    token_threshold=getattr(loop.hooks, "token_threshold", None),
                    preserve_tools=sorted(getattr(loop.hooks, "preserve_tools", None) or []))
        from src.runtime.window import WindowPolicyHooks
        if type(loop.hooks) is WindowPolicyHooks:
            hook['window'] = loop.hooks.fingerprint()
            if getattr(loop.hooks, 'dispatcher', None) is not None:
                hook['dispatcher'] = loop.hooks.dispatcher.fingerprint()
    retry = plain(loop._retry_config)
    # Keep recorded legacy fingerprints stable; enabled audit remains resume-critical.
    if not retry.get('stop_on_unknown_usage'):
        retry.pop('stop_on_unknown_usage', None)
    spec = dict(tools=tools, prompt=loop.system_prompt, hooks=hook,
        budget={k: getattr(loop.budget, k) for k in ("max_steps", "total_timeout_seconds", "step_timeout_seconds")},
        model={k: getattr(loop.llm, k, None) for k in ("model", "base_url", "temperature", "max_tokens", "thinking")},
        retry=retry, streaming=loop.use_llm_stream,
        price_table=loop.model_ledger.price_table,
        limits={k: getattr(loop, k) for k in ("file_size_limit_bytes", "tool_output_max_chars", "read_file_max_lines", "max_tool_argument_bytes")},
        output_limit=loop.run_output_meter.limit_bytes)
    if getattr(loop, "workspace_controller", None) is not None:
        spec["workspace_v1"] = loop.workspace_controller.fingerprint()
    return digest(canonical(spec).encode())


def decode_response(data):
    from src.llm.client import LLMResponse, ToolCall, StreamCapability
    from src.tools.validation import ToolValidationFailure, ToolValidationErrorKind
    data = dict(data)
    calls = []
    for raw in data.pop("tool_calls"):
        raw = dict(raw)
        if raw.get("parse_error"):
            failure = dict(raw["parse_error"])
            failure["kind"] = ToolValidationErrorKind(failure["kind"])
            raw["parse_error"] = ToolValidationFailure(**failure)
        calls.append(ToolCall(**raw))
    if data.get("capability"):
        data["capability"] = StreamCapability(data["capability"])
    return LLMResponse(tool_calls=calls, **data)


def decode_result(data):
    from src.runtime.loop import AgentRunResult, FailureReason
    from src.tracing.models import StepRecord
    data = dict(data)
    data["failure_reason"] = FailureReason(data["failure_reason"])
    data["steps"] = [StepRecord(**s) for s in data["steps"]]
    return AgentRunResult(**data)


class RunSession:
    def __init__(self, store, artifacts):
        self.store, self.artifacts = store, artifacts
        self._running = False
        self.refresh()

    @classmethod
    def create(cls, directory: Path, *, task_id, expected, timeout_seconds=600,
               artifact_item_limit_bytes=2 * 1024 * 1024):
        directory = Path(directory).resolve()
        workspace = Path(expected["workspace"]["path"])
        if directory.is_relative_to(workspace) or workspace.is_relative_to(directory):
            raise StoreError("session storage must be separate from agent workspace")
        if timeout_seconds <= 0:
            raise StoreError("session timeout must be positive")
        store = EventStore(directory, create=True, run_id=uuid.uuid4().hex, task_id=task_id)
        try:
            artifacts = ArtifactStore(directory / "artifacts", store.run_id, limit_bytes=64 * 1024 * 1024,
                                      item_limit_bytes=artifact_item_limit_bytes)
            manifest = dict(expected, started_at=utc_now(),
                            expires_at=(datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)).isoformat(),
                            quotas=dict(event_payload_bytes=store.max_event_bytes,
                                        artifact_bytes=artifacts.limit_bytes,
                                        artifact_item_bytes=artifacts.item_limit_bytes))
            store.append("run.started", manifest)
            return cls(store, artifacts)
        except BaseException:
            store.close()
            raise

    @classmethod
    def resume(cls, directory: Path, *, expected):
        store = EventStore(directory)
        try:
            session = cls(store, ArtifactStore(store.directory / "artifacts", store.run_id))
            if session.state.workspace and session.state.terminal is None:
                raise StoreError("workspace-v1 Docker continuation is unsupported; use read-only inspect/export")
            validate_resume(session.state.manifest, expected, session.artifacts)
            session.reconcile()
            return session
        except BaseException:
            store.close()
            raise

    @classmethod
    def replay(cls, directory: Path):
        with EventStore(directory, readonly=True) as store:
            return cls(store, ArtifactStore(store.directory / "artifacts", store.run_id, readonly=True)).state

    def refresh(self):
        events = self.store.read_events()
        backend = events[0]["payload"].get("backend") if events else None
        if isinstance(backend, dict) and backend.get("transport") == "workspace-v1":
            quotas = events[0]["payload"]["quotas"]
            self.artifacts.item_limit_bytes = quotas["artifact_item_bytes"]
            self.artifacts.limit_bytes = quotas["artifact_bytes"]
        for event in events:
            payload = event["payload"]
            if set(payload) == {"artifact_ref"}:
                event["payload"] = json.loads(self.artifacts.read(payload["artifact_ref"]))
            for ref in event["payload"].get("_artifacts", []):
                self.artifacts.read(ref)
        self.state = reduce_events(events)
        return self.state

    def append(self, kind, payload, **metadata):
        if self.state.terminal is not None:
            raise StoreError("terminated run is immutable")
        if "step_id" not in metadata and self.state.loop:
            metadata["step_id"] = f"step-{self.state.loop['step_number']}"
        payload = plain(payload)
        encoded = canonical(payload).encode()
        if len(encoded) > 16 * 1024:
            payload = {"artifact_ref": self.artifacts.publish(encoded, "application/json")}
        event = self.store.append(kind, payload, **metadata)
        self.refresh()
        return event

    def save_loop(self, snapshot):
        self.append("state.saved", snapshot)

    def checkpoint(self):
        self.store.checkpoint(plain(self.state))

    def set_plugin_state(self, name, state):
        self.append("plugin.state", dict(name=name, state=state))

    def record_dispatch(self, receipt, manager_state):
        """Persist a host executed read/search proposal for recovery."""
        self.append("plugin.dispatch", dict(receipt=plain(receipt),
                                             manager_state=plain(manager_state)),
                    operation_id=receipt.get("operation_id"),
                    attempt_id=receipt.get("attempt_id"))

    def model_started(self, ledger, operation_id, attempt_id):
        self.append("model.started", {"budget": ledger.snapshot()}, operation_id=operation_id, attempt_id=attempt_id)

    def model_settled(self, ledger, operation_id, attempt_id, record):
        record = plain(record)
        # Error classification is enough for recovery. Arbitrary provider error text
        # can contain authentication material and is not a session credential store.
        if record.get("message"):
            record["message"] = record.get("error_kind") or "model request failed"
        self.append("model.settled", {"budget": ledger.snapshot(), "record": record}, operation_id=operation_id, attempt_id=attempt_id)

    def intent(self, operation_id, *, name, arguments, side_effect, call_id, replacement=None):
        if self.state.terminal is not None:
            raise StoreError("terminated run cannot start operations")
        if operation_id in self.state.operations:
            raise StoreError("operation already exists; use recorded result")
        attempt_id = uuid.uuid4().hex
        self.append("tool.intent", dict(name=name, arguments=arguments,
                    side_effect=side_effect, replacement=replacement),
                    operation_id=operation_id, attempt_id=attempt_id, tool_call_id=call_id)
        return attempt_id

    def record_result(self, operation_id, result, run_output=None):
        op = self.state.operations[operation_id]
        payload = plain(result)
        if run_output is not None:
            payload["_run_output"] = run_output
        output_dir = self.store.directory / "command-output" / operation_id
        output_paths = []
        if output_dir.is_dir():
            for name in ("stdout.bin", "stderr.bin"):
                path = output_dir / name
                if path.is_file():
                    if path.is_symlink() or path.stat().st_size > self.artifacts.item_limit_bytes:
                        raise StoreError("command artifact path/size invalid")
                    ref = self.artifacts.publish(path.read_bytes())
                    payload.setdefault("_artifacts", []).append(ref)
                    output_paths.append(path)
        self.append("tool.result", payload, operation_id=operation_id,
                    attempt_id=op["attempt_id"], tool_call_id=op["tool_call_id"])
        # Only remove our redundant staging files AFTER the event/reference commits.
        for path in output_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def reconcile(self):
        for operation_id, op in list(self.state.operations.items()):
            if op["result"] is not None:
                continue
            confirmed = reconcile_file(op["intent"], Path(self.state.manifest["workspace"]["path"]))
            result = dict(call_id=op["tool_call_id"], name=op["intent"]["name"],
                          success=confirmed, output="atomic replacement confirmed by content hash" if confirmed else "",
                          status="succeeded" if confirmed else "outcome_unknown",
                          error_kind=None if confirmed else "outcome_unknown",
                          error=None if confirmed else "interrupted operation; never automatically replayed")
            self.record_result(operation_id, result)

    def previous_result(self, operation_id):
        op = self.state.operations.get(operation_id)
        if op is None:
            return None
        if op["result"] is None:
            raise StoreError("unreconciled intent; refusing execution")
        from src.tools.base import ToolStatus
        result = dict(op["result"])
        result["status"] = ToolStatus(result["status"])
        return ToolResult(**result)

    def prepare_tool(self, validated, ctx):
        """Record durable intent and optional before/after proof for write_file only."""
        replacement = None
        from src.tools.command import RunCommandTool
        from src.tools.container_files import ContainerFileTool
        if isinstance(validated.tool, (RunCommandTool, ContainerFileTool)):
            # Reserve space for both bounded collection and atomic publication.
            required = 2 * (validated.tool.output_quotas.artifact_per_tool_bytes if isinstance(validated.tool, RunCommandTool) else 1024*1024)
            if self.artifacts.used_bytes() + required > self.artifacts.limit_bytes:
                raise ArtifactQuotaError("artifact quota cannot reserve command output; refusing producer")
        # Exact builtin only; a plugin named write_file must not inherit its proof.
        from src.tools.files import WriteFileTool
        if type(validated.tool) is WriteFileTool:
            path = ctx.sandbox.resolve(validated.arguments["path"])
            content = validated.arguments["content"].encode("utf-8")
            if path is not None and (ctx.file_size_limit_bytes is None or len(content) <= ctx.file_size_limit_bytes):
                replacement = dict(path=str(path.relative_to(ctx.workspace_root)),
                                   before=file_hash(path), after=digest(content))
                ctx.extra["replacement_proof"] = replacement
        return self.intent(ctx.extra["operation_id"], name=validated.name,
                           arguments=validated.arguments, side_effect=validated.tool.side_effect.value,
                           call_id=validated.call_id, replacement=replacement)

    def finish(self, result):
        if self.state.terminal is None:
            self.append("run.finished", plain(result))

    """
    输入：build_agent 装好的 AgentLoop + 任务文本。
    输出：先做兼容性检查、恢复预算和截止时间，再把自己挂到 loop 上，返回 loop.run 的 AgentRunResult；过程中的事件写入 session/events.sqlite3。
    失败入口：模式/工作区/任务/运行时指纹/预算不一致或已过期时抛 StoreError；运行中的事件用 replay 只读查看，或直接查 events.sqlite3。
    """
    async def run(self, loop, task_description: str):
        if self._running:
            raise StoreError("session already running in this process")
        self._running = True
        try:
            return await self._run_impl(loop, task_description)
        finally:
            self._running = False

    async def _run_impl(self, loop, task_description: str):
        if self.state.terminal is not None:
            return decode_result(self.state.terminal)
        if getattr(loop, "workspace_controller", None) is not None and self.state.workspace:
            raise StoreError("workspace-v1 interrupted Docker session cannot continue; inspect/export only")
        from src.runtime.compact import MicroCompactHooks
        from src.runtime.hooks import LoopHooks
        from src.runtime.window import WindowPolicyHooks
        from src.policy.plugin import NoOpPolicyPlugin, FixturePolicyPlugin
        from src.policy.registry import RegisteredPolicyPlugin
        if type(loop.hooks) is WindowPolicyHooks:
            if type(loop.hooks.plugin) is RegisteredPolicyPlugin:
                loop.hooks.plugin.verify()
            elif type(loop.hooks.plugin) not in (NoOpPolicyPlugin, FixturePolicyPlugin):
                raise StoreError('persistent H4 requires built-in or host-registered plugin')
        if (loop.runtime_mode != "react" or (loop.hooks is not None and type(loop.hooks) not in (MicroCompactHooks, LoopHooks, WindowPolicyHooks))
                or "subagent_task" in [s["function"]["name"] for s in loop.tools.schemas(loop.enabled_tools)]):
            raise StoreError("persistent loop supports react with built-in context hooks, no subagents; refusing incompatible profile")
        if workspace_identity(loop.sandbox.workspace_root) != self.state.manifest["workspace"]:
            raise StoreError("loop workspace incompatible with session")
        task_hash = digest(task_description.encode())
        if self.state.loop and self.state.loop.get("task_hash") != task_hash:
            raise StoreError("task description changed")
        from src.policy.model_budget import ModelBudgetLedger
        if loop.model_ledger is None:
            loop.model_ledger = ModelBudgetLedger()
            loop.retry.model_ledger = loop.model_ledger
        runtime_hash = runtime_fingerprint(loop)
        if self.state.runtime_hash is not None and self.state.runtime_hash != runtime_hash:
            raise StoreError("runtime configuration changed since session start")
        if self.state.runtime_hash is None:
            self.append("runtime.bound", {"hash": runtime_hash})
        if type(loop.hooks) is WindowPolicyHooks and 'h4' in self.state.plugin_state:
            loop.hooks.restore(self.state.plugin_state['h4'])
        elif type(loop.hooks) is WindowPolicyHooks:
            loop.hooks.reset()
        ledger = self.state.budget
        if ledger:
            for key in ("max_model_calls", "max_tokens", "max_cost_usd"):
                if getattr(loop.model_ledger, key) != ledger[key]:
                    raise StoreError(f"budget incompatible: {key}")
            for key in ("calls_started", "tokens_committed", "cost_committed"):
                setattr(loop.model_ledger, key, ledger[key])
            loop.model_ledger._next_id = ledger["calls_started"]
            from src.policy.model_budget import UsageSettlement
            loop.model_ledger.settlements = [UsageSettlement(**s) for s in ledger["settlements"]]
        from src.runtime.retry import AttemptRecord
        loop.attempt_sink.attempts = [AttemptRecord(**a) for a in self.state.attempts]
        expiry = datetime.fromisoformat(self.state.manifest["expires_at"])
        remaining = (expiry - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            raise StoreError("run absolute expiry reached")
        loop.deadline.run_deadline_mono = time.monotonic() + remaining
        loop.session = self
        loop.retry.session = self
        return await loop.run(task_description, task_id=self.store.task_id)

    def close(self):
        self.store.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
