from src.eval.unified import GraderSpec, ManifestEntry, MiniAdapter, TaskSpec
from src.llm.client import LLMResponse, ToolCall
from src.policy.budget import Budget
from src.policy.sandbox import Sandbox
from src.runtime.loop import AgentLoop
from src.tools.files import WriteFileTool
from src.tools.registry import ToolRegistry
from tests.fixtures.mock_llm import MockLLMClient


def test_mini_adapter_reuses_eval_runner_with_mock_llm(tmp_path):
    def factory(_config):
        tools = ToolRegistry()
        tools.register(WriteFileTool())
        return AgentLoop(
            MockLLMClient([
                LLMResponse(tool_calls=[ToolCall("1", "write_file", {
                    "path": "hello.txt", "content": "hello"
                })], usage={"total_tokens": 1}),
                LLMResponse(text="done", usage={"total_tokens": 1}),
            ]),
            tools, Sandbox(tmp_path), Budget(max_steps=4), announce_remaining_calls=False,
        )

    task = TaskSpec(
        task_id="mini-write",
        kind="mini",
        description="write hello",
        metadata={
            "expected": {"type": "file_contains", "path": "hello.txt", "keywords": ["hello"]},
        },
    )
    entry = ManifestEntry(task=task, adapter="mini", grader=GraderSpec("builtin", "v1"))
    receipt = MiniAdapter(factory, dataset_path="datasets/mini_tasks.json").execute(
        entry, run_id="mini-write/r1"
    )
    assert receipt.status == "pass"
    assert receipt.raw_receipt["expected_check"] is True
    assert receipt.provenance["execution"] == "eval_runner"
    assert receipt.run.intervention_policy == "none"
    assert receipt.provenance["intervention"]["trigger_count"] == 0
