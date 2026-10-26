"""Local embedding models and the vector cache. The only module that imports
torch / sentence_transformers (lazily, inside ``Encoder``)."""
from __future__ import annotations

import hashlib
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from src.retrieval import repo_cache

DEFAULT_MAX_LEN = 512


@dataclass(frozen=True)
class ModelSpec:
    key: str
    name: str
    revision: str
    query_prefix: str
    doc_prefix: str
    trust_remote_code: bool
    license: str
    code_revision: str | None = None  # pinned revision of a separate remote-code repo


MODELS = {
    "qwen3-0.6b": ModelSpec(
        "qwen3-0.6b",
        "Qwen/Qwen3-Embedding-0.6B",
        "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
        "Instruct: Given a GitHub issue, retrieve the source code that must be modified to resolve it\nQuery:",
        "",
        False,
        "apache-2.0",
    ),
    "jina-code": ModelSpec(
        "jina-code",
        "jinaai/jina-embeddings-v2-base-code",
        "516f4baf13dec4ddddda8631e019b5737c8bc250",
        "",
        "",
        True,
        "apache-2.0",
        code_revision="3baf9e3ac750e76e8edd3019170176884695fb94",  # jinaai/jina-bert-v2-qk-post-norm
    ),
    "coderank": ModelSpec(
        "coderank",
        "nomic-ai/CodeRankEmbed",
        "3c4b60807d71f79b43f3c4363786d9493691f8b1",
        "Represent this query for searching relevant code: ",
        "",
        True,
        "mit",
    ),
}


def text_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def doc_text(spec: ModelSpec, chunk_text: str) -> str:
    return spec.doc_prefix + chunk_text


def query_text(spec: ModelSpec, issue: str) -> str:
    return spec.query_prefix + issue


class VectorStore:
    """sqlite keyed by sha256(text actually sent to the model); one file per
    (model name, revision, max length), so the full key is all four."""

    def __init__(self, spec: ModelSpec, max_len: int, root: Path | None = None):
        base = (root or repo_cache.cache_dir()) / "vectors"
        base.mkdir(parents=True, exist_ok=True)
        self.path = base / f"{spec.name.replace('/', '__')}@{spec.revision}-L{max_len}.sqlite"
        self.db = sqlite3.connect(self.path)
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
            CREATE TABLE IF NOT EXISTS vec (
                text_sha TEXT PRIMARY KEY, n_tokens INTEGER, truncated INTEGER, v BLOB);
            """
        )
        for k, v in (("model", spec.name), ("revision", spec.revision), ("max_len", str(max_len))):
            row = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
            if row is None:
                self.db.execute("INSERT INTO meta VALUES (?, ?)", (k, v))
            elif row[0] != v:
                raise ValueError(f"{self.path}: meta {k}={row[0]!r} != {v!r}")
        self.db.commit()

    def missing(self, shas: list[str]) -> list[str]:
        have = set()
        uniq = list(dict.fromkeys(shas))
        for i in range(0, len(uniq), 900):
            part = uniq[i:i + 900]
            q = f"SELECT text_sha FROM vec WHERE text_sha IN ({','.join('?' * len(part))})"
            have.update(r[0] for r in self.db.execute(q, part))
        return [s for s in uniq if s not in have]

    def put_many(self, rows: list[tuple[str, int, bool, np.ndarray]]) -> None:
        self.db.executemany(
            "INSERT OR REPLACE INTO vec VALUES (?, ?, ?, ?)",
            [(s, int(n), int(t), v.astype(np.float16).tobytes()) for s, n, t, v in rows],
        )
        self.db.commit()

    def preload(self) -> int:
        """Read every vector with one sequential scan; later ``get_matrix`` calls
        are served from memory (random sqlite reads are slow on spinning disks)."""
        shas, blobs = [], []
        for s, v in self.db.execute("SELECT text_sha, v FROM vec"):
            shas.append(s)
            blobs.append(v)
        self._mem_index = {s: i for i, s in enumerate(shas)}
        self._mem = np.frombuffer(b"".join(blobs), np.float16).reshape(len(shas), -1) if shas else None
        return len(shas)

    def get_matrix(self, shas: list[str]) -> np.ndarray:
        """float32 matrix in the order of ``shas``; raises if any is missing."""
        if getattr(self, "_mem_index", None) is not None:
            idx = [self._mem_index.get(s) for s in shas]
            n_missing = sum(i is None for i in idx)
            if n_missing:
                raise KeyError(f"{n_missing} vectors missing in {self.path.name}")
            return self._mem[idx].astype(np.float32)
        found: dict[str, np.ndarray] = {}
        uniq = list(dict.fromkeys(shas))
        for i in range(0, len(uniq), 900):
            part = uniq[i:i + 900]
            q = f"SELECT text_sha, v FROM vec WHERE text_sha IN ({','.join('?' * len(part))})"
            for s, v in self.db.execute(q, part):
                found[s] = np.frombuffer(v, np.float16)
        missing = [s for s in uniq if s not in found]
        if missing:
            raise KeyError(f"{len(missing)} vectors missing in {self.path.name}")
        return np.stack([found[s] for s in shas]).astype(np.float32)

    def truncation_stats(self, shas: list[str]) -> tuple[int, int]:
        """(n truncated, n) over the given keys."""
        uniq = list(dict.fromkeys(shas))
        trunc = 0
        for i in range(0, len(uniq), 900):
            part = uniq[i:i + 900]
            q = f"SELECT SUM(truncated) FROM vec WHERE text_sha IN ({','.join('?' * len(part))})"
            trunc += self.db.execute(q, part).fetchone()[0] or 0
        return trunc, len(uniq)

    def close(self) -> None:
        self.db.close()


class EmbeddingError(RuntimeError):
    pass


def _modules_with_code_revision(source: str, model_kwargs: dict, config_kwargs: dict) -> list:
    """Build the modules of ``modules.json`` by hand. SentenceTransformer(...)
    pops ``model_kwargs['code_revision']`` for its own module classes, so remote
    model code in a separate repo would otherwise resolve to that repo's main."""
    import importlib
    import json

    from sentence_transformers.models import Transformer

    modules = []
    for entry in json.loads((Path(source) / "modules.json").read_text(encoding="utf-8")):
        mod_name, _, cls_name = entry["type"].rpartition(".")
        if cls_name == "Transformer":
            modules.append(Transformer.load(source, subfolder=entry["path"], trust_remote_code=True,
                                            model_kwargs=dict(model_kwargs), config_kwargs=dict(config_kwargs),
                                            processor_kwargs=dict(config_kwargs)))
        else:
            cls = getattr(importlib.import_module(mod_name), cls_name)
            modules.append(cls.load(source, subfolder=entry["path"]))
    return modules


class Encoder:
    def __init__(self, spec: ModelSpec, max_len: int = DEFAULT_MAX_LEN, batch_size: int = 32):
        import torch
        from sentence_transformers import SentenceTransformer

        self.torch = torch
        self.spec = spec
        self.max_len = max_len
        self.batch_size = batch_size
        model_kwargs = {"torch_dtype": torch.float16}
        config_kwargs = {}
        if spec.code_revision:
            model_kwargs["code_revision"] = spec.code_revision
            config_kwargs["code_revision"] = spec.code_revision
        source = spec.name
        if spec.trust_remote_code:
            # remote code may fetch its own weights without a revision; load the pinned snapshot directory
            from huggingface_hub import snapshot_download

            source = snapshot_download(spec.name, revision=spec.revision, local_files_only=True)
        self.source = source
        if spec.code_revision:
            self.model = SentenceTransformer(modules=_modules_with_code_revision(source, model_kwargs, config_kwargs),
                                             device="cuda")
        else:
            self.model = SentenceTransformer(
                source,
                revision=spec.revision,
                trust_remote_code=spec.trust_remote_code,
                device="cuda",
                model_kwargs=model_kwargs,
                config_kwargs=config_kwargs,
            )
        self.model.half()  # some remote-code loaders ignore torch_dtype
        dtypes = {p.dtype for p in self.model.parameters()}
        if dtypes != {torch.float16}:
            raise EmbeddingError(f"{spec.key}: parameters not float16: {dtypes}")
        self.model.max_seq_length = max_len
        self.tokenizer = self.model.tokenizer
        self.model_class_module = type(self.model[0].auto_model).__module__
        if spec.trust_remote_code:
            pinned = spec.code_revision or spec.revision
            if pinned not in self.model_class_module:
                raise EmbeddingError(f"{spec.key}: remote code loaded from {self.model_class_module}, expected revision {pinned}")

    def token_lengths(self, texts: list[str]) -> np.ndarray:
        """Token count with special tokens, before truncation."""
        enc = self.tokenizer(texts, add_special_tokens=True, truncation=False, verbose=False)
        return np.array([len(x) for x in enc["input_ids"]], np.int64)

    def encode(self, texts: list[str]) -> np.ndarray:
        """Unit-norm float32 vectors. Raises on NaN/inf or zero-norm rows."""
        raw = self.model.encode(
            texts,
            batch_size=self.batch_size,
            convert_to_numpy=True,
            normalize_embeddings=False,
            show_progress_bar=False,
        ).astype(np.float32)
        if not np.isfinite(raw).all():
            raise EmbeddingError(f"{self.spec.key}: non-finite values in batch")
        norms = np.linalg.norm(raw, axis=1)
        if (norms <= 1e-12).any():
            raise EmbeddingError(f"{self.spec.key}: zero-norm vector in batch")
        return raw / norms[:, None]

    def peak_memory_mib(self) -> float:
        return self.torch.cuda.max_memory_allocated() / 2**20

    def reset_peak(self) -> None:
        self.torch.cuda.reset_peak_memory_stats()


def encode_missing(encoder: Encoder, store: VectorStore, texts: list[str], group: int = 2048, log=None) -> dict:
    """Encode texts whose sha is not in ``store``. Texts are processed longest
    first in groups so padding stays small."""
    by_sha = {}
    for t in texts:
        by_sha.setdefault(text_sha(t), t)
    todo = store.missing(list(by_sha))
    if not todo:
        return {"encoded": 0, "tokens": 0, "seconds": 0.0, "truncated": 0}
    todo_texts = [by_sha[s] for s in todo]
    lengths = encoder.token_lengths(todo_texts)
    order = np.argsort(-lengths, kind="stable")
    t0 = time.time()
    tokens = 0
    truncated = 0
    for start in range(0, len(order), group):
        idx = order[start:start + group]
        vecs = encoder.encode([todo_texts[i] for i in idx])
        rows = []
        for j, i in enumerate(idx):
            n = int(lengths[i])
            tr = n > encoder.max_len
            tokens += min(n, encoder.max_len)
            truncated += tr
            rows.append((todo[i], n, tr, vecs[j]))
        store.put_many(rows)
        if log:
            log(f"  {start + len(idx)}/{len(order)} encoded, {time.time() - t0:.0f}s")
    return {"encoded": len(todo), "tokens": tokens, "seconds": time.time() - t0, "truncated": truncated}
