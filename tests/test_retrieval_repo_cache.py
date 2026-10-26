import subprocess
from pathlib import Path

import pytest

from src.retrieval import repo_cache


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "core.autocrlf=false", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


def make_repo(root: Path, commits: list[dict[str, str | None]]) -> tuple[Path, list[str]]:
    """Create a work repo; each dict maps path -> content (None deletes)."""
    work = root / "work"
    work.mkdir()
    _git(work, "init", "-q")
    shas = []
    for i, files in enumerate(commits):
        for rel, content in files.items():
            p = work / rel
            if content is None:
                p.unlink()
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content.encode())
        _git(work, "add", "-A")
        _git(work, "commit", "-q", "-m", f"c{i}")
        shas.append(_git(work, "rev-parse", "HEAD"))
    return work, shas


@pytest.fixture
def cloned(tmp_path):
    work, shas = make_repo(
        tmp_path,
        [
            {"pkg/a.py": "def f():\n    return 1\n", "README": "hi\n"},
            {"pkg/b.py": "X = 2\n", "pkg/a.py": "def f():\n    return 2\n"},
        ],
    )
    root = tmp_path / "cache"
    git_dir, cloned_now = repo_cache.ensure_clone("o/r", url=str(work), root=root)
    return git_dir, shas, root, cloned_now


def test_clone_is_bare_and_idempotent(cloned):
    git_dir, shas, root, cloned_now = cloned
    assert cloned_now and git_dir == root / "repos" / "o__r.git"
    assert (git_dir / "HEAD").exists() and not (git_dir / "pkg").exists()
    again, now = repo_cache.ensure_clone("o/r", url="unused", root=root)
    assert again == git_dir and not now


def test_resolve_commits(cloned):
    git_dir, shas, _, _ = cloned
    res = repo_cache.resolve_commits(git_dir, shas + ["0" * 40])
    assert res == {shas[0]: True, shas[1]: True, "0" * 40: False}


def test_list_files_per_commit(cloned):
    git_dir, shas, _, _ = cloned
    first = {e.path for e in repo_cache.list_files(git_dir, shas[0])}
    second = {e.path for e in repo_cache.list_files(git_dir, shas[1])}
    assert first == {"pkg/a.py", "README"}
    assert second == {"pkg/a.py", "pkg/b.py", "README"}


def test_blob_reader_reads_versions(cloned):
    git_dir, shas, _, _ = cloned
    e0 = {e.path: e for e in repo_cache.list_files(git_dir, shas[0])}
    e1 = {e.path: e for e in repo_cache.list_files(git_dir, shas[1])}
    with repo_cache.BlobReader(git_dir) as r:
        assert r.read(e0["pkg/a.py"].blob_sha) == b"def f():\n    return 1\n"
        assert r.read(e1["pkg/a.py"].blob_sha) == b"def f():\n    return 2\n"
        assert r.read(e1["README"].blob_sha) == b"hi\n"
    assert e0["README"].blob_sha == e1["README"].blob_sha


def test_cache_dir_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CR_CACHE_DIR", str(tmp_path))
    assert repo_cache.cache_dir() == tmp_path
    monkeypatch.delenv("CR_CACHE_DIR")
    assert repo_cache.cache_dir() == repo_cache.DEFAULT_CACHE_DIR
