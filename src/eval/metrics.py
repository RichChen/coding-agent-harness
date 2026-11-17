"""评测指标：单条 TaskResult 与按配置聚合的 EvalMetrics。

English: per-task results + aggregate metrics for reports.

注意：`success` 表示 agent 是否正常结束；`expected_check` 表示是否通过数据集判定器，
二者在报表里会区分（通过才算「任务通过」的常见定义见 aggregate_metrics）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean, stdev

from src.eval.bootstrap import ConfidenceInterval, bootstrap_mean_ci, bootstrap_proportion_ci
from src.runtime.loop import FailureReason


@dataclass
class TaskResult:
    task_id: str
    success: bool
    failure_reason: FailureReason = FailureReason.NONE
    steps: int = 0
    elapsed_ms: float = 0.0
    tokens: int = 0
    tool_failures: int = 0
    policy_blocks: int = 0
    total_tool_invocations: int = 0
    final_answer: str | None = None
    expected_check: bool = False  # 是否通过 expected 判定（与 agent success 不同）
    expected_failures: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    trace_file: str = ""  # relative path to JSONL trace for this task
    intervention: dict | None = None


@dataclass
class EvalMetrics:
    config_name: str
    total_tasks: int = 0
    passed: int = 0
    failed: int = 0
    success_rate: float = 0.0
    avg_steps: float = 0.0
    avg_elapsed_ms: float = 0.0
    avg_tokens: float = 0.0
    total_tokens: int = 0
    est_cost: float | None = None
    tool_failure_rate: float = 0.0
    policy_block_count: int = 0
    timeout_count: int = 0
    tool_error_count: int = 0
    model_error_count: int = 0
    step_limit_count: int = 0
    results: list[TaskResult] = field(default_factory=list)

    @property
    def failure_breakdown(self) -> dict[str, int]:
        return {
            "timeout": self.timeout_count,
            "tool_error": self.tool_error_count,
            "policy_block": self.policy_block_count,
            "model_parse_error": self.model_error_count,
            "step_limit": self.step_limit_count,
        }


@dataclass
class MetricStats:
    mean: float = 0.0
    std: float = 0.0


def is_degenerate_run(run: EvalMetrics, *, step_floor: float = 1.5) -> bool:
    """整轮坍塌判定：几乎没推进就全挂，强烈暗示 provider 故障而非模型能力。"""
    if run.total_tasks == 0:
        return False
    near_zero_progress = run.avg_steps <= step_floor
    all_failed = run.success_rate == 0.0
    model_error_heavy = run.model_error_count >= max(1, run.total_tasks // 2)
    return all_failed and (near_zero_progress or model_error_heavy)


@dataclass
class RepeatedEvalMetrics:
    config_name: str
    runs: list[EvalMetrics] = field(default_factory=list)
    total_tasks: int = 0
    success_rate: MetricStats = field(default_factory=MetricStats)
    avg_steps: MetricStats = field(default_factory=MetricStats)
    avg_elapsed_ms: MetricStats = field(default_factory=MetricStats)
    avg_tokens: MetricStats = field(default_factory=MetricStats)
    tool_failure_rate: MetricStats = field(default_factory=MetricStats)
    policy_block_count: MetricStats = field(default_factory=MetricStats)
    success_rate_ci: ConfidenceInterval | None = None
    degenerate_run_indices: list[int] = field(default_factory=list)
    valid_run_count: int = 0

    @property
    def run_count(self) -> int:
        return len(self.runs)

    @property
    def passed(self) -> int:
        if not self.runs:
            return 0
        return self.runs[0].passed

    @property
    def failed(self) -> int:
        if not self.runs:
            return 0
        return self.runs[0].failed


def _stats(values: list[float]) -> MetricStats:
    if not values:
        return MetricStats()
    if len(values) == 1:
        return MetricStats(mean=values[0], std=0.0)
    return MetricStats(mean=mean(values), std=stdev(values))


def aggregate_metrics(
    config_name: str,
    results: list[TaskResult],
    *,
    cost_per_1k_tokens: float = 0.0,
) -> EvalMetrics:
    if not results:
        return EvalMetrics(config_name=config_name)

    total = len(results)
    passed = sum(1 for r in results if r.success and r.expected_check)
    steps = [r.steps for r in results]
    elapsed = [r.elapsed_ms for r in results]
    tokens = [r.tokens for r in results]
    tool_fails = [r.tool_failures for r in results]
    total_tool_calls = sum(r.total_tool_invocations for r in results)

    total_token_sum = sum(tokens)
    est_cost = None
    if cost_per_1k_tokens > 0:
        est_cost = total_token_sum / 1000.0 * cost_per_1k_tokens

    return EvalMetrics(
        config_name=config_name,
        total_tasks=total,
        passed=passed,
        failed=total - passed,
        success_rate=(passed / total * 100) if total > 0 else 0.0,
        avg_steps=sum(steps) / len(steps) if steps else 0.0,
        avg_elapsed_ms=sum(elapsed) / len(elapsed) if elapsed else 0.0,
        avg_tokens=sum(tokens) / len(tokens) if tokens else 0.0,
        total_tokens=total_token_sum,
        est_cost=est_cost,
        tool_failure_rate=(sum(tool_fails) / total_tool_calls * 100)
        if total_tool_calls > 0
        else 0.0,
        policy_block_count=sum(r.policy_blocks for r in results),
        timeout_count=sum(1 for r in results if r.failure_reason == FailureReason.TIMEOUT),
        tool_error_count=sum(
            1 for r in results if r.failure_reason == FailureReason.TOOL_ERROR
        ),
        model_error_count=sum(
            1 for r in results if r.failure_reason == FailureReason.MODEL_PARSE_ERROR
        ),
        step_limit_count=sum(
            1 for r in results if r.failure_reason == FailureReason.STEP_LIMIT
        ),
        results=results,
    )


def aggregate_repeated_metrics(
    config_name: str,
    runs: list[EvalMetrics],
    *,
    cost_per_1k_tokens: float = 0.0,
) -> RepeatedEvalMetrics:
    if not runs:
        return RepeatedEvalMetrics(config_name=config_name)

    degenerate_run_indices = [
        idx for idx, run in enumerate(runs, start=1) if is_degenerate_run(run)
    ]
    valid_runs = [run for run in runs if not is_degenerate_run(run)]
    valid_run_count = len(valid_runs)

    success_rates = [m.success_rate for m in valid_runs]
    ci = None
    if valid_run_count >= 2:
        ci = bootstrap_mean_ci(success_rates)
    elif valid_run_count == 1 and valid_runs[0].total_tasks > 0:
        ci = bootstrap_proportion_ci(valid_runs[0].passed, valid_runs[0].total_tasks)

    return RepeatedEvalMetrics(
        config_name=config_name,
        runs=runs,
        total_tasks=runs[0].total_tasks,
        success_rate=_stats(success_rates),
        avg_steps=_stats([m.avg_steps for m in valid_runs]),
        avg_elapsed_ms=_stats([m.avg_elapsed_ms for m in valid_runs]),
        avg_tokens=_stats([m.avg_tokens for m in valid_runs]),
        tool_failure_rate=_stats([m.tool_failure_rate for m in valid_runs]),
        policy_block_count=_stats([float(m.policy_block_count) for m in valid_runs]),
        success_rate_ci=ci,
        degenerate_run_indices=degenerate_run_indices,
        valid_run_count=valid_run_count,
    )
