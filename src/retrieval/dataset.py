"""Read the local SWE-bench Verified parquet and expose two isolated views.

``QueryView`` carries only what a retriever may see. The gold diff column is
read here but handed out only through ``load_gold_diffs``, whose sole caller
is ``labels.py``.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, fields
from pathlib import Path

_HF_HUB = Path(os.environ.get("HF_HUB_CACHE")
               or Path.home() / ".cache" / "huggingface" / "hub")
PARQUET_PATH = (
    _HF_HUB
    / "datasets--princeton-nlp--SWE-bench_Verified"
    / "snapshots"
    / "c104f840cc67f8b6eec6f759ebc8b2693d585d4a"
    / "data"
    / "test-00000-of-00001.parquet"
)

_QUERY_COLUMNS = ("instance_id", "repo", "base_commit", "problem_statement")
_GOLD_COLUMN = "patch"


@dataclass(frozen=True)
class QueryView:
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str


def query_view_fields() -> tuple[str, ...]:
    return tuple(f.name for f in fields(QueryView))


def parquet_sha256(path: Path = PARQUET_PATH) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _read_columns(path: Path, columns: tuple[str, ...]) -> dict[str, list]:
    import pyarrow.parquet as pq

    return pq.read_table(path, columns=list(columns)).to_pydict()


def load_query_views(path: Path = PARQUET_PATH) -> list[QueryView]:
    cols = _read_columns(path, _QUERY_COLUMNS)
    return [
        QueryView(*(cols[name][i] for name in _QUERY_COLUMNS))
        for i in range(len(cols["instance_id"]))
    ]


def load_gold_diffs(path: Path = PARQUET_PATH) -> dict[str, str]:
    """instance_id -> gold diff text. Only ``labels.py`` may call this."""
    cols = _read_columns(path, ("instance_id", _GOLD_COLUMN))
    return dict(zip(cols["instance_id"], cols[_GOLD_COLUMN]))
