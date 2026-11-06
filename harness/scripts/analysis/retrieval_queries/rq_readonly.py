"""Read-only access to the frozen CR-1 caches (chunk index and jina vectors).

Both sqlite files are opened with ``mode=ro``. A cache miss raises instead of
building and writing the missing record, so the frozen caches cannot change.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np

from src.retrieval import repo_cache
from src.retrieval.chunking import ChunkStore
from src.retrieval.tokenize import Vocab


class CacheMiss(RuntimeError):
    pass


def _connect_ro(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


class ReadOnlyChunkStore(ChunkStore):
    def __init__(self, repo: str, root: Path | None = None):
        self.repo = repo
        self.path = (root or repo_cache.cache_dir()) / "index" / f"{repo_cache.repo_slug(repo)}.sqlite"
        self.db = _connect_ro(self.path)
        rows = self.db.execute("SELECT token FROM vocab ORDER BY id").fetchall()
        self.vocab = Vocab([r[0] for r in rows])
        self._persisted_vocab = len(self.vocab)

    def put(self, blob_sha, record) -> None:
        raise CacheMiss(f"{self.repo}: blob {blob_sha} not in the frozen chunk cache")

    def put_tree(self, commit, entries) -> None:
        raise CacheMiss(f"{self.repo}: tree {commit} not in the frozen chunk cache")

    def flush(self) -> None:
        if len(self.vocab) != self._persisted_vocab:
            raise CacheMiss(f"{self.repo}: vocabulary grew; the frozen cache is incomplete")

    def close(self) -> None:
        self.flush()
        self.db.close()


class ReadOnlyVectors:
    """All vectors of one frozen vector file, loaded with one sequential scan."""

    def __init__(self, path: Path):
        self.path = path
        db = _connect_ro(path)
        try:
            self.meta = dict(db.execute("SELECT k, v FROM meta").fetchall())
            shas, blobs = [], []
            for s, v in db.execute("SELECT text_sha, v FROM vec"):
                shas.append(s)
                blobs.append(v)
        finally:
            db.close()
        self.index = {s: i for i, s in enumerate(shas)}
        self.mem = np.frombuffer(b"".join(blobs), np.float16).reshape(len(shas), -1)

    def __contains__(self, sha: str) -> bool:
        return sha in self.index

    def get_matrix(self, shas: list[str]) -> np.ndarray:
        idx = [self.index.get(s) for s in shas]
        n_missing = sum(i is None for i in idx)
        if n_missing:
            raise CacheMiss(f"{n_missing} vectors missing in {self.path.name}")
        return self.mem[idx].astype(np.float32)


def file_fingerprint(paths: list[Path]) -> dict[str, dict]:
    """Size and mtime of each file, recorded before and after a run."""
    out = {}
    for p in paths:
        st = p.stat()
        out[p.name] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns}
    return out
