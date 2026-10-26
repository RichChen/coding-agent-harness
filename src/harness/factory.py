"""Agent 装配工厂：配置加载 + AgentLoop 构建。

English: single source of truth for loading YAML configs and assembling AgentLoop instances.
No business logic — pure wiring.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

from src.config.schema import HarnessConfig
from src.execution.base import OutputQuotas
from src.execution.local import LocalExecutionBackend
from src.execution.docker import DockerExecutionBackend, DEFAULT_IMAGE
from src.tools.container_files import ContainerFileTool
from src.llm.client import LLMClient
from src.policy.budget import Budget
from src.policy.model_budget import ModelBudgetLedger
from src.policy.rules import CommandPolicy
from src.policy.sandbox import Sandbox
from src.runtime.loop import AgentLoop
from src.runtime.context import ContextPolicyConfig, build_context_hooks
from src.runtime.summary import LLMSummaryProvider
from src.runtime.planner import SimplePlanner
from src.runtime.retry import RetryConfig
from src.policy.dispatcher import ReadSearchDispatcher
from src.tools.command import RunCommandTool
from src.tools.edit import EditFileTool
from src.tools.files import ListDirTool, ReadFileTool, WriteFileTool
from src.tools.search import SearchCodeTool
from src.tools.patch import SearchReplaceTool
from src.tools.registry import ToolRegistry
from src.tracing.events import EventBus
from src.tracing.logger import TraceLogger


def _load_dotenv_if_present(dotenv_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not dotenv_path.exists():
        return values

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values

"""
输入：YAML 路径；其中 ${VAR:默认值} 依次用环境变量、上级目录的 .env、默认值替换。
输出：经 HarnessConfig 校验后的普通 dict，交给 build_agent；教学入口还把它存成 config.json。
失败入口：YAML 语法错误，或 pydantic ValidationError（未知字段、类型不对，报错里有字段路径），都在终端 traceback 里。
"""
def load_config(config_path: str | Path) -> dict:
    """Load a YAML config file with ${VAR:default} env-var substitution.

    Validates against HarnessConfig schema — unknown keys or wrong types
    raise pydantic ValidationError with a clear field path.
    """
    config_path = Path(config_path)
    dotenv_values = {}
    for parent in config_path.resolve().parents:
        found = _load_dotenv_if_present(parent / ".env")
        if found:
            dotenv_values = found
            break

    with open(config_path, encoding="utf-8") as f:
        raw = f.read()

    def resolve_env(match):
        var = match.group(1)
        default = match.group(2) if match.lastindex >= 2 else ""
        return os.environ.get(var, dotenv_values.get(var, default))

    raw = re.sub(r"\$\{(\w+):?([^}]*)\}", resolve_env, raw)
    data = yaml.safe_load(raw)
    # Validate and return as dict (not model instance) for backwards compat
    return HarnessConfig.model_validate(data).model_dump()

"""
输入：配置 dict + workspace 路径（+ 可选的 tracer、event_bus、外部注入的执行后端）。
输出：装好 llm/tools/sandbox/budget/retry/hooks 的 AgentLoop，交给调用方，再由 RunSession.run 驱动。
失败入口：组合不合法时直接抛 ValueError（如 Docker 配置下开了不允许的工具、启用子 agent 却不共享预算）
，system prompt 文件不存在时抛 FileNotFoundError；都发生在会话创建之前，只能看终端 traceback。
"""
def build_agent(
    config: dict,
    workspace: Path,
    *,
    tracer: TraceLogger | None = None,
    event_bus: EventBus | None = None,
    plugin_registry=None,
    execution_backend=None,
    retrieval=None,
) -> AgentLoop:
    """Assemble an AgentLoop from a config dict and workspace.

    This is pure wiring — no side effects beyond object construction.
    Optional execution_backend lets callers inject A5 image-backed backends.
    Optional retrieval (src.tools.retrieve.TaskRetrieval) is required when retrieve_code is enabled.
    """
    llm_cfg = config["llm"]
    rt_cfg = config["runtime"]
    retry_cfg = config.get("retry", {})
    policy_cfg = config.get("policy", {})
    tools_cfg = config.get("tools", {})

    llm = LLMClient(
        base_url=llm_cfg["base_url"],
        api_key=llm_cfg["api_key"],
        model=llm_cfg["model"],
        temperature=llm_cfg.get("temperature", 0.0),
        max_tokens=llm_cfg.get("max_tokens", 4096),
        max_tool_argument_bytes=tools_cfg.get("max_argument_bytes", 65536),
        supports_streaming=llm_cfg.get("supports_streaming", True),
        max_connections=llm_cfg.get("max_connections", 10),
        max_keepalive_connections=llm_cfg.get("max_keepalive_connections", 5),
        trust_env=llm_cfg.get("trust_env", True),
        thinking=llm_cfg.get("thinking"),
    )

    sandbox = Sandbox(
        workspace_root=workspace,
        deny_absolute=policy_cfg.get("sandbox", {}).get("deny_absolute_paths", True),
        deny_parent_traversal=policy_cfg.get("sandbox", {}).get("deny_parent_traversal", True),
    )

    budget_cfg = rt_cfg.get("budget", {}) or {}
    model_ledger = None
    if any(
        budget_cfg.get(k) is not None
        for k in ("max_model_calls", "max_tokens", "max_cost_usd")
    ):
        model_ledger = ModelBudgetLedger(
            max_model_calls=budget_cfg.get("max_model_calls"),
            max_tokens=budget_cfg.get("max_tokens"),
            max_cost_usd=budget_cfg.get("max_cost_usd"),
            price_table=budget_cfg.get("price_table"),
        )

    budget = Budget(
        max_steps=rt_cfg["max_steps"],
        total_timeout_seconds=rt_cfg["total_timeout_seconds"],
        step_timeout_seconds=rt_cfg["step_timeout_seconds"],
        model_ledger=model_ledger,
    )

    retry = RetryConfig(
        max_retries=retry_cfg.get("max_retries", 2),
        backoff_base_seconds=retry_cfg.get("backoff_base_seconds", 1.0),
        backoff_factor=retry_cfg.get("backoff_factor", 2.0),
        retry_on=retry_cfg.get("retry_on", ["timeout", "model_error", "http", "network"]),
    )

    tools = ToolRegistry()
    enabled = tools_cfg.get("enabled", ["read_file", "write_file", "list_dir"])
    cmd_cfg = policy_cfg.get("command", {})
    exec_profile = cmd_cfg.get("execution_profile", "legacy_host")
    docker_backend = None
    if exec_profile == "docker_linux":
        # retrieve_code is the one host-side tool allowed here: it reads only the CR cache, never the workspace.
        if set(enabled) - {"read_file", "write_file", "list_dir", "edit_file", "search_replace", "run_command", "search_code",
                           "retrieve_code"}:
            raise ValueError("Docker profile rejects host-only/debug/subagent tools")
        if execution_backend is not None:
            docker_backend = execution_backend
        else:
            docker_backend = DockerExecutionBackend(
                image=cmd_cfg.get("docker_image") or DEFAULT_IMAGE,
                context=cmd_cfg.get("docker_context", "desktop-linux"),
                cpus=cmd_cfg.get("docker_cpus", 1.0),
                memory_bytes=cmd_cfg.get("docker_memory_bytes", 512*1024*1024),
                pids_limit=cmd_cfg.get("docker_pids_limit", 128),
                network=cmd_cfg.get("docker_network", "none"),
                tmpfs_bytes=cmd_cfg.get("docker_tmpfs_bytes", 16*1024*1024),
                workspace_bytes=cmd_cfg.get("docker_workspace_bytes", 64*1024*1024))
    def file_tool(tool):
        return ContainerFileTool(tool, docker_backend) if docker_backend else tool
    if "read_file" in enabled:
        tools.register(file_tool(ReadFileTool()))
    if "write_file" in enabled:
        tools.register(file_tool(WriteFileTool()))
    if "list_dir" in enabled:
        tools.register(file_tool(ListDirTool()))
    if "run_command" in enabled:
        cmd_cfg = policy_cfg.get("command", {})
        cmd_policy = CommandPolicy(
            allow_all=cmd_cfg.get("allow_all", True),
            allowed_prefixes=cmd_cfg.get("allowed_prefixes", []),
            blocked_patterns=cmd_cfg.get("blocked_patterns", []),
        )
        exec_profile = cmd_cfg.get("execution_profile", "legacy_host")
        quotas = OutputQuotas(
            memory_per_stream_bytes=cmd_cfg.get("memory_per_stream_bytes", 64 * 1024),
            artifact_per_tool_bytes=cmd_cfg.get("artifact_per_tool_bytes", 1 * 1024 * 1024),
            run_total_bytes=cmd_cfg.get("run_output_limit_bytes", 16 * 1024 * 1024),
        )
        backend = docker_backend or LocalExecutionBackend(profile=exec_profile)
        tools.register(
            RunCommandTool(
                command_policy=cmd_policy,
                timeout_seconds=cmd_cfg.get("timeout_seconds", 30),
                env_whitelist=cmd_cfg.get("env_whitelist", []),
                prefer_argv=cmd_cfg.get("prefer_argv", False),
                execution_backend=backend,
                output_quotas=quotas,
            )
        )
    if "search_replace" in enabled:
        tools.register(file_tool(SearchReplaceTool()))
    if "edit_file" in enabled:
        tools.register(file_tool(EditFileTool()))
    if "search_code" in enabled:
        tools.register(SearchCodeTool(execution_backend=docker_backend))
    if "retrieve_code" in enabled:
        if retrieval is None:
            raise ValueError("tools.enabled includes retrieve_code but build_agent got no retrieval service")
        from src.tools.retrieve import RetrieveCodeTool
        tools.register(RetrieveCodeTool(retrieval))

    subagent_cfg = rt_cfg.get("subagents", {})
    if subagent_cfg.get("enabled"):
        # HP-03: controlled profiles must not silently omit child model cost.
        # Without share_parent_budget, refuse enablement (no separate child ledger yet).
        if not subagent_cfg.get("share_parent_budget"):
            raise ValueError(
                "runtime.subagents.enabled=true requires share_parent_budget=true "
                "so child model calls share the parent ledger/deadline "
                "(HP-03 does not implement a separate child budget). "
                "Legacy independent Budget without shared ledger is no longer allowed "
                "on controlled wiring — see docs/upgrade/evidence/HP-03/LEGACY-MIGRATION.md"
            )
        if model_ledger is None:
            raise ValueError(
                "share_parent_budget=true requires runtime.budget max_model_calls "
                "and/or max_tokens (and price_table if max_cost_usd is set)"
            )
        if "subagent_task" not in enabled:
            raise ValueError(
                "runtime.subagents.enabled=true requires tools.enabled to include subagent_task"
            )
        from src.tools.subagent import SubagentTaskTool
        tools.register(
            SubagentTaskTool(
                llm=llm,
                output_dir=config.get("tracing", {}).get("output_dir", "traces"),
                max_depth=subagent_cfg.get("max_depth", 1),
                readonly=subagent_cfg.get("readonly", True),
                max_steps=subagent_cfg.get("max_steps", 3),
                total_timeout_seconds=rt_cfg.get("step_timeout_seconds", 60),
                step_timeout_seconds=rt_cfg.get("step_timeout_seconds", 60),
                file_size_limit_bytes=policy_cfg.get("file_size_limit_bytes"),
                tool_output_max_chars=policy_cfg.get("tool_output_max_chars"),
                read_file_max_lines=policy_cfg.get("read_file_max_lines"),
                model_ledger=model_ledger,
            )
        )

    # System prompt: load from file if path configured
    system_prompt = None
    prompt_path = rt_cfg.get("system_prompt_path")
    if prompt_path:
        resolved_prompt = Path(prompt_path)
        if not resolved_prompt.is_absolute():
            resolved_prompt = Path.cwd() / resolved_prompt
        if resolved_prompt.exists():
            system_prompt = resolved_prompt.read_text(encoding="utf-8").strip()
        else:
            raise FileNotFoundError(f"System prompt file not found: {resolved_prompt}")

    # Context policy: L0 limits are carried by ToolContext; L1 uses hooks.
    ctx_cfg = rt_cfg.get("context", {})
    context_policy = ContextPolicyConfig.from_runtime_config(ctx_cfg, policy_cfg)
    dispatcher = None
    if context_policy.dispatcher == "read_search":
        if docker_backend is None:
            raise ValueError("read_search dispatcher requires docker_linux execution profile")
        dispatcher = ReadSearchDispatcher(
            docker_backend,
            max_calls=context_policy.dispatcher_max_calls,
            max_file_bytes=policy_cfg.get("file_size_limit_bytes", 1_048_576),
            max_output_bytes=policy_cfg.get("tool_output_max_chars", 16_000),
        )
    hooks = build_context_hooks(context_policy, plugin_registry=plugin_registry,
                                dispatcher=dispatcher,
                                summary_provider=(LLMSummaryProvider(llm)
                                    if context_policy.summary_provider == 'llm' else None))

    runtime_mode = rt_cfg.get("mode", "react")
    planner = None
    planner_cfg = rt_cfg.get("planner", {})
    if runtime_mode == "plan_act" and planner_cfg.get("enabled"):
        planner = SimplePlanner()

    # EventBus: create if lifecycle_events is enabled in tracing config
    tracing_cfg = config.get("tracing", {})
    if event_bus is None:
        if tracing_cfg.get("lifecycle_events"):
            event_bus = EventBus(output_dir=tracing_cfg.get("output_dir", "traces"))

    # read_events tool (debug only — not in baseline/hardened)
    if "read_events" in enabled:
        from src.tools.events import ReadEventsTool
        read_events_tool = ReadEventsTool(event_bus=event_bus)
        tools.register(read_events_tool)

    agent = AgentLoop(
        llm=llm,
        tools=tools,
        sandbox=sandbox,
        budget=budget,
        retry_config=retry,
        tracer=tracer,
        enabled_tools=enabled,
        file_size_limit_bytes=policy_cfg.get("file_size_limit_bytes"),
        tool_output_max_chars=policy_cfg.get("tool_output_max_chars"),
        read_file_max_lines=policy_cfg.get("read_file_max_lines"),
        hooks=hooks,
        event_bus=event_bus,
        system_prompt=system_prompt,
        runtime_mode=runtime_mode,
        planner=planner,
        use_llm_stream=tracing_cfg.get("llm_stream_events", False),
        max_tool_argument_bytes=tools_cfg.get("max_argument_bytes", 65536),
        model_ledger=model_ledger,
        run_output_limit_bytes=policy_cfg.get("command", {}).get(
            "run_output_limit_bytes", 16 * 1024 * 1024
        ),
        estimated_input_tokens=budget_cfg.get("estimated_input_tokens", 512),
    )
    agent.execution_backend = docker_backend
    return agent
