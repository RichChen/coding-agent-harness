from scripts.analysis.showcase_cross_repo import as_markdown, summarize_manifest


def test_summary_counts_unique_repositories_separately():
    summary = summarize_manifest(
        {"tasks": [{"id": "a-1", "repo": "org/a"}, {"id": "a-2", "repo": "org/a"}, {"id": "b-1", "repo": "org/b"}]},
        "fixture.yaml",
    )
    assert summary["task_count"] == 3
    assert summary["unique_repo_count"] == 2
    assert summary["duplicate_repos"] == {"org/a": 2}
    assert "30 个不同仓库" not in as_markdown(summary)


def test_missing_repo_is_rejected():
    try:
        summarize_manifest({"tasks": [{"id": "bad"}]}, "fixture.yaml")
    except ValueError as exc:
        assert "repository" in str(exc)
    else:
        raise AssertionError("missing repository must be rejected")
