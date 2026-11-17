"""EvalRunner：按数据集批量跑配置，并交给评测判定器打分。

English: batch dataset runner — prepares workspace, runs AgentLoop, applies expected.* checks.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml

from src.eval.metrics import (
    EvalMetrics,
    RepeatedEvalMetrics,
    TaskResult,
    aggregate_metrics,
    aggregate_repeated_metrics,
)
from src.runtime.loop import AgentLoop, AgentRunResult, FailureReason

logger = logging.getLogger(__name__)


class EvalRunner:
    """对单个或多个 agent 配置跑完整数据集。"""

    def __init__(self, agent_factory, dataset_path: str | Path, max_concurrency: int = 1):
        """
        Args:
            agent_factory: callable(config_dict) -> AgentLoop，由脚本注入。
            dataset_path: JSON 数据集路径。
            max_concurrency: 并发任务上限（默认 1，便于排障）。
        """
        self.agent_factory = agent_factory
        self.dataset_path = Path(dataset_path)
        self.max_concurrency = max_concurrency

    def run_all(
        self,
        configs: dict[str, dict],
        judge_fn=None,
        runs: int = 1,
        cost_per_1k_tokens: float = 0.0,
    ) -> list[EvalMetrics] | list[RepeatedEvalMetrics]:
        """对所有任务 × 所有配置运行；每个配置返回一份 EvalMetrics。

        Args:
            configs: 例如 {"baseline": {...}, "hardened": {...}}。
            judge_fn: 可选自定义判定函数 (task, agent_result) -> bool。
        """
        tasks = self._load_dataset()
        all_metrics: list[EvalMetrics] | list[RepeatedEvalMetrics] = []
        runs = max(1, runs)

        for config_name, config in configs.items():
            run_metrics: list[EvalMetrics] = []
            for run_index in range(runs):
                logger.info(
                    f"Running config '{config_name}' on {len(tasks)} tasks "
                    f"(run {run_index + 1}/{runs})"
                )
                results = asyncio.run(
                    self._run_config(config_name, config, tasks, judge_fn)
                )
                run_metrics.append(
                    aggregate_metrics(
                        config_name, results, cost_per_1k_tokens=cost_per_1k_tokens
                    )
                )

            if runs == 1:
                all_metrics.append(run_metrics[0])
            else:
                all_metrics.append(
                    aggregate_repeated_metrics(
                        config_name, run_metrics, cost_per_1k_tokens=cost_per_1k_tokens
                    )
                )

        return all_metrics

    async def _run_config(
        self,
        config_name: str,
        config: dict,
        tasks: list[dict],
        judge_fn=None,
    ) -> list[TaskResult]:
        sem = asyncio.Semaphore(self.max_concurrency)

        async def run_one(task: dict) -> TaskResult:
            async with sem:
                return await self._run_task(config_name, config, task, judge_fn)

        return await asyncio.gather(*[run_one(t) for t in tasks])

    async def _run_task(
        self,
        config_name: str,
        config: dict,
        task: dict,
        judge_fn=None,
    ) -> TaskResult:
        task_id = task["id"]
        description = task["description"]
        expected = task.get("expected", {})
        initial_files = task.get("initial_files", {})

        agent: AgentLoop | None = None
        try:
            agent = self.agent_factory(config)
            if getattr(agent, "execution_backend", None) is not None:
                raise ValueError("Docker runs require the H3 independent evaluator; legacy host judges are not valid for container snapshots")
            tracer = agent.tracer

            # Set up workspace with initial files
            workspace = agent.sandbox.workspace_root
            fixture_dir = task.get("fixture_dir")
            if fixture_dir:
                self._copy_fixture_dir(workspace, fixture_dir)
            self._write_initial_files(workspace, initial_files)

            if tracer:
                tracer.open_run(task_id, config_name=config_name)

            start = time.monotonic()
            result: AgentRunResult = await agent.run(description, task_id=task_id)
            elapsed = (time.monotonic() - start) * 1000

            if fixture_dir:
                self._restore_protected_paths(workspace, fixture_dir, task)

            # 判定：自定义 judge_fn 优先，否则按 expected.type 内置裁判
            expected_check = False
            expected_failures: list[str] = []
            if judge_fn:
                expected_check = judge_fn(task, result)
            else:
                expected_check, expected_failures = self._explain_expected(
                    workspace, result, expected
                )

            trace_file = ""
            if tracer:
                from src.tracing.models import TraceRun
                trace = TraceRun(
                    task_id=task_id,
                    config_name=config_name,
                    steps=result.steps,
                    success=result.success,
                    final_answer=result.final_answer,
                    failure_reason=result.failure_reason.value if result.failure_reason else None,
                    total_elapsed_ms=elapsed,
                    total_tokens=result.total_tokens,
                    tool_failures=result.tool_failures,
                    policy_blocks=result.policy_blocks,
                    metadata={
                        "expected_check": expected_check,
                        "expected_failures": expected_failures,
                        "plan": result.plan,
                        "context_events": result.context_events,
                        "summary": result.summary,
                        "intervention": result.intervention,
                        "intervention_events": result.intervention_events,
                        "subagent_traces": result.subagent_traces,
                    },
                )
                tracer.log_run(trace)
                tracer.close()
                trace_file = str(tracer.path) if tracer.path else ""

            return TaskResult(
                task_id=task_id,
                success=result.success,
                failure_reason=result.failure_reason,
                steps=len(result.steps),
                elapsed_ms=elapsed,
                tokens=result.total_tokens,
                tool_failures=result.tool_failures,
                policy_blocks=result.policy_blocks,
                total_tool_invocations=result.total_tool_invocations,
                final_answer=result.final_answer,
                expected_check=expected_check,
                expected_failures=expected_failures,
                tags=task.get("tags", []),
                trace_file=trace_file,
                intervention=result.intervention,
            )
        finally:
            # Lifecycle: release owned HTTP clients after success, error, or cancel.
            # Borrowed/shared clients are left open (LLMClient.aclose respects ownership).
            # Subagents share the parent llm and must not aclose it; only this finally runs
            # after the parent task completes.
            if agent is not None:
                await self._release_agent_llm(agent)

    @staticmethod
    async def _release_agent_llm(agent: AgentLoop) -> None:
        llm = getattr(agent, "llm", None)
        if llm is None:
            return
        aclose = getattr(llm, "aclose", None)
        if not callable(aclose):
            return
        try:
            await aclose()
        except Exception:
            logger.debug("error closing agent llm after eval task", exc_info=True)

    def _load_dataset(self) -> list[dict]:
        with open(self.dataset_path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "tasks" in data:
            return data["tasks"]
        if isinstance(data, list):
            return data
        raise ValueError("Dataset must be a list or {tasks: [...]}")

    @staticmethod
    def _write_initial_files(workspace: Path, files: dict, prefix: str = "") -> None:
        """Recursively write initial file tree from a nested dict.

        Example:
            {"workspace/": {"hello.py": "print('hi')", "sub/": {"a.txt": "data"}}}
        """
        for name, content in files.items():
            if name.endswith("/"):
                subdir = workspace / prefix / name.rstrip("/")
                subdir.mkdir(parents=True, exist_ok=True)
                if isinstance(content, dict):
                    EvalRunner._write_initial_files(
                        workspace, content, prefix=f"{prefix}{name}"
                    )
            else:
                filepath = workspace / prefix / name
                filepath.parent.mkdir(parents=True, exist_ok=True)
                filepath.write_text(content, encoding="utf-8")

    @staticmethod
    def _copy_fixture_dir(workspace: Path, fixture_dir: str | Path) -> None:
        fixture = Path(fixture_dir)
        if not fixture.is_absolute():
            fixture = Path.cwd() / fixture
        if not fixture.exists() or not fixture.is_dir():
            raise FileNotFoundError(f"Fixture directory not found: {fixture}")

        for item in fixture.iterdir():
            if item.name == ".oracle":
                continue
            dest = workspace / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dest)

    @staticmethod
    def _restore_protected_paths(workspace: Path, fixture_dir, task: dict) -> None:
        protected = task.get("protected_paths") or []
        if not protected:
            return
        fixture = Path(fixture_dir)
        if not fixture.is_absolute():
            fixture = Path.cwd() / fixture
        for rel in protected:
            src = fixture / rel
            if not src.exists():
                continue
            dst = workspace / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    @staticmethod
    def _check_expected(
        workspace: Path,
        result: AgentRunResult,
        expected: dict,
    ) -> bool:
        ok, _ = EvalRunner._explain_expected(workspace, result, expected)
        return ok

    @staticmethod
    def _explain_expected(
        workspace: Path,
        result: AgentRunResult,
        expected: dict,
    ) -> tuple[bool, list[str]]:
        expected_type = expected.get("type")
        if expected_type == "file_contains":
            return EvalRunner._explain_file_contains(workspace, expected)
        if expected_type == "file_not_contains":
            return EvalRunner._explain_file_not_contains(workspace, expected)
        if expected_type == "command_output":
            return EvalRunner._explain_command_output(workspace, expected)
        if expected_type == "command_success":
            return EvalRunner._explain_command_success(workspace, expected)
        if expected_type == "json_equals":
            return EvalRunner._explain_json_equals(workspace, expected)
        if expected_type == "yaml_contains":
            return EvalRunner._explain_yaml_contains(workspace, expected)
        if expected_type == "regex_match":
            return EvalRunner._explain_regex_match(workspace, expected)
        if expected_type == "pytest_passes":
            command = expected.get("command", "python -m pytest -q")
            return EvalRunner._explain_command_success(
                workspace, {**expected, "command": command}
            )
        if expected_type == "file_exists":
            return EvalRunner._explain_file_exists(workspace, expected)
        if expected_type == "keywords_in_answer":
            return EvalRunner._explain_keywords(result.final_answer, expected)
        if expected_type == "python_ast_valid":
            return EvalRunner._explain_python_ast_valid(workspace, expected)
        if expected_type == "python_defines":
            return EvalRunner._explain_python_defines(workspace, expected)
        if expected_type == "multi_assert":
            failures: list[str] = []
            for assertion in expected.get("assertions", []):
                ok, reasons = EvalRunner._explain_expected(workspace, result, assertion)
                if not ok:
                    failures.extend(reasons)
            return len(failures) == 0, failures
        return False, [f"unknown expected type: {expected_type!r}"]

    @staticmethod
    def _explain_file_contains(workspace: Path, expected: dict) -> tuple[bool, list[str]]:
        path = workspace / expected["path"]
        if not path.exists():
            return False, [f"file_contains: file not found: {expected['path']}"]
        content = path.read_text(encoding="utf-8")
        missing = [kw for kw in expected.get("keywords", []) if kw not in content]
        if missing:
            return False, [
                f"file_contains: missing keyword {kw!r} in {expected['path']}"
                for kw in missing
            ]
        return True, []

    @staticmethod
    def _explain_file_not_contains(workspace: Path, expected: dict) -> tuple[bool, list[str]]:
        path = workspace / expected["path"]
        if not path.exists():
            return False, [f"file_not_contains: file not found: {expected['path']}"]
        content = path.read_text(encoding="utf-8")
        present = [kw for kw in expected.get("keywords", []) if kw in content]
        if present:
            return False, [
                f"file_not_contains: forbidden keyword {kw!r} found in {expected['path']}"
                for kw in present
            ]
        return True, []

    @staticmethod
    def _explain_command_output(workspace: Path, expected: dict) -> tuple[bool, list[str]]:
        cmd = expected["command"]
        shell_result = EvalRunner._run_shell_command(workspace, expected)
        if shell_result is None:
            return False, [f"command_output failed: {cmd} (execution error)"]
        require_exit_zero = expected.get("require_exit_zero", True)
        if require_exit_zero and shell_result.returncode != 0:
            return False, [
                f"command_output failed: {cmd} (exit={shell_result.returncode})"
            ]
        output = shell_result.stdout + shell_result.stderr
        missing = [kw for kw in expected.get("keywords", []) if kw not in output]
        if missing:
            return False, [
                f"command_output: missing keyword {kw!r} in output of {cmd!r}"
                for kw in missing
            ]
        return True, []

    @staticmethod
    def _explain_command_success(workspace: Path, expected: dict) -> tuple[bool, list[str]]:
        cmd = expected["command"]
        shell_result = EvalRunner._run_shell_command(workspace, expected)
        if shell_result is None:
            return False, [f"command_success failed: {cmd} (execution error)"]
        if shell_result.returncode != 0:
            return False, [f"command_success failed: {cmd} (exit={shell_result.returncode})"]
        output = shell_result.stdout + shell_result.stderr
        missing = [kw for kw in expected.get("keywords", []) if kw not in output]
        if missing:
            return False, [
                f"command_success: missing keyword {kw!r} in output of {cmd!r}"
                for kw in missing
            ]
        return True, []

    @staticmethod
    def _explain_json_equals(workspace: Path, expected: dict) -> tuple[bool, list[str]]:
        path = workspace / expected["path"]
        if not path.exists():
            return False, [f"json_equals: file not found: {expected['path']}"]
        try:
            actual = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            return False, [f"json_equals: invalid JSON in {expected['path']}: {e}"]
        expected_value = expected.get("value")
        if actual != expected_value:
            return False, [f"json_equals: value mismatch in {expected['path']}"]
        return True, []

    @staticmethod
    def _explain_yaml_contains(workspace: Path, expected: dict) -> tuple[bool, list[str]]:
        path = workspace / expected["path"]
        if not path.exists():
            return False, [f"yaml_contains: file not found: {expected['path']}"]
        try:
            content = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as e:
            return False, [f"yaml_contains: invalid YAML in {expected['path']}: {e}"]
        text = yaml.dump(content, allow_unicode=True, default_flow_style=False)
        missing = [kw for kw in expected.get("keywords", []) if kw not in text]
        if missing:
            return False, [
                f"yaml_contains: missing keyword {kw!r} in {expected['path']}"
                for kw in missing
            ]
        return True, []

    @staticmethod
    def _explain_regex_match(workspace: Path, expected: dict) -> tuple[bool, list[str]]:
        path = workspace / expected["path"]
        if not path.exists():
            return False, [f"regex_match: file not found: {expected['path']}"]
        try:
            content = path.read_text(encoding="utf-8")
            flags = re.MULTILINE
            if expected.get("dotall"):
                flags |= re.DOTALL
            if re.search(expected["pattern"], content, flags) is None:
                return False, [
                    f"regex_match: pattern {expected['pattern']!r} not found in {expected['path']}"
                ]
        except Exception as e:
            return False, [f"regex_match failed: {expected['path']}: {e}"]
        return True, []

    @staticmethod
    def _explain_file_exists(workspace: Path, expected: dict) -> tuple[bool, list[str]]:
        path = workspace / expected["path"]
        if not path.exists():
            return False, [f"file_exists: file not found: {expected['path']}"]
        min_size = expected.get("min_size_bytes")
        if min_size is not None and path.stat().st_size < min_size:
            return False, [
                f"file_exists: {expected['path']} size {path.stat().st_size} < {min_size}"
            ]
        return True, []

    @staticmethod
    def _explain_keywords(final_answer: str | None, expected: dict) -> tuple[bool, list[str]]:
        if not final_answer:
            return False, ["keywords_in_answer: empty final answer"]
        missing = [
            kw for kw in expected.get("keywords", [])
            if kw.lower() not in final_answer.lower()
        ]
        if missing:
            return False, [
                f"keywords_in_answer: missing keyword {kw!r}" for kw in missing
            ]
        return True, []

    @staticmethod
    def _explain_python_ast_valid(workspace: Path, expected: dict) -> tuple[bool, list[str]]:
        path = workspace / expected["path"]
        if not path.exists():
            return False, [f"python_ast_valid: file not found: {expected['path']}"]
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as e:
            return False, [
                f"python_ast_valid: syntax error in {expected['path']} at line {e.lineno}: {e.msg}"
            ]
        except Exception as e:
            return False, [f"python_ast_valid: failed to parse {expected['path']}: {e}"]
        return True, []

    @staticmethod
    def _explain_python_defines(workspace: Path, expected: dict) -> tuple[bool, list[str]]:
        path = workspace / expected["path"]
        if not path.exists():
            return False, [f"python_defines: file not found: {expected['path']}"]
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as e:
            return False, [
                f"python_defines: syntax error in {expected['path']} at line {e.lineno}: {e.msg}"
            ]
        except Exception as e:
            return False, [f"python_defines: failed to parse {expected['path']}: {e}"]

        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)

        missing = [
            name for name in expected.get("names", []) if name not in names
        ]
        if missing:
            return False, [
                f"python_defines: missing definition(s) {missing!r} in {expected['path']}"
            ]
        return True, []

    @staticmethod
    def _check_file_contains(workspace: Path, expected: dict) -> bool:
        path = workspace / expected["path"]
        if not path.exists():
            return False
        content = path.read_text(encoding="utf-8")
        keywords = expected.get("keywords", [])
        return all(kw in content for kw in keywords)

    @staticmethod
    def _check_file_not_contains(workspace: Path, expected: dict) -> bool:
        path = workspace / expected["path"]
        if not path.exists():
            return False
        content = path.read_text(encoding="utf-8")
        keywords = expected.get("keywords", [])
        return all(kw not in content for kw in keywords)

    @staticmethod
    def _run_shell_command(workspace: Path, expected: dict) -> subprocess.CompletedProcess | None:
        try:
            return subprocess.run(
                expected["command"],
                shell=True,
                capture_output=True,
                text=True,
                cwd=str(workspace),
                timeout=expected.get("timeout", 30),
            )
        except Exception:
            return None

    @staticmethod
    def _check_command_output(workspace: Path, expected: dict) -> bool:
        result = EvalRunner._run_shell_command(workspace, expected)
        if result is None:
            return False
        require_exit_zero = expected.get("require_exit_zero", True)
        if require_exit_zero and result.returncode != 0:
            return False
        output = result.stdout + result.stderr
        keywords = expected.get("keywords", [])
        return all(kw in output for kw in keywords)

    @staticmethod
    def _check_command_success(workspace: Path, expected: dict) -> bool:
        result = EvalRunner._run_shell_command(workspace, expected)
        if result is None:
            return False
        if result.returncode != 0:
            return False
        output = result.stdout + result.stderr
        keywords = expected.get("keywords", [])
        return all(kw in output for kw in keywords)

    @staticmethod
    def _check_json_equals(workspace: Path, expected: dict) -> bool:
        path = workspace / expected["path"]
        if not path.exists():
            return False
        try:
            actual = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return False
        return actual == expected.get("value")

    @staticmethod
    def _check_yaml_contains(workspace: Path, expected: dict) -> bool:
        path = workspace / expected["path"]
        if not path.exists():
            return False
        try:
            content = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            return False
        keywords = expected.get("keywords", [])
        text = yaml.dump(content, allow_unicode=True, default_flow_style=False)
        return all(kw in text for kw in keywords)

    @staticmethod
    def _check_regex_match(workspace: Path, expected: dict) -> bool:
        path = workspace / expected["path"]
        if not path.exists():
            return False
        try:
            content = path.read_text(encoding="utf-8")
            flags = re.MULTILINE
            if expected.get("dotall"):
                flags |= re.DOTALL
            return re.search(expected["pattern"], content, flags) is not None
        except Exception:
            return False

    @staticmethod
    def _check_file_exists(workspace: Path, expected: dict) -> bool:
        path = workspace / expected["path"]
        if not path.exists():
            return False
        min_size = expected.get("min_size_bytes")
        if min_size is not None and path.stat().st_size < min_size:
            return False
        return True

    @staticmethod
    def _check_keywords(final_answer: str | None, expected: dict) -> bool:
        if not final_answer:
            return False
        keywords = expected.get("keywords", [])
        return all(kw.lower() in final_answer.lower() for kw in keywords)
