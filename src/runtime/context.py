"""Context policy wiring for L0/L1 context management.

L0 is enforced by existing ToolContext output limits, configured through
policy.tool_output_max_chars/read_file_max_lines. L1 is the micro-compact hook.
L2 optionally borrows the host LLM through an explicit summary adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.runtime.compact import MicroCompactHooks
from src.runtime.hooks import LoopHooks
from src.runtime.window import WindowPolicy, WindowPolicyHooks, DEFAULT_WINDOW
from src.policy.plugin import FixturePolicyPlugin, NoOpPolicyPlugin


@dataclass(frozen=True)
class ContextPolicyConfig:
    strategy: str = "none"
    max_tokens: int | None = None
    micro_compact: bool = False
    keep_recent_tool_results: int = 3
    compact_token_threshold: int | None = None
    tool_output_max_chars: int | None = None
    read_file_max_lines: int | None = None
    summary_max_calls: int = 1
    summary_provider: str = 'fixture'
    summary_failure: str = 'masking'
    summary_max_tokens: int = 131072
    summary_max_output_tokens: int = 512
    summary_max_output_bytes: int = 8192
    summary_input_max_bytes: int = 65536
    summary_timeout_seconds: float = 30.0
    summary_time_budget_seconds: float = 60.0
    summary_price_version: str = 'unspecified'
    plugin: str = "noop"
    plugin_candidates: tuple = ()
    max_injection_bytes: int = 4096
    explicit_window: bool = False
    dispatcher: str = "disabled"
    dispatcher_max_calls: int = 0
    protection_rules: str = "v1"
    admission_estimate: str = "utf8_bytes"

    @classmethod
    def from_runtime_config(
        cls,
        runtime_context: dict[str, Any],
        policy_config: dict[str, Any] | None = None,
    ) -> "ContextPolicyConfig":
        policy_config = policy_config or {}
        return cls(
            strategy=runtime_context.get("strategy") or ("masking" if runtime_context.get("micro_compact", False) else "none"),
            max_tokens=runtime_context.get("max_tokens"),
            micro_compact=runtime_context.get("micro_compact", False),
            keep_recent_tool_results=runtime_context.get("keep_recent_tool_results", 3),
            compact_token_threshold=runtime_context.get("compact_token_threshold"),
            tool_output_max_chars=policy_config.get("tool_output_max_chars"),
            read_file_max_lines=policy_config.get("read_file_max_lines"),
            summary_max_calls=runtime_context.get("summary_max_calls", 1),
            **{name: runtime_context.get(name, cls.__dataclass_fields__[name].default)
               for name in cls.__dataclass_fields__ if name.startswith('summary_') and name != 'summary_max_calls'},
            plugin=runtime_context.get("plugin", "noop"),
            plugin_candidates=tuple(runtime_context.get('plugin_candidates', [])),
            max_injection_bytes=runtime_context.get('max_injection_bytes',4096),
            explicit_window=runtime_context.get('strategy') is not None or runtime_context.get('max_tokens') is not None or runtime_context.get('plugin','noop')!='noop'
                or runtime_context.get('admission_estimate', 'utf8_bytes') != 'utf8_bytes',
            dispatcher=runtime_context.get('dispatcher','disabled'),
            dispatcher_max_calls=runtime_context.get('dispatcher_max_calls',0),
            protection_rules=runtime_context.get('protection_rules', 'v1'),
            admission_estimate=runtime_context.get('admission_estimate', 'utf8_bytes'),
        )

    def to_trace_metadata(self) -> dict[str, Any]:
        metadata = {
            "l0_tool_output_max_chars": self.tool_output_max_chars,
            "l0_read_file_max_lines": self.read_file_max_lines,
            "l1_micro_compact": self.micro_compact,
            "l1_keep_recent_tool_results": self.keep_recent_tool_results,
            "l1_compact_token_threshold": self.compact_token_threshold,
            "l2_llm_summary": self.summary_provider == 'llm' and self.strategy in ('summary', 'mask-then-summary'),
            "summary_provider": self.summary_provider,
            "h4_strategy": self.strategy,
            "h4_max_tokens": self.max_tokens,
            "h4_summary_max_calls": self.summary_max_calls,
            "h4_plugin": self.plugin,
            "h5_dispatcher": self.dispatcher,
            "h5_dispatcher_max_calls": self.dispatcher_max_calls,
        }
        if self.protection_rules != "v1":
            metadata["protection_rules"] = self.protection_rules
        if self.admission_estimate != "utf8_bytes":
            metadata["admission_estimate"] = self.admission_estimate
        return metadata


def build_context_hooks(config: ContextPolicyConfig, *, plugin_registry=None, dispatcher=None, summary_provider=None) -> LoopHooks | None:
    """Build context hooks from config.

    Returns None when no hook is required, preserving baseline behaviour.
    """
    if config.plugin not in ('noop', 'fixture'):
        if plugin_registry is None:
            raise ValueError('external plugin requires explicit host registration')
        if config.plugin_candidates:
            raise ValueError('external plugin cannot use fixture candidates')
        plugin = plugin_registry.create(config.plugin)
    else:
        plugin = FixturePolicyPlugin(config.plugin_candidates) if config.plugin == 'fixture' else NoOpPolicyPlugin()
    if config.dispatcher != 'disabled' and dispatcher is None:
        raise ValueError('dispatcher requires host-supplied implementation')
    if config.dispatcher == 'read_search' and config.plugin in ('noop','fixture'):
        raise ValueError('read_search dispatcher requires an external plugin')
    if config.strategy == "none" and not config.micro_compact and not config.explicit_window and config.plugin == 'noop' and config.dispatcher == 'disabled':
        return None
    # Keep the old micro_compact object as a compatibility path for existing
    # callers/tests; new explicit H4 strategies use the unified policy hook.
    if config.micro_compact and config.strategy == "masking" and not config.explicit_window and config.plugin == 'noop':
        return MicroCompactHooks(
            keep_recent=config.keep_recent_tool_results,
            token_threshold=config.compact_token_threshold,
            protection_rules=config.protection_rules,
        )
    return WindowPolicyHooks(WindowPolicy(
        strategy=config.strategy,
        max_tokens=config.max_tokens or DEFAULT_WINDOW,
        keep_recent=config.keep_recent_tool_results,
        compact_token_threshold=config.compact_token_threshold,
        max_summary_calls=config.summary_max_calls,
        **{name: getattr(config, name) for name in config.__dataclass_fields__
           if name.startswith('summary_') and name != 'summary_max_calls'},
        max_injection_bytes=config.max_injection_bytes,
        dispatcher=config.dispatcher,
        dispatcher_max_calls=config.dispatcher_max_calls,
    ), plugin, dispatcher=dispatcher, summary_provider=summary_provider, protection_rules=config.protection_rules,
       admission=config.admission_estimate)
