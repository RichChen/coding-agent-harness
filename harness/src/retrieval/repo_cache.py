"""Bare git clones outside the repo; list trees and read blobs without checkout."""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CACHE_DIR = Path("D:/crq/cache/cr")


def cache_dir() -> Path:
    return Path(os.environ.get("CR_CACHE_DIR") or DEFAULT_CACHE_DIR)


def repo_slug(repo: str) -> str:
    return repo.replace("/", "__")


def repo_dir(repo: str, root: Path | None = None) -> Path:
    return (root or cache_dir()) / "repos" / f"{repo_slug(repo)}.git"


def _git(git_dir: Path, *args: str, input: bytes | None = None) -> bytes:
    proc = subprocess.run(
        ["git", f"--git-dir={git_dir}", *args],
        input=input,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed ({proc.returncode}): "
            f"{proc.stderr.decode('utf-8', 'replace').strip()}"
        )
    return proc.stdout


def ensure_clone(repo: str, url: str | None = None, root: Path | None = None) -> tuple[Path, bool]:
    """Full ``git clone --bare``. Returns (path, cloned_now)."""
    dst = repo_dir(repo, root)
    if dst.exists():
        return dst, False
    dst.parent.mkdir(parents=True, exist_ok=True)
    partial = dst.with_name(dst.name + ".partial")
    if partial.exists():
        shutil.rmtree(partial)
    src = url or f"https://github.com/{repo}.git"
    proc = subprocess.run(
        ["git", "clone", "--bare", "--quiet", src, str(partial)],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"clone {src} failed: {proc.stderr.decode('utf-8', 'replace').strip()}")
    partial.rename(dst)
    return dst, True


def resolve_commits(git_dir: Path, shas: list[str]) -> dict[str, bool]:
    """sha -> whether it resolves to a commit object in this clone."""
    if not shas:
        return {}
    payload = "".join(f"{s}^{{commit}}\n" for s in shas).encode()
    out = _git(git_dir, "cat-file", "--batch-check", input=payload).decode().splitlines()
    return {sha: not line.endswith(" missing") for sha, line in zip(shas, out)}


@dataclass(frozen=True)
class TreeEntry:
    path: str
    blob_sha: str
    mode: str


def list_files(git_dir: Path, commit: str) -> list[TreeEntry]:
    """Regular-file blobs at ``commit``. Symlinks and submodules are skipped."""
    out = _git(git_dir, "ls-tree", "-r", "-z", commit)
    entries = []
    for rec in out.split(b"\0"):
        if not rec:
            continue
        meta, path = rec.split(b"\t", 1)
        mode, otype, sha = meta.decode().split(" ")
        if otype != "blob" or mode == "120000":
            continue
        entries.append(TreeEntry(path.decode("utf-8", "surrogateescape"), sha, mode))
    return entries


class BlobReader:
    """Persistent ``git cat-file --batch`` process."""

    def __init__(self, git_dir: Path):
        self._proc = subprocess.Popen(
            ["git", f"--git-dir={git_dir}", "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )

    def read(self, sha: str) -> bytes:
        assert self._proc.stdin and self._proc.stdout
        self._proc.stdin.write(sha.encode() + b"\n")
        self._proc.stdin.flush()
        header = self._proc.stdout.readline().decode().strip()
        parts = header.split(" ")
        if len(parts) != 3:
            raise KeyError(f"cat-file: {header}")
        size = int(parts[2])
        data = self._proc.stdout.read(size)
        self._proc.stdout.read(1)
        return data

    def close(self) -> None:
        if self._proc.stdin:
            self._proc.stdin.close()
        self._proc.wait(timeout=30)

    def __enter__(self) -> "BlobReader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
