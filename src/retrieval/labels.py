"""Gold labels. The only module in src/retrieval that reads the gold diff."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from src.retrieval import dataset, repo_cache
from src.retrieval.chunking import (
    ChunkStore,
    chunk_id,
    ensure_blobs,
    is_corpus_path,
    is_test_path,
    locate_line,
    tree_entries,
)

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class Hunk:
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    lines: list[str] = field(default_factory=list)


@dataclass
class FileDiff:
    old_path: str | None
    new_path: str | None
    hunks: list[Hunk] = field(default_factory=list)


def _strip_prefix(p: str) -> str | None:
    p = p.split("\t", 1)[0].strip()
    if p == "/dev/null":
        return None
    if p.startswith(("a/", "b/")):
        return p[2:]
    return p


def parse_diff(text: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    lines = text.split("\n")
    i = 0
    cur: FileDiff | None = None
    while i < len(lines):
        line = lines[i]
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/(.*) b/(.*)$", line)
            cur = FileDiff(m.group(1) if m else None, m.group(2) if m else None)
            files.append(cur)
        elif cur is not None and line.startswith("new file mode"):
            cur.old_path = None
        elif cur is not None and line.startswith("deleted file mode"):
            cur.new_path = None
        elif cur is not None and line.startswith("rename from "):
            cur.old_path = line[len("rename from "):]
        elif cur is not None and line.startswith("rename to "):
            cur.new_path = line[len("rename to "):]
        elif cur is not None and line.startswith("--- "):
            cur.old_path = _strip_prefix(line[4:])
        elif cur is not None and line.startswith("+++ "):
            cur.new_path = _strip_prefix(line[4:])
        elif cur is not None and line.startswith("@@"):
            m = _HUNK.match(line)
            if m:
                def count(g):
                    return 1 if g is None else int(g)

                h = Hunk(int(m.group(1)), count(m.group(2)), int(m.group(3)), count(m.group(4)))
                old_left, new_left = h.old_len, h.new_len
                i += 1
                while i < len(lines) and (old_left > 0 or new_left > 0):
                    body = lines[i]
                    tag = body[:1]
                    if tag == "\\":
                        pass
                    elif tag == "-":
                        old_left -= 1
                        h.lines.append(body)
                    elif tag == "+":
                        new_left -= 1
                        h.lines.append(body)
                    else:
                        old_left -= 1
                        new_left -= 1
                        h.lines.append(body if tag == " " else " " + body)
                    i += 1
                while i < len(lines) and lines[i].startswith("\\"):
                    i += 1
                cur.hunks.append(h)
                continue
        i += 1
    return files


def changed_old_lines(hunk: Hunk, per_hunk: bool = False) -> list[int]:
    """Old-side lines touched by a hunk.

    Each contiguous run of -/+ lines contributes its deleted lines, or, for a
    pure insertion, the old line just before the insertion point (0 when the
    insertion is at the top of the file). With ``per_hunk=True`` a hunk that
    deletes anything contributes only its deleted lines.
    """
    cur = hunk.old_start if hunk.old_len > 0 else hunk.old_start + 1
    runs: list[tuple[list[int], int | None]] = []
    dels: list[int] = []
    adds = False
    anchor = 0
    for line in hunk.lines:
        tag = line[:1]
        if tag in "-+" and not dels and not adds:
            anchor = cur - 1
        if tag == "-":
            dels.append(cur)
            cur += 1
        elif tag == "+":
            adds = True
        else:
            if dels or adds:
                runs.append((dels, None if dels else anchor))
            dels, adds = [], False
            cur += 1
    if dels or adds:
        runs.append((dels, None if dels else anchor))
    all_dels = [ln for d, _ in runs for ln in d]
    if per_hunk and all_dels:
        return all_dels
    out: list[int] = []
    for d, a in runs:
        out.extend(d if d else [a])
    return out


@dataclass(frozen=True)
class LabelView:
    instance_id: str
    repo: str
    gold_files: tuple[str, ...]
    gold_chunks: tuple[str, ...]
    gold_chunks_per_hunk: tuple[str, ...]
    module_chunks: int
    excluded: tuple[tuple[str, int], ...]

    @property
    def unretrievable(self) -> bool:
        return not self.gold_files

    def excluded_counts(self) -> dict[str, int]:
        return dict(self.excluded)


def build_label_view(instance_id: str, repo: str, diff_text: str, base_files: dict[str, str], get_record) -> LabelView:
    """``base_files``: path -> blob sha for every regular file at base_commit.
    ``get_record``: blob sha -> chunking.BlobRecord."""
    excluded: Counter = Counter()
    gold_files: list[str] = []
    chunks: dict[str, bool] = {}
    chunks_ph: dict[str, bool] = {}
    for fd in parse_diff(diff_text):
        path = fd.old_path
        if path is None:
            excluded["new_file"] += 1
            continue
        if not path.endswith(".py"):
            excluded["non_py"] += 1
            continue
        if is_test_path(path):
            excluded["test"] += 1
            continue
        if path not in base_files:
            excluded["missing_at_base"] += 1
            continue
        if path not in gold_files:
            gold_files.append(path)
        rec = get_record(base_files[path])
        for target, per_hunk in ((chunks, False), (chunks_ph, True)):
            for h in fd.hunks:
                for ln in changed_old_lines(h, per_hunk=per_hunk):
                    span = rec.spans[locate_line(rec.spans, ln)]
                    target[chunk_id(path, span.symbol, span.start_line)] = span.kind == "module"
    return LabelView(
        instance_id,
        repo,
        tuple(sorted(gold_files)),
        tuple(sorted(chunks)),
        tuple(sorted(chunks_ph)),
        sum(chunks.values()),
        tuple(sorted(excluded.items())),
    )


def load_label_views(queries, parquet: Path = dataset.PARQUET_PATH, root: Path | None = None) -> dict[str, LabelView]:
    """LabelView for each QueryView in ``queries`` (only repo/base_commit are used)."""
    diffs = dataset.load_gold_diffs(parquet)
    by_repo: dict[str, list] = {}
    for q in queries:
        by_repo.setdefault(q.repo, []).append(q)
    out: dict[str, LabelView] = {}
    for repo, qs in sorted(by_repo.items()):
        git_dir = repo_cache.repo_dir(repo, root)
        store = ChunkStore(repo, root)
        try:
            for q in qs:
                entries = tree_entries(store, git_dir, q.base_commit)
                base_files = {e.path: e.blob_sha for e in entries}
                needed = [
                    base_files[fd.old_path]
                    for fd in parse_diff(diffs[q.instance_id])
                    if fd.old_path in base_files and is_corpus_path(fd.old_path)
                ]
                ensure_blobs(store, git_dir, needed)
                out[q.instance_id] = build_label_view(
                    q.instance_id, repo, diffs[q.instance_id], base_files, store.get
                )
        finally:
            store.close()
    return out
