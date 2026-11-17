from src.eval.unified import (
    GraderSpec,
    RunReceipt,
    RunSpec,
    TaskSpec,
    UnifiedContractError,
    normalize_legacy_result,
)


def test_mini_contract_round_trip_and_hash():
    task = TaskSpec.from_mapping({"id": "mini-1", "description": "write a file", "kind": "mini"})
    run = RunSpec(run_id="mini-1-r1", task=task, adapter="mini", runner_version="unified-test")
    receipt = RunReceipt(run=run, grader=GraderSpec("builtin", "v1"), status="pass",
                         raw_receipt={"expected_check": True}, provenance={"source": "fixture"})
    payload = receipt.to_dict()
    assert payload["schema"] == "eval.unified.v1"
    assert payload["run"]["task"]["task_id"] == "mini-1"
    assert payload["receipt_hash"] == receipt.receipt_hash


def test_repo_and_swebench_records_keep_adapter_specific_payload():
    task = TaskSpec(task_id="repo-1", kind="repo_pytest", repo_id="org/repo",
                    base_revision="abc", execution_profile="docker-linux",
                    grader_profile="pytest", metadata={"image": "repo@sha256:" + "a" * 64})
    repo = normalize_legacy_result(
        {"status": "partial", "passed": 2, "collected": 3, "receipt": {"schema": "repo.pytest.receipt.v1"}},
        task=task, run_id="repo-1-r1", adapter="repo_pytest", grader_kind="repo_pytest",
        provenance={"source_file": "runs/example/grader-result.json"},
    )
    assert repo.status == "partial"
    assert repo.raw_receipt["receipt"]["schema"] == "repo.pytest.receipt.v1"
    assert repo.provenance["source_file"]

    swe_task = TaskSpec(task_id="swe-1", kind="swebench", repo_id="org/repo",
                        execution_profile="docker-linux", grader_profile="image_grader")
    swe = normalize_legacy_result(
        {"solved": True, "f2p_passed": 2, "p2p_passed": 3}, task=swe_task,
        run_id="swe-1-r1", adapter="swebench", grader_kind="image_grader",
    )
    assert swe.status == "pass"
    assert swe.grader.kind == "image_grader"


def test_intervention_provenance_is_preserved_in_run_and_receipt():
    task = TaskSpec(task_id="mini-e", kind="mini")
    receipt = normalize_legacy_result(
        {
            "status": "pass",
            "intervention": {
                "schema": "intervention_run.v1",
                "policy": "showcase_trajectory",
                "policy_version": "1",
                "config_hash": "abc123",
                "trigger_count": 1,
                "prompt_injection_count": 1,
                "resumed": True,
                "events": [{"trigger": "candidate_no_edit_after_read"}],
            },
        },
        task=task,
        run_id="mini-e/r1",
        adapter="mini",
        grader_kind="builtin",
    )
    assert receipt.run.intervention_policy == "showcase_trajectory"
    assert receipt.run.intervention_policy_version == "1"
    assert receipt.run.intervention_config_hash == "abc123"
    assert receipt.provenance["intervention"]["trigger_count"] == 1


def test_invalid_kind_is_rejected():
    try:
        TaskSpec(task_id="x", kind="unknown")
    except UnifiedContractError:
        pass
    else:
        raise AssertionError("unknown task kinds must not silently enter the shared report")
