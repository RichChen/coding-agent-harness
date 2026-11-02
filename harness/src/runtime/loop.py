"""AgentLoop：plan → act → observe 主循环（Coding Harness 的核心）。

English: core agent loop — system prompt + tools, LLM round-trip, tool execution,
budget/tracing hooks, terminal conditions.

单次任务流程概览：
  1. 拼装 system prompt 与 tools schema，发送用户任务到 LLM。
  2. 若返回纯文本且无 tool_calls → 视为最终答案并结束。
  3. 若有 tool_calls → 经策略与沙箱执行工具，将 tool 消息写回 messages，再循环。
  4. 终止条件：最终答案、步数上限、总超时/单步超时、或不可恢复的 LLM 错误。
"""

from __future__ import annotations

import asyncio
import copy
import enum
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from src.llm.client import LLMClient, LLMError, LLMErrorKind, LLMResponse, ToolCall
from src.policy.budget import Budget, BudgetExhausted
from src.policy.model_budget import BudgetLedgerError, ModelBudgetLedger
from src.policy.sandbox import Sandbox
from src.execution.base import RUN_OUTPUT_TOTAL_BYTES, RunOutputMeter
from src.runtime.awaitctl import await_with_control
from src.runtime.deadline import (
    DeadlineClock,
    DeadlineExceeded,
    UserCancelled,
    is_write_side_effect,
)
from src.runtime.hooks import HookContext, LoopHooks
from src.runtime.window import (ContextWindowError, WindowPolicyHooks, DEFAULT_WINDOW,
                                admit, view_hash)
from src.runtime.planner import Plan, SimplePlanner
from src.runtime.retry import AttemptSink, RetryConfig, RetryPolicy
from src.runtime.state import AgentState, LoopPhase
from src.runtime.session import decode_response, decode_result, plain
from src.runtime.summary import summary_totals
from src.tracing.store import StoreError, digest
from src.tools.base import ToolContext, ToolResult, ToolStatus
from src.tools.registry import ToolRegistry
from src.tools.validation import (
    DEFAULT_MAX_ARGUMENT_BYTES,
    validate_tool_call_batch,
    validation_failure_to_tool_result,
)
from src.tracing.events import EventBus
from src.tracing.logger import TraceLogger
from src.tracing.models import StepRecord, TraceRun

logger = logging.getLogger(__name__)

# 以下 system prompt 故意使用英文：便于多数 LLM 遵循工具与格式约定；勿改为中文除非你有对照实验需求。
SYSTEM_PROMPT = """You are a coding agent. Your job is to complete tasks by reasoning and using tools.

Workflow:
1. Think about what the task requires.
2. Use tools to read files, explore the workspace, and make changes.
3. When the task is complete, respond with a final answer explaining what you did.

Rules:
- All file paths are relative to the workspace root. Do not use absolute paths.
- Write complete, working code. Do not use placeholders.
- If a tool returns an error, read the error and try a different approach.
- When you are done, do NOT call any more tools. Just provide your final answer."""


class FailureReason(str, enum.Enum):
    NONE = "none"
    TIMEOUT = "timeout"
    TOOL_ERROR = "tool_error"
    POLICY_BLOCK = "policy_block"
    MODEL_PARSE_ERROR = "model_parse_error"
    STEP_LIMIT = "step_limit"
    CANCELLED = "cancelled"
    USER_CANCELLED = "user_cancelled"
    REQUEST_TIMEOUT = "request_timeout"
    TOOL_TIMEOUT = "tool_timeout"
    RUN_DEADLINE = "run_deadline"
    STEP_DEADLINE = "step_deadline"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CLEANUP_FAILED = "cleanup_failed"
    UNKNOWN = "unknown"
    CONTEXT_OVERFLOW = "context_overflow"
    CONTEXT_INVALID = "context_invalid"


@dataclass
class AgentRunResult:
    task_id: str
    success: bool
    final_answer: str | None = None
    failure_reason: FailureReason = FailureReason.NONE
    steps: list[StepRecord] = field(default_factory=list)
    total_elapsed_ms: float = 0.0
    total_tokens: int = 0
    tool_failures: int = 0
    policy_blocks: int = 0
    total_tool_invocations: int = 0
    plan: dict | None = None
    context_events: list[dict] = field(default_factory=list)
    subagent_traces: list[dict] = field(default_factory=list)
    llm_attempts: list[dict] = field(default_factory=list)
    termination_kind: str | None = None
    cleanup_elapsed_s: float | None = None
    model_budget: dict | None = None
    run_output: dict | None = None
    execution: dict | None = None
    summary: dict | None = None


class AgentLoop:
    """主循环：维护对话 messages 与每轮工具执行（conversation + tool cycle）。"""

    def __init__(
        self,
        llm: LLMClient,
        tools: ToolRegistry,
        sandbox: Sandbox,
        budget: Budget,
        retry_config: RetryConfig | None = None,
        tracer: TraceLogger | None = None,
        enabled_tools: list[str] | None = None,
        file_size_limit_bytes: int | None = None,
        tool_output_max_chars: int | None = None,
        read_file_max_lines: int | None = None,
        hooks: LoopHooks | None = None,
        event_bus: EventBus | None = None,
        system_prompt: str | None = None,
        runtime_mode: str = "react",
        planner: SimplePlanner | None = None,
        use_llm_stream: bool = False,
        cancel_event: asyncio.Event | None = None,
        max_tool_argument_bytes: int = DEFAULT_MAX_ARGUMENT_BYTES,
        model_ledger: ModelBudgetLedger | None = None,
        run_output_limit_bytes: int = RUN_OUTPUT_TOTAL_BYTES,
        estimated_input_tokens: int = 512,
    ):
        self.llm = llm
        self.tools = tools
        self.sandbox = sandbox
        self.budget = budget
        self.model_ledger = model_ledger or budget.model_ledger
        self.attempt_sink = AttemptSink()
        self._retry_config = retry_config or RetryConfig()
        self.deadline = DeadlineClock(cancel_event=cancel_event)
        self.retry = RetryPolicy(
            self._retry_config,
            sink=self.attempt_sink,
            deadline=self.deadline,
            model_ledger=self.model_ledger,
        )
        self.tracer = tracer
        self.enabled_tools = enabled_tools
        self.file_size_limit_bytes = file_size_limit_bytes
        self.tool_output_max_chars = tool_output_max_chars
        self.read_file_max_lines = read_file_max_lines
        self.hooks = hooks
        self.event_bus = event_bus
        self.system_prompt = system_prompt if system_prompt else SYSTEM_PROMPT
        self.runtime_mode = runtime_mode
        self.planner = planner
        self.use_llm_stream = use_llm_stream
        self.cancel_event = cancel_event
        self.max_tool_argument_bytes = max_tool_argument_bytes
        self.run_output_meter = RunOutputMeter(limit_bytes=run_output_limit_bytes)
        self.estimated_input_tokens = estimated_input_tokens
        self._cleanup_elapsed_s: float | None = None
        self.session = None
        self.execution_backend = None

    def _result(
        self,
        *,
        task_id: str,
        success: bool,
        failure_reason: FailureReason = FailureReason.NONE,
        final_answer: str | None = None,
        steps: list | None = None,
        total_tokens: int = 0,
        tool_failures: int = 0,
        policy_blocks: int = 0,
        total_tool_invocations: int = 0,
        plan: dict | None = None,
        context_events: list | None = None,
        subagent_traces: list | None = None,
        start_time: float,
        termination_kind: str | None = None,
    ) -> AgentRunResult:
        result = AgentRunResult(
            task_id=task_id,
            success=success,
            final_answer=final_answer,
            failure_reason=failure_reason,
            steps=steps or [],
            total_elapsed_ms=(time.monotonic() - start_time) * 1000,
            total_tokens=total_tokens,
            tool_failures=tool_failures,
            policy_blocks=policy_blocks,
            total_tool_invocations=total_tool_invocations,
            plan=plan,
            context_events=context_events or [],
            subagent_traces=subagent_traces or [],
            llm_attempts=self._attempt_dicts(),
            termination_kind=termination_kind or (
                failure_reason.value if failure_reason != FailureReason.NONE else None
            ),
            cleanup_elapsed_s=self._cleanup_elapsed_s,
            model_budget=self.model_ledger.snapshot() if self.model_ledger else None,
            run_output=self.run_output_meter.snapshot(),
            summary=summary_totals(self.hooks.state) if isinstance(self.hooks, WindowPolicyHooks) else None,
        )
        if result.summary:
            # Legacy total_tokens remains the known-token sum, never an estimate.
            # Unknowns and conservative reserves are explicit in summary/model_budget.
            result.total_tokens += result.summary['known_total_tokens']
        if self.session and self.execution_backend is None:
            self.session.finish(result)
        return result

    def _check_cancelled(
        self,
        task_id: str,
        *,
        start_time: float,
        steps: list,
        total_tokens: int,
        tool_failures: int,
        policy_blocks: int,
        total_tool_invocations: int,
        plan,
        context_events: list,
        subagent_traces: list,
    ) -> AgentRunResult | None:
        if self.cancel_event and self.cancel_event.is_set():
            if self.event_bus:
                self.event_bus.emit("run_end", success=False, reason="user_cancelled")
            return self._result(
                task_id=task_id,
                success=False,
                failure_reason=FailureReason.USER_CANCELLED,
                steps=steps,
                total_tokens=total_tokens,
                tool_failures=tool_failures,
                policy_blocks=policy_blocks,
                total_tool_invocations=total_tool_invocations,
                plan=plan.to_dict() if plan else None,
                context_events=context_events,
                subagent_traces=subagent_traces,
                start_time=start_time,
                termination_kind="user_cancelled",
            )
        return None

    def _usage_tokens(self, usage: dict | None) -> int:
        if not isinstance(usage, dict):
            return 0
        total = usage.get("total_tokens")
        return total if isinstance(total, int) else 0

    def _attempt_dicts(self) -> list[dict]:
        return [
            {
                "attempt": a.attempt,
                "success": a.success,
                "error_kind": a.error_kind,
                "status_code": a.status_code,
                "message": a.message,
                "elapsed_ms": a.elapsed_ms,
                "usage": a.usage,
                "usage_provenance": a.usage_provenance,
            }
            for a in self.attempt_sink.attempts
        ]

    async def _model_view(self, messages, tool_schemas, task_description, task_id, step, context_events):
        """Prepare a disposable view; persist raw history before entering here."""
        raw_hash = view_hash(messages)
        saved = self.session.state.context if self.session else {}
        if saved and saved['step'] == step and saved['raw_message_hash'] == raw_hash:
            view = copy.deepcopy(saved['view'])
            if view_hash(view) != saved['stats']['view_hash']:
                raise StoreError('prepared context hash mismatch')
            if isinstance(self.hooks, WindowPolicyHooks):
                self.hooks.restore(saved['stats']['manager_state'])
            if (isinstance(self.hooks, WindowPolicyHooks) or saved['stats'].get('did_compact')) and (not context_events or context_events[-1] != saved['stats']):
                context_events.append(copy.deepcopy(saved['stats']))
            return self._with_remaining_calls(view)
        view = copy.deepcopy(messages)
        max_output = int(getattr(self.llm, 'max_tokens', 4096) or 4096)
        hook_ctx = HookContext(step_number=step, workspace_root=str(self.sandbox.workspace_root), extra={
            'tools': tool_schemas, 'max_output_tokens': max_output,
            'public_task': {'task_id':task_id,'issue_text':task_description},
            'budget': self.model_ledger.snapshot() if self.model_ledger else {},
            'model_ledger': self.model_ledger,
            'event_bus': self.event_bus,
            'repo_revision': getattr(self, 'public_repo_revision', None),
            'source_event_seq': self.session.state.last_seq if self.session else None,
            'deadline': self.deadline,
            'cancel_event': self.cancel_event,
            'run_output_meter': self.run_output_meter,
            'session': self.session,
            'artifact_dir': (self.session.store.directory / 'command-output'
                             if self.session else None),
        })
        if self.hooks:
            view = await await_with_control(self.hooks.before_llm(view, hook_ctx),
                deadline=self.deadline, cancel_event=self.cancel_event)
        stats = dict(hook_ctx.extra.get('context_stats') or {})
        limit = self.hooks.policy.max_tokens if isinstance(self.hooks, WindowPolicyHooks) else DEFAULT_WINDOW
        stats.update(admit(view,tool_schemas,max_output,limit),step=step,raw_message_hash=raw_hash,view_hash=view_hash(view))
        if not isinstance(self.hooks, WindowPolicyHooks):
            stats.setdefault('strategy','legacy-hook' if self.hooks else 'none')
        if isinstance(self.hooks, WindowPolicyHooks) or stats.get('did_compact'):
            context_events.append(stats)
        if self.session:
            self.session.append('context.prepared', dict(step=step,raw_message_hash=raw_hash,view=view,stats=stats))
        if self.event_bus:
            self.event_bus.emit('context_prepared',task_id=task_id,**stats)
            if stats.get('did_compact'):
                self.event_bus.emit('context_compacted',task_id=task_id,**stats)
        return self._with_remaining_calls(view)

    def _with_remaining_calls(self, view):
        ledger = self.model_ledger
        if ledger is None or ledger.max_model_calls is None:
            return view
        left = max(int(ledger.max_model_calls) - int(ledger.calls_started), 0)
        return list(view) + [{"role": "user", "content": f"remaining model calls: {left}/{ledger.max_model_calls}"}]

    async def _call_llm(
        self,
        messages: list[dict],
        tool_schemas: list[dict],
        step_number: int,
    ) -> LLMResponse:
        """Invoke LLM; each retry attempt reserves/settles independently via RetryPolicy."""
        max_out = int(getattr(self.llm, "max_tokens", 4096) or 4096)
        if not self.use_llm_stream:
            return await self.retry.execute(
                self.llm.chat,
                messages,
                tool_schemas,
                messages=messages,
                tools=tool_schemas,
                max_output_tokens=max_out,
            )
        return await self.retry.execute(
            self._stream_collect,
            messages,
            tool_schemas,
            step_number,
            messages=messages,
            tools=tool_schemas,
            max_output_tokens=max_out,
        )

    async def _stream_collect(
        self,
        messages: list[dict],
        tool_schemas: list[dict],
        step_number: int,
    ) -> LLMResponse:
        # Fresh aggregation per attempt — retry must not reuse partial tool JSON.
        # Stalled reads are interrupted by await_with_control wrapping this coroutine
        # (cancel closes the agen in finally → closes active HTTP response).
        final: LLMResponse | None = None
        agen = self.llm.stream_chat(messages, tool_schemas)
        try:
            async for event in agen:
                if self.event_bus:
                    payload = {k: v for k, v in event.data.items() if k != "response"}
                    self.event_bus.emit(event.event, step=step_number, **payload)
                if event.event == "llm_stream_done":
                    response = event.data.get("response")
                    if isinstance(response, LLMResponse):
                        final = response
                    else:
                        final = LLMResponse(
                            text=None,
                            tool_calls=[],
                            finish_reason=event.data.get("finish_reason", "stop"),
                            usage=event.data.get("usage") or {},
                        )
                elif event.event == "llm_error":
                    kind_raw = event.data.get("kind", LLMErrorKind.UNKNOWN.value)
                    try:
                        kind = LLMErrorKind(kind_raw)
                    except ValueError:
                        kind = LLMErrorKind.UNKNOWN
                    raise LLMError(
                        event.data.get("message", "LLM error"),
                        kind=kind,
                        status_code=event.data.get("status_code"),
                    )
        finally:
            aclose = getattr(agen, "aclose", None)
            if callable(aclose):
                try:
                    await aclose()
                except Exception:
                    logger.debug("error closing stream agen", exc_info=True)

        if final is None:
            raise LLMError(
                "stream finished without llm_stream_done",
                kind=LLMErrorKind.INCOMPLETE,
            )
        return final

    async def run(self, task_description: str, task_id: str = "unknown") -> AgentRunResult:
        backend = self.execution_backend
        if backend is None:
            return await self._run_loop(task_description, task_id)
        if self.session and self.session.state.terminal is not None:
            return decode_result(self.session.state.terminal)
        from src.execution.docker import read_workspace
        if self.session and self.session.state.loop:
            raise StoreError("Docker interrupted-session resume requires explicit reconciliation; refusing fresh container replay")
        started = time.monotonic()
        result = None
        try:
            if self.cancel_event and self.cancel_event.is_set():
                raise UserCancelled()
            # Session creation consumes the SAME run deadline as model/tool work.
            limit = started + self.budget.total_timeout_seconds
            if self.deadline.run_deadline_mono is not None:
                limit = min(limit, self.deadline.run_deadline_mono)
            self.deadline.run_deadline_mono = limit
            seed = backend.seed_files if backend.seed_files is not None else read_workspace(self.sandbox.workspace_root)
            backend.seed_files = dict(seed)
            if self.session:
                if any(e['event_type'] == 'execution.starting' for e in self.session.store.read_events()):
                    raise StoreError('interrupted Docker creation requires reconciliation; refusing replay')
                self.session.append('execution.starting', dict(container=backend.name, context=backend.context))
            await await_with_control(backend.start(seed),
                deadline=self.deadline, cancel_event=self.cancel_event, cleanup_grace_s=12)
            result = await self._run_loop(task_description, task_id)
        except UserCancelled:
            result = self._result(task_id=task_id, success=False, failure_reason=FailureReason.USER_CANCELLED, start_time=started)
        except DeadlineExceeded as exc:
            result = self._result(task_id=task_id, success=False,
                failure_reason=FailureReason.CLEANUP_FAILED if exc.kind.value == "cleanup_failed" else FailureReason.RUN_DEADLINE, start_time=started)
        except asyncio.CancelledError:
            raise
        except StoreError:
            raise
        except Exception as exc:
            result = self._result(task_id=task_id, success=False, failure_reason=FailureReason.UNKNOWN,
                                  start_time=started, termination_kind="backend_unavailable")
            backend.unavailable_error = str(exc)[:1000]
        finally:
            cleanup = asyncio.create_task(self._close_backend(backend, result))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise
        result.execution = dict(capabilities=backend.capabilities, lifecycle=backend.lifecycle,
                                snapshot_error=backend.snapshot_error, cleanup_ok=backend.cleanup_ok,
                                unavailable_error=getattr(backend, 'unavailable_error', None))
        if not backend.cleanup_ok:
            result.success = False
            result.failure_reason = FailureReason.CLEANUP_FAILED
            result.termination_kind = "cleanup_failed"
        elif backend.snapshot_error:
            result.success = False
            result.failure_reason = FailureReason.UNKNOWN
            result.termination_kind = "candidate_snapshot_failed"
        elif backend.snapshot is None and result.success:
            result.success = False
            result.failure_reason = FailureReason.TOOL_ERROR
            result.termination_kind = 'container_terminated_before_snapshot'
        result.cleanup_elapsed_s = backend.cleanup_elapsed_s
        result.total_elapsed_ms = (time.monotonic()-started)*1000
        if self.session:
            from src.eval.grader import tree_hash
            if backend.snapshot is not None:
                refs = {}
                for name, data in backend.snapshot.items():
                    refs[name] = [self.session.artifacts.publish(data[i:i+1024*1024])
                                  for i in range(0, len(data), 1024*1024)]
                self.session.append('execution.snapshot', dict(files=refs,
                    sha256=tree_hash(backend.snapshot), _artifacts=[r for chunks in refs.values() for r in chunks]))
                result.execution['candidate_hash'] = tree_hash(backend.snapshot)
            self.session.finish(result)
        return result

    async def _run_loop(self, task_description: str, task_id: str = "unknown") -> AgentRunResult:
        """对单条任务执行完整 agent 循环。

        Args:
            task_description: 自然语言任务描述。
            task_id: 用于 trace / 评测报表的任务标识。

        Returns:
            AgentRunResult：含每步记录与汇总统计。
        """
        if self.session is None and isinstance(self.hooks, WindowPolicyHooks):
            self.hooks.reset()
        workspace_root = self.sandbox.workspace_root
        state = AgentState()
        steps: list[StepRecord] = []
        total_tokens = 0
        tool_failures = 0
        policy_blocks = 0
        total_tool_invocations = 0
        start_time = time.monotonic()
        plan: Plan | None = None
        replan_used = False
        context_events: list[dict] = []
        subagent_traces: list[dict] = []

        # 初始 messages：system + user（tools schema 每轮相同，由 LLMClient 附带）
        tool_schemas = self.tools.schemas(self.enabled_tools)
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": task_description},
        ]

        if self.runtime_mode == "plan_act" and self.planner:
            plan = self.planner.create_plan(task_description, self.enabled_tools)
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Current execution plan. Use it to guide tool choices; "
                        "do not treat it as user-visible output.\n"
                        f"{plan.to_prompt_text()}"
                    ),
                }
            )

        self.budget.start()
        # Preserve a pre-injected absolute run deadline (e.g. shared parent) as a
        # hard upper bound — never extend past it on child start/retry.
        prior_run_deadline = self.deadline.run_deadline_mono
        self.deadline.cancel_event = self.cancel_event
        self.deadline.set_run(self.budget.total_timeout_seconds)
        if prior_run_deadline is not None and self.deadline.run_deadline_mono is not None:
            self.deadline.run_deadline_mono = min(
                self.deadline.run_deadline_mono, prior_run_deadline
            )
        self.run_output_meter = RunOutputMeter(limit_bytes=self.run_output_meter.limit_bytes)

        saved = self.session.state.loop if self.session else {}
        if self.session:
            from datetime import datetime
            started_at = datetime.fromisoformat(self.session.state.manifest["started_at"]).timestamp()
            start_time = time.monotonic() - max(0, time.time() - started_at)
        resume_response = None
        resumed_step = bool(saved)
        response_operations = []
        if self.session and self.session.state.terminal is not None:
            return decode_result(self.session.state.terminal)
        if saved:
            messages = saved["messages"]
            steps = [StepRecord(**s) for s in saved["steps"]]
            state.step_number = saved["step_number"]
            self.budget._step_count = saved["step_count"]
            total_tokens = saved["total_tokens"]
            tool_failures = saved["tool_failures"]
            policy_blocks = saved["policy_blocks"]
            total_tool_invocations = saved["total_tool_invocations"]
            context_events = saved.get("context_events", [])
            self.run_output_meter = RunOutputMeter(**self.session.state.run_output)
            step_remaining = saved["step_expires_at"] - time.time()
            self.deadline.step_deadline_mono = time.monotonic() + step_remaining
            response_operations = saved.get("response_operations", [])
            if saved.get("response"):
                resume_response = decode_response(saved["response"])
        if self.session and self.session.state.pending_model:
            return self._result(task_id=task_id, success=False, failure_reason=FailureReason.UNKNOWN,
                                steps=steps, total_tokens=total_tokens, tool_failures=tool_failures,
                                policy_blocks=policy_blocks, total_tool_invocations=total_tool_invocations,
                                start_time=start_time, termination_kind="outcome_unknown")

        def persist_boundary(response=None):
            if self.session:
                self.session.save_loop(dict(
                    task_hash=digest(task_description.encode()), messages=messages, steps=plain(steps),
                    step_number=state.step_number, step_count=self.budget.step_count,
                    step_expires_at=time.time() + (self.deadline.step_deadline_mono - time.monotonic()),
                    total_tokens=total_tokens, tool_failures=tool_failures, policy_blocks=policy_blocks,
                    context_events=context_events,
                    total_tool_invocations=total_tool_invocations, run_output=self.run_output_meter.snapshot(),
                    response=plain(response) if response else None, response_operations=response_operations))

        if self.event_bus:
            self.event_bus.open(task_id)
            self.event_bus.emit("run_start", task_id=task_id)
            if plan:
                self.event_bus.emit("plan_created", task_id=task_id, plan=plan.to_dict())

        try:
            while True:
                if self.execution_backend is not None and not self.execution_backend.active:
                    return self._result(task_id=task_id, success=False, failure_reason=FailureReason.TOOL_ERROR,
                        steps=steps, total_tokens=total_tokens, tool_failures=tool_failures,
                        total_tool_invocations=total_tool_invocations, start_time=start_time,
                        termination_kind='container_terminated')
                cancelled = self._check_cancelled(
                    task_id,
                    start_time=start_time,
                    steps=steps,
                    total_tokens=total_tokens,
                    tool_failures=tool_failures,
                    policy_blocks=policy_blocks,
                    total_tool_invocations=total_tool_invocations,
                    plan=plan,
                    context_events=context_events,
                    subagent_traces=subagent_traces,
                )
                if cancelled:
                    return cancelled

                self.budget.check_step_timeout()

                # ---- THINKING：等待 LLM ----
                state.transition(LoopPhase.THINKING)
                if not resumed_step:
                    self.budget.start_step()
                    self.deadline.begin_step(self.budget.step_timeout_seconds)
                resumed_step = False
                self.deadline.clear_tool()

                step_start = time.monotonic()
                try:
                    self.deadline.raise_if_expired_or_cancelled()
                    if resume_response is not None:
                        response = resume_response
                        resume_response = None
                    else:
                        persist_boundary()
                        model_messages = await self._model_view(messages,tool_schemas,task_description,
                                                                task_id,state.step_number,context_events)
                        response: LLMResponse = await self._call_llm(
                            model_messages, tool_schemas, state.step_number
                        )
                        response_operations = [uuid.uuid4().hex for _ in response.tool_calls]
                        persist_boundary(response)
                except ContextWindowError as overflow:
                    stats = dict(overflow.stats, step=state.step_number, error_kind=overflow.kind)
                    context_events.append(stats)
                    if self.session:
                        self.session.append('context.rejected',stats)
                    return self._result(task_id=task_id, success=False,
                        failure_reason=FailureReason.CONTEXT_OVERFLOW if overflow.kind=='context_overflow' else FailureReason.CONTEXT_INVALID,
                        steps=steps,total_tokens=total_tokens,tool_failures=tool_failures,policy_blocks=policy_blocks,
                        total_tool_invocations=total_tool_invocations,context_events=context_events,
                        start_time=start_time,termination_kind=stats.get('rejection_reason',overflow.kind))
                except UserCancelled:
                    return self._result(
                        task_id=task_id,
                        success=False,
                        failure_reason=FailureReason.USER_CANCELLED,
                        steps=steps,
                        total_tokens=total_tokens,
                        tool_failures=tool_failures,
                        policy_blocks=policy_blocks,
                        total_tool_invocations=total_tool_invocations,
                        plan=plan.to_dict() if plan else None,
                        context_events=context_events,
                        subagent_traces=subagent_traces,
                        start_time=start_time,
                    )
                except DeadlineExceeded as de:
                    self._cleanup_elapsed_s = getattr(de, "elapsed_s", None)
                    fr = {
                        "run_deadline": FailureReason.RUN_DEADLINE,
                        "step_deadline": FailureReason.STEP_DEADLINE,
                        "tool_timeout": FailureReason.TOOL_TIMEOUT,
                        "request_timeout": FailureReason.REQUEST_TIMEOUT,
                        "cleanup_failed": FailureReason.CLEANUP_FAILED,
                    }.get(de.kind.value, FailureReason.TIMEOUT)
                    if self.event_bus:
                        self.event_bus.emit("run_end", success=False, reason=de.kind.value)
                    return self._result(
                        task_id=task_id,
                        success=False,
                        failure_reason=fr,
                        steps=steps,
                        total_tokens=total_tokens,
                        tool_failures=tool_failures,
                        policy_blocks=policy_blocks,
                        total_tool_invocations=total_tool_invocations,
                        plan=plan.to_dict() if plan else None,
                        context_events=context_events,
                        subagent_traces=subagent_traces,
                        start_time=start_time,
                        termination_kind=de.kind.value,
                    )
                except BudgetLedgerError as be:
                    if self.event_bus:
                        self.event_bus.emit("run_end", success=False, reason=be.reason.value)
                    return self._result(
                        task_id=task_id,
                        success=False,
                        failure_reason=FailureReason.BUDGET_EXHAUSTED,
                        steps=steps,
                        total_tokens=total_tokens,
                        tool_failures=tool_failures,
                        policy_blocks=policy_blocks,
                        total_tool_invocations=total_tool_invocations,
                        plan=plan.to_dict() if plan else None,
                        context_events=context_events,
                        subagent_traces=subagent_traces,
                        start_time=start_time,
                        termination_kind=be.reason.value,
                    )
                except StoreError:
                    raise  # A journal failure must never permit another external effect.
                except Exception as e:
                    logger.error(f"LLM call failed: {e}")
                    state.record_failure(str(e))
                    steps.append(
                        StepRecord(
                            step=state.step_number,
                            phase=LoopPhase.FAILED,
                            messages_snapshot=len(messages),
                            llm_text=None,
                            tool_calls=[],
                            tool_results=[],
                            elapsed_ms=(time.monotonic() - step_start) * 1000,
                        )
                    )
                    # Distinguish timeout-ish LLM errors from parse errors.
                    fr = FailureReason.MODEL_PARSE_ERROR
                    if isinstance(e, LLMError) and e.kind == LLMErrorKind.TIMEOUT:
                        fr = FailureReason.REQUEST_TIMEOUT
                    if self.event_bus:
                        self.event_bus.emit("run_end", success=False, reason=fr.value)
                    return self._result(
                        task_id=task_id,
                        success=False,
                        failure_reason=fr,
                        steps=steps,
                        total_tokens=total_tokens,
                        tool_failures=tool_failures,
                        policy_blocks=policy_blocks,
                        total_tool_invocations=total_tool_invocations,
                        plan=plan.to_dict() if plan else None,
                        context_events=context_events,
                        subagent_traces=subagent_traces,
                        start_time=start_time,
                    )

                total_tokens += self._usage_tokens(response.usage)
                step_elapsed = (time.monotonic() - step_start) * 1000

                if self.event_bus:
                    self.event_bus.emit(
                        "llm_complete",
                        step=state.step_number,
                        has_tool_calls=response.has_tool_calls,
                        tool_count=len(response.tool_calls),
                        elapsed_ms=round(step_elapsed, 2),
                        model=getattr(self.llm, "model", None),
                        usage=response.usage,
                        usage_provenance=response.usage_provenance,
                        capability=(
                            response.capability.value if response.capability else None
                        ),
                    )

                # ---- 无 tool_calls：视为最终自然语言答案 ----
                if not response.has_tool_calls:
                    state.transition(LoopPhase.DONE)
                    if plan:
                        plan.mark_done()
                    steps.append(
                        StepRecord(
                            step=state.step_number,
                            phase=LoopPhase.DONE,
                            messages_snapshot=len(messages),
                            llm_text=response.text,
                            tool_calls=[],
                            tool_results=[],
                            elapsed_ms=step_elapsed,
                        )
                    )
                    if self.event_bus:
                        self.event_bus.emit("run_end", success=True)
                    return self._result(
                        task_id=task_id,
                        success=True,
                        final_answer=response.text,
                        steps=steps,
                        total_tokens=total_tokens,
                        tool_failures=tool_failures,
                        policy_blocks=policy_blocks,
                        total_tool_invocations=total_tool_invocations,
                        plan=plan.to_dict() if plan else None,
                        context_events=context_events,
                        subagent_traces=subagent_traces,
                        start_time=start_time,
                    )

                # ---- ACTING：执行本轮所有 tool_calls ----
                state.transition(LoopPhase.ACTING)

                cancelled = self._check_cancelled(
                    task_id,
                    start_time=start_time,
                    steps=steps,
                    total_tokens=total_tokens,
                    tool_failures=tool_failures,
                    policy_blocks=policy_blocks,
                    total_tool_invocations=total_tool_invocations,
                    plan=plan,
                    context_events=context_events,
                    subagent_traces=subagent_traces,
                )
                if cancelled:
                    return cancelled

                # OpenAI 格式：assistant 消息携带 tool_calls，后续每条 tool 结果单独一条 message
                assistant_msg = {
                    "role": "assistant",
                    "content": response.text,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": self._serialize_args(tc),
                            },
                        }
                        for tc in response.tool_calls
                    ],
                }
                messages.append(assistant_msg)

                # Batch-validate every call before any side effect.
                batch = validate_tool_call_batch(
                    response.tool_calls,
                    registry=self.tools,
                    enabled_tools=self.enabled_tools,
                )
                tool_results: list[ToolResult] = []

                if not batch.ok:
                    if self.event_bus:
                        self.event_bus.emit(
                            "tool_validation_failed",
                            step=state.step_number,
                            offending_ids=batch.offending_ids,
                            failure_kinds=[f.kind.value for f in batch.failures],
                        )
                    # No tool.execute — record blocked results for the whole batch.
                    failure_by_id = {f.call_id: f for f in batch.failures if f.call_id}
                    for tc in response.tool_calls:
                        failure = failure_by_id.get(tc.id)
                        if failure is None:
                            # Sibling call blocked because another call in the batch failed.
                            failure = batch.failures[0]
                            tr = ToolResult(
                                call_id=tc.id,
                                name=tc.name,
                                success=False,
                                output="",
                                error=(
                                    f"kind=batch_blocked; blocked because sibling call "
                                    f"{failure.call_id or '(unknown)'} failed validation "
                                    f"({failure.kind.value})"
                                ),
                                status=ToolStatus.BLOCKED,
                                error_kind="batch_blocked",
                            )
                        else:
                            tr = validation_failure_to_tool_result(failure)
                        tool_failures += 1
                        tool_results.append(tr)
                        messages.append(tr.to_message())
                else:
                    # 顺序执行；终止态阻止后续工具与后续模型请求
                    batch_stop_kind: str | None = None  # user_cancelled|run_deadline|...
                    safe_artifact_root = workspace_root / ".harness_artifacts" / uuid.uuid4().hex
                    if self.execution_backend is not None:
                        safe_artifact_root = None  # Docker artifacts belong to host session storage only.
                    if self.session:
                        safe_artifact_root = self.session.store.directory / "command-output"

                    def _skip_remaining(kind: str, msg: str, status: ToolStatus) -> None:
                        nonlocal batch_stop_kind
                        batch_stop_kind = kind
                        # Mark only tools not yet recorded as skipped.
                        done_ids = {r.call_id for r in tool_results}
                        for v in batch.validated:
                            if v.call_id in done_ids:
                                continue
                            tool_results.append(
                                ToolResult(
                                    call_id=v.call_id,
                                    name=v.name,
                                    success=False,
                                    output="",
                                    error=msg,
                                    status=status,
                                    error_kind=kind,
                                )
                            )
                            messages.append(tool_results[-1].to_message())

                    for tool_index, validated in enumerate(batch.validated):
                        if batch_stop_kind is not None:
                            break
                        if self.cancel_event is not None and self.cancel_event.is_set():
                            _skip_remaining(
                                "user_cancelled",
                                "skipped: cancelled before tool start",
                                ToolStatus.CANCELLED,
                            )
                            break
                        if self.run_output_meter.producers_stopped:
                            _skip_remaining(
                                "run_output_limit",
                                "skipped: run output quota exhausted",
                                ToolStatus.FAILED,
                            )
                            break
                        expired = self.deadline.which_expired()
                        if expired is not None:
                            _skip_remaining(
                                expired.value,
                                f"skipped: {expired.value}",
                                ToolStatus.TIMED_OUT,
                            )
                            break

                        total_tool_invocations += 1
                        # Host-safe artifact dir — never trust raw model call_id as path.
                        op_safe = response_operations[tool_index] if self.session else uuid.uuid4().hex
                        ctx = ToolContext(
                            workspace_root=workspace_root,
                            sandbox=self.sandbox,
                            file_size_limit_bytes=self.file_size_limit_bytes,
                            tool_output_max_chars=self.tool_output_max_chars,
                            read_file_max_lines=self.read_file_max_lines,
                            call_id=validated.call_id,
                            extra={
                                "event_bus": self.event_bus,
                                "deadline": self.deadline,
                                "cancel_event": self.cancel_event,
                                "run_output_meter": self.run_output_meter,
                                "operation_id": op_safe,
                                "attempt_id": uuid.uuid4().hex,
                                "artifact_dir": safe_artifact_root,
                            },
                        )
                        previous = self.session.previous_result(op_safe) if self.session else None
                        if self.session and previous is None:
                            ctx.extra["attempt_id"] = self.session.prepare_tool(validated, ctx)
                        try:
                            if previous is not None:
                                tr = previous
                            else:
                                tr = await await_with_control(
                                    validated.tool.execute(dict(validated.arguments), ctx),
                                    deadline=self.deadline,
                                    cancel_event=self.cancel_event,
                                    cleanup_grace_s=12 if self.execution_backend else 2,
                                )
                        except UserCancelled as stopped:
                            tr = getattr(stopped, "work_result", None) or ToolResult(
                                call_id=validated.call_id,
                                name=validated.name,
                                success=False,
                                output="",
                                error="cancelled during tool execution",
                                status=ToolStatus.CANCELLED,
                                error_kind="user_cancelled",
                            )
                            tr.success = False
                            tr.status = ToolStatus.CANCELLED
                            tr.error_kind = "user_cancelled"
                            batch_stop_kind = "user_cancelled"
                        except DeadlineExceeded as de:
                            tr = getattr(de, "work_result", None) or ToolResult(
                                call_id=validated.call_id,
                                name=validated.name,
                                success=False,
                                output="",
                                error=f"deadline during tool: {de.kind.value}",
                                status=(ToolStatus.OUTCOME_UNKNOWN if de.kind.value == "cleanup_failed" else ToolStatus.TIMED_OUT),
                                error_kind=de.kind.value,
                            )
                            tr.success = False
                            tr.status = ToolStatus.OUTCOME_UNKNOWN if de.kind.value == "cleanup_failed" else ToolStatus.TIMED_OUT
                            tr.error_kind = de.kind.value
                            if de.kind.value == "cleanup_failed":
                                tr.cleanup_state = "cleanup_failed"
                                tr.cleanup_elapsed_s = getattr(de, "elapsed_s", None)
                            if de.kind.value == "tool_timeout":
                                seconds = getattr(validated.tool, "timeout_seconds", None)
                                note = f"命令在 {int(seconds)} 秒后超时，输出已截断\n" if seconds else "命令超时，输出已截断\n"
                                if not (tr.output or "").startswith("命令在"):
                                    tr.output = note + (tr.output or "")
                            else:
                                batch_stop_kind = de.kind.value

                        if self.session and previous is None:
                            self.session.record_result(op_safe, tr, self.run_output_meter.snapshot())

                        if tr.status == ToolStatus.OUTCOME_UNKNOWN or tr.error_kind in (
                            "cleanup_failed",
                            "run_output_limit",
                        ):
                            if tr.cleanup_elapsed_s is not None:
                                self._cleanup_elapsed_s = tr.cleanup_elapsed_s
                            batch_stop_kind = tr.error_kind or "cleanup_failed"
                        if tr.status == ToolStatus.CANCELLED or tr.error_kind == "user_cancelled":
                            batch_stop_kind = batch_stop_kind or "user_cancelled"
                        if tr.status == ToolStatus.TIMED_OUT and tr.error_kind not in (None, "tool_timeout"):
                            batch_stop_kind = batch_stop_kind or tr.error_kind

                        if not tr.success:
                            tool_failures += 1
                            if tr.error and "policy" in tr.error.lower():
                                policy_blocks += 1
                            if tr.error_kind == "policy_block":
                                policy_blocks += 1

                        tool_results.append(tr)
                        messages.append(tr.to_message())
                        if validated.name == "subagent_task" and tr.success:
                            subagent_trace = self._extract_subagent_trace(tr.output)
                            if subagent_trace:
                                subagent_traces.append(subagent_trace)

                        if batch_stop_kind is not None:
                            # Skip any remaining tools with accurate stop kind (not fake cancel).
                            status = (
                                ToolStatus.CANCELLED
                                if batch_stop_kind == "user_cancelled"
                                else ToolStatus.TIMED_OUT
                                if "deadline" in batch_stop_kind or "timeout" in batch_stop_kind
                                else ToolStatus.OUTCOME_UNKNOWN
                                if batch_stop_kind in ("cleanup_failed",)
                                else ToolStatus.FAILED
                            )
                            _skip_remaining(
                                batch_stop_kind,
                                f"skipped: run stopping ({batch_stop_kind})",
                                status,
                            )
                            break

                    if batch_stop_kind is not None:
                        steps.append(
                            StepRecord(
                                step=state.step_number,
                                phase=LoopPhase.ACTING,
                                messages_snapshot=len(messages),
                                llm_text=response.text,
                                tool_calls=[
                                    {
                                        "name": tc.name,
                                        "args": tc.arguments if tc.arguments is not None else None,
                                        "raw_arguments": tc.raw_arguments or None,
                                        "parse_error": (
                                            tc.parse_error.kind.value if tc.parse_error else None
                                        ),
                                    }
                                    for tc in response.tool_calls
                                ],
                                tool_results=[
                                    {
                                        "name": r.name,
                                        "success": r.success,
                                        "status": r.status.value if r.status else None,
                                        "error_kind": r.error_kind,
                                        "output": r.output[:500],
                                        "exit_code": r.exit_code,
                                        "cleanup_state": r.cleanup_state,
                                        "truncated": r.truncated,
                                        "bytes_seen": r.bytes_seen,
                                        "bytes_kept": r.bytes_kept,
                                    }
                                    for r in tool_results
                                ],
                                elapsed_ms=(time.monotonic() - step_start) * 1000,
                            )
                        )
                        if self.tracer:
                            self.tracer.log_step(steps[-1])
                        fr_map = {
                            "user_cancelled": FailureReason.USER_CANCELLED,
                            "run_deadline": FailureReason.RUN_DEADLINE,
                            "step_deadline": FailureReason.STEP_DEADLINE,
                            "tool_timeout": FailureReason.TOOL_TIMEOUT,
                            "request_timeout": FailureReason.REQUEST_TIMEOUT,
                            "cleanup_failed": FailureReason.CLEANUP_FAILED,
                            "run_output_limit": FailureReason.TOOL_ERROR,
                        }
                        fr = fr_map.get(batch_stop_kind, FailureReason.UNKNOWN)
                        if self.event_bus:
                            self.event_bus.emit(
                                "run_end", success=False, reason=batch_stop_kind
                            )
                        return self._result(
                            task_id=task_id,
                            success=False,
                            failure_reason=fr,
                            steps=steps,
                            total_tokens=total_tokens,
                            tool_failures=tool_failures,
                            policy_blocks=policy_blocks,
                            total_tool_invocations=total_tool_invocations,
                            plan=plan.to_dict() if plan else None,
                            context_events=context_events,
                            subagent_traces=subagent_traces,
                            start_time=start_time,
                            termination_kind=batch_stop_kind,
                        )

                # Hook: after all tool results appended (e.g. compaction)
                if self.hooks:
                    hook_ctx = HookContext(
                        step_number=state.step_number,
                        workspace_root=str(workspace_root),
                    )
                    messages = await self.hooks.after_tools(messages, hook_ctx)

                if self.event_bus and response.has_tool_calls:
                    self.event_bus.emit(
                        "tools_complete",
                        step=state.step_number,
                        success_count=sum(1 for r in tool_results if r.success),
                        failure_count=sum(1 for r in tool_results if not r.success),
                    )

                # ---- OBSERVING：记录本步 trace，准备进入下一轮 THINKING ----
                state.transition(LoopPhase.OBSERVING)
                if plan:
                    plan_changed = False
                    if (
                        not replan_used
                        and any(not r.success for r in tool_results)
                        and plan.maybe_replan("tool failure")
                    ):
                        replan_used = True
                        plan_changed = True
                        if self.event_bus:
                            self.event_bus.emit(
                                "plan_replanned",
                                step=state.step_number,
                                reason="tool_failure",
                                plan=plan.to_dict(),
                            )
                    else:
                        if plan.advance_after_tools(
                            [tc.name for tc in response.tool_calls],
                            success_count=sum(1 for r in tool_results if r.success),
                        ):
                            plan_changed = True
                    if self.event_bus:
                        self.event_bus.emit(
                            "plan_updated",
                            step=state.step_number,
                            plan=plan.to_dict(),
                        )
                    if plan_changed:
                        messages.append(
                            {
                                "role": "system",
                                "content": "Plan update: " + plan.to_prompt_text(),
                            }
                        )

                steps.append(
                    StepRecord(
                        step=state.step_number,
                        phase=LoopPhase.ACTING,
                        messages_snapshot=len(messages),
                        llm_text=response.text,
                        tool_calls=[
                            {
                                "name": tc.name,
                                "args": tc.arguments if tc.arguments is not None else None,
                                "raw_arguments": tc.raw_arguments or None,
                                "parse_error": (
                                    tc.parse_error.kind.value if tc.parse_error else None
                                ),
                            }
                            for tc in response.tool_calls
                        ],
                        tool_results=[
                            {
                                "name": r.name,
                                "success": r.success,
                                "status": r.status.value if r.status else None,
                                "error_kind": r.error_kind,
                                "output": r.output[:500],
                            }
                            for r in tool_results
                        ],
                        elapsed_ms=step_elapsed,
                    )
                )

                if self.tracer:
                    self.tracer.log_step(steps[-1])

                state.step_number += 1

        except BudgetExhausted as e:
            logger.warning(f"Budget exhausted: {e.reason}")
            kind = getattr(e, "kind", "")
            if kind == "step_limit":
                fr = FailureReason.STEP_LIMIT
            elif kind == "run_deadline":
                fr = FailureReason.RUN_DEADLINE
            elif kind == "step_deadline":
                fr = FailureReason.STEP_DEADLINE
            elif "timeout" in e.reason.lower():
                fr = FailureReason.TIMEOUT
            else:
                fr = FailureReason.BUDGET_EXHAUSTED
            if self.event_bus:
                self.event_bus.emit("budget_exhausted", reason=e.reason)
                self.event_bus.emit("run_end", success=False, reason=fr.value)
            return self._result(
                task_id=task_id,
                success=False,
                failure_reason=fr,
                steps=steps,
                total_tokens=total_tokens,
                tool_failures=tool_failures,
                policy_blocks=policy_blocks,
                total_tool_invocations=total_tool_invocations,
                plan=plan.to_dict() if plan else None,
                context_events=context_events,
                subagent_traces=subagent_traces,
                start_time=start_time,
                termination_kind=kind or fr.value,
            )
        finally:
            if self.event_bus:
                self.event_bus.close()

    @staticmethod
    async def _close_backend(backend, result):
        """Export the workspace first whenever the container is still reachable."""
        if result is not None and not backend.active and not getattr(backend, "snapshot_error", None):
            backend.snapshot_error = "container_gone_before_snapshot"
        await backend.close(snapshot=result is not None and backend.active)

    @staticmethod
    def _serialize_args(tc: ToolCall) -> str:
        """Serialize assistant tool_call arguments without host metadata."""
        if tc.raw_arguments:
            return tc.raw_arguments
        if tc.arguments is None:
            return "{}"
        return json.dumps(tc.arguments, ensure_ascii=False)

    @staticmethod
    def _extract_subagent_trace(output: str) -> dict | None:
        try:
            payload = json.loads(output)
        except Exception:
            return None

        trace_file = payload.get("trace_file")
        if not trace_file:
            return None
        record = {
            "child_task_id": payload.get("child_task_id", ""),
            "trace_file": trace_file,
        }
        if payload.get("success") is not None:
            record["success"] = payload.get("success")
        if payload.get("schema_ok") is not None:
            record["schema_ok"] = payload.get("schema_ok")
        return record



"""
AgentLoop._run_loop
输入：任务文本 + task_id，以及 build_agent 装好、RunSession.run 挂上 session 的 self（llm/tools/sandbox/budget/hooks）。
输出：AgentRunResult（success、final_answer、steps、各计数器、model_budget），返回给 RunSession.run 再到教学入口；过程中每轮写 state.saved 等事件，每步 tracer.log_step。
失败入口：几乎所有失败都 return success=False 并带 failure_reason/termination_kind，看 summary.json；单个工具失败不结束循环，只让 tool_failures 加 1、报错进下一轮的 tool message；只有 StoreError 会以 traceback 直接抛出。
"""