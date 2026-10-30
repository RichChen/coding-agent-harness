"""Pydantic 配置 schema — YAML 加载后校验。

English: typed config models with validation. Unknown fields are forbidden
so typos in YAML keys surface as clear errors.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str
    api_key: str = ""
    model: str = "deepseek-v4-pro"
    temperature: float = 0.0
    max_tokens: int = 4096
    supports_streaming: bool = True
    max_connections: int = 10
    max_keepalive_connections: int = 5
    # When True (default), httpx honors HTTP(S)_PROXY / ALL_PROXY for real providers.
    # Loopback tests should set False so local fixtures do not require SOCKS deps.
    trust_env: bool = True
    # Explicit non-thinking mode for DeepSeek tool calls. None leaves provider
    # defaults unchanged; thinking replay is not supported by this runtime yet.
    thinking: Literal["disabled"] | None = None


class RetryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stop_on_unknown_usage: bool = False

    max_retries: int = 2
    backoff_base_seconds: float = 1.0
    backoff_factor: float = 2.0
    retry_on: list[str] = Field(
        default_factory=lambda: ["timeout", "model_error", "http", "network"]
    )


class RuntimeContextConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: str | None = Field(default=None, pattern="^(none|masking|summary|mask-then-summary)$")
    max_tokens: int | None = Field(default=None, gt=0)
    micro_compact: bool = False
    keep_recent_tool_results: int = Field(default=3, ge=0)
    compact_token_threshold: int | None = Field(default=None, ge=0)
    summary_max_calls: int = Field(default=1, ge=0)
    summary_provider: Literal['fixture', 'llm'] = 'fixture'
    summary_failure: Literal['masking', 'reject'] = 'masking'
    summary_max_tokens: int = Field(default=131072, gt=0)
    summary_max_output_tokens: int = Field(default=512, gt=0)
    summary_max_output_bytes: int = Field(default=8192, gt=0)
    summary_input_max_bytes: int = Field(default=65536, gt=0)
    summary_timeout_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)
    summary_time_budget_seconds: float = Field(default=60, gt=0, allow_inf_nan=False)
    summary_price_version: str = 'unspecified'
    # A selector only. External IDs require an explicit host-supplied registry.
    plugin: str = Field(default="noop", pattern="^[a-z][a-z0-9_-]{0,63}$")
    plugin_candidates: list[dict] = Field(default_factory=list)
    max_injection_bytes: int = Field(default=4096, ge=0, le=65536)
    dispatcher: str = Field(default="disabled", pattern="^(disabled|read_search)$")
    dispatcher_max_calls: int = Field(default=0, ge=0, le=256)


class RuntimePlannerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False


class RuntimeSubagentsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    max_depth: int = 1
    readonly: bool = True
    max_steps: int = 3
    # When True, subagent shares parent ModelBudgetLedger + DeadlineClock.
    # Controlled profiles refuse enablement unless share_parent_budget is True
    # (HP-03 does not implement a separate child ledger).
    share_parent_budget: bool = False


class RuntimeBudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_model_calls: int | None = None
    max_tokens: int | None = None
    max_cost_usd: float | None = None
    # Required when max_cost_usd is set: {"prompt_per_1k": float, "completion_per_1k": float}
    price_table: dict[str, float] | None = None
    estimated_input_tokens: int = 512


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = "react"
    max_steps: int = 30
    step_timeout_seconds: int = 120
    total_timeout_seconds: int = 600
    system_prompt_path: str | None = None
    context: RuntimeContextConfig = Field(default_factory=RuntimeContextConfig)
    planner: RuntimePlannerConfig = Field(default_factory=RuntimePlannerConfig)
    subagents: RuntimeSubagentsConfig = Field(default_factory=RuntimeSubagentsConfig)
    budget: RuntimeBudgetConfig = Field(default_factory=RuntimeBudgetConfig)


class SandboxConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deny_absolute_paths: bool = True
    deny_parent_traversal: bool = True
    workspace_root: str | None = None


class CommandConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_all: bool = True
    allowed_prefixes: list[str] = Field(default_factory=list)
    blocked_patterns: list[str] = Field(default_factory=list)
    prefer_argv: bool = False
    env_whitelist: list[str] = Field(default_factory=list)
    timeout_seconds: int = 30
    # controlled: Linux process-group cleanup; Windows pre-spawn blocked.
    # legacy_host: explicit host exec without tree reclaim (migration required).
    execution_profile: str = "legacy_host"
    run_output_limit_bytes: int = 16 * 1024 * 1024
    memory_per_stream_bytes: int = 64 * 1024
    artifact_per_tool_bytes: int = 1 * 1024 * 1024
    # H3 Docker Linux profile. The image must be pinned by digest at runtime.
    docker_image: str = "python@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9"
    docker_context: str = "desktop-linux"
    docker_cpus: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    docker_memory_bytes: int = Field(default=512 * 1024 * 1024, gt=0)
    docker_pids_limit: int = Field(default=128, gt=0)
    docker_network: str = Field(default="none", pattern="^(none|bridge)$")
    docker_tmpfs_bytes: int = Field(default=16 * 1024 * 1024, gt=0)
    docker_workspace_bytes: int = Field(default=64 * 1024 * 1024, gt=0)


class PolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    command: CommandConfig = Field(default_factory=CommandConfig)
    file_size_limit_bytes: int | None = None
    tool_output_max_chars: int | None = None
    read_file_max_lines: int | None = None


class ToolsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: list[str] = Field(default_factory=lambda: ["read_file", "write_file", "list_dir"])
    max_argument_bytes: int = Field(default=65536, ge=1)


class TracingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: str = "jsonl"
    output_dir: str = "traces"
    lifecycle_events: bool = False
    llm_stream_events: bool = False


class WorkspaceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    profile: Literal["sphinx8721"] = "sphinx8721"
    retrieval_mode: Literal["bm25"] = "bm25"
    finalization_reserve_seconds: int = Field(default=182, ge=182)
    capture_timeout_seconds: int = Field(default=20, ge=1, le=20)
    startup_timeout_seconds: int = Field(default=60, ge=1, le=60)
    final_artifact_reserve_bytes: int = Field(default=16 * 1024 * 1024, ge=16 * 1024 * 1024)
    final_output_reserve_bytes: int = Field(default=4 * 1024 * 1024, ge=4 * 1024 * 1024)


class HarnessConfig(BaseModel):
    """Top-level harness configuration with validation."""

    model_config = ConfigDict(extra="forbid")

    llm: LLMConfig
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    tracing: TracingConfig = Field(default_factory=TracingConfig)
    workspace: WorkspaceConfig | None = None
