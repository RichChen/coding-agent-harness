from src.eval.unified import (
    GraderSpec,
    RunReceipt,
    RunSpec,
    TaskSpec,
    normalize_legacy_result,
)


def _receipt(task: TaskSpec, *, run_id: str, adapter: str, grader: str, raw: dict, status: str) -> RunReceipt:
    run = RunSpec(
        run_id=run_id,
        task=task,
        adapter=adapter,
        config_name="showcase",
        runner_version="showcase-fixture",
    )
    return RunReceipt(
        run=run,
        grader=GraderSpec(grader, "fixture-v1"),
        status=status,
        success=status == "pass",
        partial=status == "partial",
        raw_receipt=raw,
        provenance={"source": "showcase_fixture"},
    )


def test_three_evaluation_paths_share_an_envelope_without_losing_details():
    mini = _receipt(
        TaskSpec(task_id="mini-hello", kind="mini", grader_profile="builtin"),
        run_id="mini-hello/r1", adapter="mini", grader="builtin",
        raw={"expected_check": True, "workspace": "fixture"}, status="pass",
    )
    repo = _receipt(
        TaskSpec(
            task_id="repo-example-1",
            kind="repo_pytest",
            repo_id="example/project",
            base_revision="a" * 40,
            execution_profile="docker-linux",
            grader_profile="pytest",
            metadata={"image": "example@sha256:" + "b" * 64},
        ),
        run_id="repo-example-1/r1", adapter="repo_pytest", grader="repo.pytest",
        raw={"receipt": {"schema": "repo.pytest.receipt.v1", "exit_code": 1}}, status="partial",
    )
    swe = normalize_legacy_result(
        {"solved": True, "f2p_passed": 2, "p2p_passed": 1},
        task=TaskSpec(
            task_id="swe-example-1", kind="swebench", repo_id="example/project",
            execution_profile="docker-linux", grader_profile="image_grader",
        ),
        run_id="swe-example-1/r1", adapter="swebench", grader_kind="image_grader",
        provenance={"source": "swebench-fixture"},
    )

    receipts = [mini, repo, swe]
    assert {receipt.run.task.kind for receipt in receipts} == {"mini", "repo_pytest", "swebench"}
    assert [receipt.status for receipt in receipts] == ["pass", "partial", "pass"]
    assert repo.raw_receipt["receipt"]["schema"] == "repo.pytest.receipt.v1"
    assert swe.raw_receipt["f2p_passed"] == 2
    assert all(receipt.provenance for receipt in receipts)
    assert len({receipt.receipt_hash for receipt in receipts}) == 3
