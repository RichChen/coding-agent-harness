"""Corpus definition, AST chunking with line-window fallback, and the
blob-keyed chunk/token cache."""
from __future__ import annotations

import ast
import pickle
import sqlite3
import warnings
import zlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import numpy as np

from src.retrieval import repo_cache
from src.retrieval.tokenize import TOKENIZER_VERSION, Vocab, count_tokens

CHUNKER_VERSION = "1"
CACHE_VERSION = f"chunk{CHUNKER_VERSION}-tok{TOKENIZER_VERSION}"
WINDOW_LINES = 60
WINDOW_STEP = 40
MODULE_SYMBOL = "<module>"

_TEST_DIRS = {"tests", "test", "testing"}


def is_test_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if any(seg in _TEST_DIRS for seg in parts[:-1]):
        return True
    name = parts[-1] if parts else ""
    return (
        (name.startswith("test_") and name.endswith(".py"))
        or name.endswith("_test.py")
        or name == "conftest.py"
    )


def is_corpus_path(path: str) -> bool:
    return path.endswith(".py") and not is_test_path(path)


def chunk_header(path: str, symbol: str) -> str:
    return f"# path: {path}  symbol: {symbol}"


def decode_source(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    if text.startswith("\ufeff"):
        text = text[1:]
    return text.replace("\r\n", "\n")


def split_lines(source: str) -> list[str]:
    lines = source.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


@dataclass(frozen=True)
class ChunkSpan:
    symbol: str
    kind: str  # function | class | module | window
    start_line: int
    end_line: int


@dataclass(frozen=True)
class Chunk:
    repo: str
    commit: str
    path: str
    symbol: str
    kind: str
    start_line: int
    end_line: int
    blob_sha: str
    text: str

    @property
    def chunk_id(self) -> str:
        return chunk_id(self.path, self.symbol, self.start_line)


def chunk_id(path: str, symbol: str, start_line: int) -> str:
    return f"{path}::{symbol}@{start_line}"


def _node_start(node: ast.AST) -> int:
    decos = getattr(node, "decorator_list", None) or []
    return min([node.lineno] + [d.lineno for d in decos])


def _window_spans(n_lines: int) -> list[ChunkSpan]:
    spans = []
    start = 1
    while True:
        end = min(start + WINDOW_LINES - 1, max(n_lines, 1))
        spans.append(ChunkSpan(f"<window {start}-{end}>", "window", start, end))
        if end >= n_lines:
            break
        start += WINDOW_STEP
    return spans


def _text_of(lines: list[str], start: int, end: int, holes: list[tuple[int, int]]) -> str:
    keep = []
    for ln in range(start, end + 1):
        if any(a <= ln <= b for a, b in holes):
            continue
        keep.append(lines[ln - 1] if ln - 1 < len(lines) else "")
    return "\n".join(keep)


def chunk_source(source: str) -> tuple[list[ChunkSpan], list[str], bool]:
    """Return (spans, body texts, parse_ok). The module chunk is always first."""
    lines = split_lines(source)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        spans = _window_spans(len(lines))
        return spans, [_text_of(lines, s.start_line, s.end_line, []) for s in spans], False

    spans: list[ChunkSpan] = []
    texts: list[str] = []

    def visit_class(node: ast.ClassDef, prefix: str) -> tuple[int, int]:
        qual = prefix + node.name
        start, end = _node_start(node), node.end_lineno or node.lineno
        holes = []
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                rng = (_node_start(child), child.end_lineno or child.lineno)
                spans.append(ChunkSpan(f"{qual}.{child.name}", "function", *rng))
                texts.append(_text_of(lines, rng[0], rng[1], []))
                holes.append(rng)
            elif isinstance(child, ast.ClassDef):
                holes.append(visit_class(child, qual + "."))
        spans.append(ChunkSpan(qual, "class", start, end))
        texts.append(_text_of(lines, start, end, holes))
        return start, end

    top_holes = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            rng = (_node_start(node), node.end_lineno or node.lineno)
            spans.append(ChunkSpan(node.name, "function", *rng))
            texts.append(_text_of(lines, rng[0], rng[1], []))
            top_holes.append(rng)
        elif isinstance(node, ast.ClassDef):
            top_holes.append(visit_class(node, ""))

    n = max(len(lines), 1)
    module = ChunkSpan(MODULE_SYMBOL, "module", 1, n)
    return [module] + spans, [_text_of(lines, 1, n, top_holes)] + texts, True


def locate_line(spans: list[ChunkSpan], line: int) -> int:
    """Index of the innermost function/class span containing ``line``; the
    module span otherwise. For window-chunked files, the first window
    containing the line."""
    if spans and spans[0].kind == "window":
        for i, s in enumerate(spans):
            if s.start_line <= line <= s.end_line:
                return i
        return 0 if line < 1 else len(spans) - 1
    best, best_size = 0, None
    for i, s in enumerate(spans):
        if s.kind == "module" or not (s.start_line <= line <= s.end_line):
            continue
        size = s.end_line - s.start_line
        if best_size is None or size < best_size or (size == best_size and s.start_line > spans[best].start_line):
            best, best_size = i, size
    return best


# ---------------------------------------------------------------- cache


@dataclass
class BlobRecord:
    parse_ok: bool
    n_lines: int
    spans: list[ChunkSpan]
    texts: list[str]
    tok_ids: list[np.ndarray]
    tok_cnts: list[np.ndarray]
    file_ids: np.ndarray
    file_cnts: np.ndarray


def _counter_arrays(counter, vocab: Vocab) -> tuple[np.ndarray, np.ndarray]:
    if not counter:
        return np.zeros(0, np.int32), np.zeros(0, np.int32)
    ids = np.fromiter((vocab.add(t) for t in counter), np.int32, len(counter))
    cnts = np.fromiter(counter.values(), np.int32, len(counter))
    return ids, cnts


def build_blob_record(data: bytes, vocab: Vocab) -> BlobRecord:
    source = decode_source(data)
    spans, texts, ok = chunk_source(source)
    tok_ids, tok_cnts = [], []
    for text in texts:
        ids, cnts = _counter_arrays(count_tokens(text), vocab)
        tok_ids.append(ids)
        tok_cnts.append(cnts)
    file_ids, file_cnts = _counter_arrays(count_tokens(source), vocab)
    return BlobRecord(ok, len(split_lines(source)), spans, texts, tok_ids, tok_cnts, file_ids, file_cnts)


class ChunkStore:
    """Per-repo sqlite cache keyed by (blob_sha, CACHE_VERSION)."""

    def __init__(self, repo: str, root: Path | None = None):
        self.repo = repo
        base = (root or repo_cache.cache_dir()) / "index"
        base.mkdir(parents=True, exist_ok=True)
        self.path = base / f"{repo_cache.repo_slug(repo)}.sqlite"
        self.db = sqlite3.connect(self.path)
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS vocab (id INTEGER PRIMARY KEY, token TEXT UNIQUE);
            CREATE TABLE IF NOT EXISTS blobs (
                blob_sha TEXT, version TEXT, data BLOB, PRIMARY KEY (blob_sha, version));
            CREATE TABLE IF NOT EXISTS trees (commit_sha TEXT PRIMARY KEY, data BLOB);
            """
        )
        rows = self.db.execute("SELECT token FROM vocab ORDER BY id").fetchall()
        self.vocab = Vocab([r[0] for r in rows])
        self._persisted_vocab = len(self.vocab)

    def has(self, blob_sha: str) -> bool:
        return self.db.execute(
            "SELECT 1 FROM blobs WHERE blob_sha=? AND version=?", (blob_sha, CACHE_VERSION)
        ).fetchone() is not None

    def get(self, blob_sha: str) -> BlobRecord | None:
        row = self.db.execute(
            "SELECT data FROM blobs WHERE blob_sha=? AND version=?", (blob_sha, CACHE_VERSION)
        ).fetchone()
        return pickle.loads(zlib.decompress(row[0])) if row else None

    def put(self, blob_sha: str, record: BlobRecord) -> None:
        data = zlib.compress(pickle.dumps(record, protocol=pickle.HIGHEST_PROTOCOL), 6)
        self.db.execute(
            "INSERT OR REPLACE INTO blobs VALUES (?, ?, ?)", (blob_sha, CACHE_VERSION, data)
        )

    def get_tree(self, commit: str) -> list[repo_cache.TreeEntry] | None:
        row = self.db.execute("SELECT data FROM trees WHERE commit_sha=?", (commit,)).fetchone()
        return pickle.loads(zlib.decompress(row[0])) if row else None

    def put_tree(self, commit: str, entries: list[repo_cache.TreeEntry]) -> None:
        data = zlib.compress(pickle.dumps(entries, protocol=pickle.HIGHEST_PROTOCOL), 6)
        self.db.execute("INSERT OR REPLACE INTO trees VALUES (?, ?)", (commit, data))

    def flush(self) -> None:
        new = self.vocab.tokens[self._persisted_vocab:]
        if new:
            self.db.executemany(
                "INSERT INTO vocab (id, token) VALUES (?, ?)",
                [(self._persisted_vocab + i, t) for i, t in enumerate(new)],
            )
            self._persisted_vocab = len(self.vocab)
        self.db.commit()

    def close(self) -> None:
        self.flush()
        self.db.close()


def tree_entries(store: ChunkStore, git_dir: Path, commit: str) -> list[repo_cache.TreeEntry]:
    """All regular files at ``commit`` (cached)."""
    entries = store.get_tree(commit)
    if entries is None:
        entries = repo_cache.list_files(git_dir, commit)
        store.put_tree(commit, entries)
    return entries


def ensure_blobs(store: ChunkStore, git_dir: Path, blob_shas: list[str]) -> tuple[int, int]:
    """Build missing records. Returns (built, reused)."""
    missing = [s for s in dict.fromkeys(blob_shas) if not store.has(s)]
    if missing:
        with repo_cache.BlobReader(git_dir) as reader:
            for sha in missing:
                store.put(sha, build_blob_record(reader.read(sha), store.vocab))
        store.flush()
    return len(missing), len(set(blob_shas)) - len(missing)


# ---------------------------------------------------------------- snapshot


@dataclass
class Snapshot:
    """Corpus of one (repo, commit), ready for indexing."""

    repo: str
    commit: str
    vocab: Vocab
    paths: list[str]
    blob_shas: list[str]
    records: list[BlobRecord]
    chunk_file: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))
    chunk_local: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))
    chunk_spans: list[ChunkSpan] = field(default_factory=list)

    def chunk_ids(self) -> list[str]:
        return [
            chunk_id(self.paths[f], s.symbol, s.start_line)
            for f, s in zip(self.chunk_file.tolist(), self.chunk_spans)
        ]

    def chunk(self, i: int) -> Chunk:
        f = int(self.chunk_file[i])
        span = self.chunk_spans[i]
        body = self.records[f].texts[int(self.chunk_local[i])]
        return Chunk(
            self.repo, self.commit, self.paths[f], span.symbol, span.kind,
            span.start_line, span.end_line, self.blob_shas[f],
            chunk_header(self.paths[f], span.symbol) + "\n" + body,
        )

    def _triples(self, header_ids, bodies) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        docs, terms, tfs = [], [], []
        for d, ((h_ids, h_cnts), (b_ids, b_cnts)) in enumerate(zip(header_ids, bodies)):
            ids = np.concatenate([h_ids, b_ids])
            docs.append(np.full(len(ids), d, np.int64))
            terms.append(ids)
            tfs.append(np.concatenate([h_cnts, b_cnts]))
        if not docs:
            z = np.zeros(0, np.int64)
            return z, z, z
        return np.concatenate(docs), np.concatenate(terms).astype(np.int64), np.concatenate(tfs).astype(np.int64)

    def chunk_doc_triples(self):
        """(doc_idx, term_id, tf) for chunk documents = header line + body."""
        heads, bodies = [], []
        for f, rec in enumerate(self.records):
            for k, span in enumerate(rec.spans):
                heads.append(_counter_arrays(count_tokens(chunk_header(self.paths[f], span.symbol)), self.vocab))
                bodies.append((rec.tok_ids[k], rec.tok_cnts[k]))
        return self._triples(heads, bodies)

    def file_doc_triples(self):
        """(doc_idx, term_id, tf) for whole-file documents = path header + file."""
        heads = [_counter_arrays(count_tokens(f"# path: {p}"), self.vocab) for p in self.paths]
        bodies = [(r.file_ids, r.file_cnts) for r in self.records]
        return self._triples(heads, bodies)


def load_snapshot(store: ChunkStore, git_dir: Path, commit: str) -> Snapshot:
    entries = sorted(
        (e for e in tree_entries(store, git_dir, commit) if is_corpus_path(e.path)),
        key=lambda e: e.path,
    )
    ensure_blobs(store, git_dir, [e.blob_sha for e in entries])
    records = [store.get(e.blob_sha) for e in entries]
    vocab = Vocab()
    vocab.id_of = dict(store.vocab.id_of)
    vocab.tokens = list(store.vocab.tokens)
    chunk_file, chunk_local, spans = [], [], []
    for f, rec in enumerate(records):
        chunk_file.extend([f] * len(rec.spans))
        chunk_local.extend(range(len(rec.spans)))
        spans.extend(rec.spans)
    return Snapshot(
        store.repo, commit, vocab,
        [e.path for e in entries], [e.blob_sha for e in entries], records,
        np.asarray(chunk_file, np.int64), np.asarray(chunk_local, np.int64), spans,
    )
