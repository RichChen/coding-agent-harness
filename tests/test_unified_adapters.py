from src.eval.unified import (
    AdapterError,
    AdapterRegistry,
    ManifestEntry,
    MockAdapter,
    RepoPytestAdapter,
    SwebenchAdapter,
)
import asyncio


def _entry(kind: str) -> ManifestEntry:
    return ManifestEntry.from_mapping({
        "task": {
            "id": f"{kind}-1",
            "kind": kind,
            "repo_id": "org/repo" if kind != "mini" else None,
            "execution_profile": "docker-linux" if kind != "mini" else "host",
            "grader_profile": "image_grader" if kind == "swebench" else "builtin",
        },
        "adapter": kind,
        "grader": {"kind": "image_grader" if kind == "swebench" else kind, "version": "v1"},
    })


def test_manifest_and_mock_adapter_keep_grader_and_raw_result():
    entry = _entry("repo_pytest")
    registry = AdapterRegistry({"repo_pytest": MockAdapter("repo_pytest")})
    receipt = registry.execute(
        entry, run_id="repo-1/r1", raw_result={"status": "partial", "passed": 2}
    )
    assert receipt.status == "partial"
    assert receipt.grader.kind == "repo_pytest"
    assert receipt.raw_receipt["passed"] == 2
    assert receipt.provenance["execution"] == "mock"


def test_all_three_manifest_kinds_validate():
    for kind in ("mini", "repo_pytest", "swebench"):
        entry = _entry(kind)
        assert entry.task.kind == kind
        assert entry.adapter == kind


def test_registry_does_not_implicitly_select_real_runner():
    entry = _entry("mini")
    registry = AdapterRegistry()
    try:
        registry.execute(entry, run_id="mini-1/r1", raw_result={"status": "pass"})
    except AdapterError as exc:
        assert "not registered" in str(exc)
    else:
        raise AssertionError("missing adapters must fail explicitly")


def test_mismatched_task_and_adapter_rejected():
    try:
        ManifestEntry.from_mapping({
            "task": {"id": "mini-1", "kind": "mini"},
            "adapter": "swebench",
            "grader": "image_grader",
        })
    except AdapterError:
        pass
    else:
        raise AssertionError("manifest must not silently mix adapter semantics")


class _RepoGrader:
    async def evaluate(self, base, candidate, spec):
        assert base == {"src.py": b"base"}
        assert candidate == {"src.py": b"candidate"}
        assert spec == "explicit-spec"
        return {"status": "pass", "solved": True, "cases": []}


class _SweGrader:
    async def evaluate(self, **kwargs):
        assert kwargs["image"] == "sha256:fixture"
        assert kwargs["f2p"] == ["test_a"]
        assert kwargs["p2p"] == ["test_b"]
        return {"status": "partial", "solved": False, "f2p_passed": 1}


def test_real_adapters_call_only_explicit_case_loader():
    from src.eval.unified import RunReceipt

    repo = RepoPytestAdapter(
        lambda entry: {"base": {"src.py": b"base"}, "candidate": {"src.py": b"candidate"},
                       "spec": "explicit-spec"},
        grader=_RepoGrader(),
    )
    receipt = repo.execute(_entry("repo_pytest"), run_id="repo-1/r1")
    assert isinstance(receipt, RunReceipt)
    assert receipt.status == "pass"
    assert receipt.provenance["execution"] == "repository_grader"

    swe = SwebenchAdapter(
        lambda entry: {"image": "sha256:fixture", "test_overlay": {},
                       "f2p": ["test_a"], "p2p": ["test_b"]},
        grader=_SweGrader(),
    )
    receipt = swe.execute(_entry("swebench"), run_id="swe-1/r1")
    assert receipt.status == "partial"
    assert receipt.provenance["execution"] == "image_grader"


def test_registry_async_front_door_preserves_manifest_order():
    async def run():
        entries = [_entry("repo_pytest"), _entry("swebench")]
        registry = AdapterRegistry({
            "repo_pytest": RepoPytestAdapter(
                lambda entry: {"base": {"src.py": b"base"},
                               "candidate": {"src.py": b"candidate"}, "spec": "explicit-spec"},
                grader=_RepoGrader(),
            ),
            "swebench": SwebenchAdapter(
                lambda entry: {"image": "sha256:fixture", "test_overlay": {},
                               "f2p": ["test_a"], "p2p": ["test_b"]},
                grader=_SweGrader(),
            ),
        })
        return await registry.execute_many_async(entries)

    receipts = asyncio.run(run())
    assert [receipt.run.adapter for receipt in receipts] == ["repo_pytest", "swebench"]
    assert [receipt.run.run_id for receipt in receipts] == ["repo_pytest-1/r1", "swebench-1/r2"]
