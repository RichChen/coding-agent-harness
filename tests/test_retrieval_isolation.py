"""Answer isolation for the CR retrieval line (CR-1 contract, section 2)."""
from __future__ import annotations

import importlib.util
import inspect
import re
import sys
from pathlib import Path

import pytest

from src.retrieval import bm25, chunking, dataset, fusion, repo_cache, tokenize

ROOT = Path(__file__).resolve().parents[1]
RETRIEVAL_DIR = ROOT / "src" / "retrieval"
ALLOWED = {"dataset.py", "labels.py"}
FORBIDDEN = ("patch", "test_patch", "hints_text", "FAIL_TO_PASS", "PASS_TO_PASS")
QUOTED = re.compile(r"""(['"])(%s)\1""" % "|".join(re.escape(f) for f in FORBIDDEN))


def _scanned_sources() -> list[Path]:
    files = [p for p in sorted(RETRIEVAL_DIR.glob("*.py")) if p.name not in ALLOWED]
    files += sorted((ROOT / "scripts").glob("cr_*.py"))
    return files


def test_query_view_fields_exact():
    assert set(dataset.query_view_fields()) == {"instance_id", "repo", "base_commit", "problem_statement"}
    assert len(dataset.query_view_fields()) == 4


def test_scan_covers_expected_files():
    names = {p.name for p in _scanned_sources()}
    assert {"bm25.py", "chunking.py", "fusion.py", "metrics.py", "tokenize.py", "repo_cache.py"} <= names
    assert {"cr_prepare.py", "cr_index.py", "cr_eval.py"} <= names


@pytest.mark.parametrize("path", _scanned_sources(), ids=lambda p: p.name)
def test_no_quoted_gold_field_names(path: Path):
    hits = [m.group(0) for m in QUOTED.finditer(path.read_text(encoding="utf-8"))]
    assert hits == [], f"{path.name} mentions gold fields: {hits}"


def test_scanner_catches_both_quote_styles_and_ignores_substrings():
    assert QUOTED.search("x['patch']") and QUOTED.search('y["FAIL_TO_PASS"]')
    assert QUOTED.search("'dispatch'") is None
    assert QUOTED.search('"patches"') is None


def test_retrieval_modules_do_not_import_labels():
    for mod in (bm25, chunking, fusion, repo_cache, tokenize):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert not re.search(r"^\s*(from|import)\s+src\.retrieval(\.labels|\s+import\s+.*labels)", src, re.M), mod.__name__


def _load_cr_eval():
    spec = importlib.util.spec_from_file_location("cr_eval_under_test", ROOT / "scripts" / "cr_eval.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve string annotations through sys.modules
    spec.loader.exec_module(mod)
    return mod


def _signature_mentions_label(fn) -> bool:
    for p in inspect.signature(fn).parameters.values():
        ann = p.annotation if isinstance(p.annotation, str) else getattr(p.annotation, "__name__", str(p.annotation))
        if "label" in p.name.lower() or "LabelView" in str(ann):
            return True
    return False


def test_retriever_signatures_have_no_label_view():
    cr_eval = _load_cr_eval()
    assert cr_eval.RETRIEVERS, "cr_eval.RETRIEVERS must list the retrieval entry points"
    fns = list(cr_eval.RETRIEVERS.values()) + [cr_eval.retrieve_instance]
    for mod in (bm25, fusion, chunking, tokenize):
        fns += [f for _, f in inspect.getmembers(mod, inspect.isfunction) if f.__module__ == mod.__name__]
        for _, cls in inspect.getmembers(mod, inspect.isclass):
            if cls.__module__ == mod.__name__:
                fns += [f for _, f in inspect.getmembers(cls, inspect.isfunction)]
    for fn in fns:
        assert not _signature_mentions_label(fn), fn.__qualname__


def test_retrieve_instance_takes_query_view():
    cr_eval = _load_cr_eval()
    params = list(inspect.signature(cr_eval.retrieve_instance).parameters.values())
    assert "QueryView" in str(params[0].annotation)


def test_scan_covers_embed_module():
    assert "embed.py" in {p.name for p in _scanned_sources()}


@pytest.mark.parametrize("path", _scanned_sources(), ids=lambda p: p.name)
def test_no_gold_diff_loader_outside_allowed(path: Path):
    """Contract 11.4."""
    assert "load_gold_diffs" not in path.read_text(encoding="utf-8"), path.name


# CR-2: the only files allowed to read labels. cr2_oracle_prefetch.py builds the O-group list from the gold
# files (CR-2 contract 7.1, the answer-isolation exception); analyze_cr2.py computes the metrics from the gold
# files after all runs (CR-2 contract section 4). The runner reads only the frozen oracle file.
CR2_LABEL_READERS = {"cr2_oracle_prefetch.py", "analyze_cr2.py"}
LABELS_IMPORT = re.compile(r"src\.retrieval\.labels|from\s+src\.retrieval\s+import\s+[^\n]*\blabels\b")
ORACLE_IMPORT = re.compile(r"import\s+cr2_oracle_prefetch|from\s+scripts(\.cr2_oracle_prefetch|\s+import\s+[^\n]*\bcr2_oracle_prefetch\b)")


def _cr2_sources() -> list[Path]:
    files = sorted((ROOT / "scripts").glob("cr2_*.py"))
    files += [ROOT / "scripts" / "run_cr2.py", ROOT / "scripts" / "analyze_cr2.py",
              RETRIEVAL_DIR / "serve.py", ROOT / "src" / "tools" / "retrieve.py"]
    return files


def test_cr2_scan_covers_oracle_script():
    names = {p.name for p in _cr2_sources()}
    assert {"cr2_oracle_prefetch.py", "run_cr2.py", "cr2_cost.py"} <= names


@pytest.mark.parametrize("path", _cr2_sources(), ids=lambda p: p.name)
def test_cr2_labels_only_in_allowed_files(path: Path):
    text = path.read_text(encoding="utf-8")
    assert QUOTED.search(text) is None, path.name
    assert "load_gold_diffs" not in text, path.name
    if path.name not in CR2_LABEL_READERS:
        assert LABELS_IMPORT.search(text) is None, path.name
        assert ORACLE_IMPORT.search(text) is None, path.name


def test_cr2_label_import_patterns():
    assert LABELS_IMPORT.search("from src.retrieval import dataset, labels")
    assert LABELS_IMPORT.search("import src.retrieval.labels as l")
    assert LABELS_IMPORT.search("from src.retrieval import labelsx") is None
    assert ORACLE_IMPORT.search("from scripts import cr2_oracle_prefetch")
    assert ORACLE_IMPORT.search("written by cr2_oracle_prefetch.py") is None


def test_cr1b_retrievers_take_query_view_and_no_label_view():
    from src.retrieval import embed

    cr_eval = _load_cr_eval()
    assert set(cr_eval.CR1B_RETRIEVERS) == {"embedding", "hybrid", "hybrid_mention"}
    fns = list(cr_eval.CR1B_RETRIEVERS.values()) + [
        cr_eval.EmbeddingView.__init__, cr_eval.RankMaterial.__init__, cr_eval._hybrid_orders,
        cr_eval.per_instance_embed_select, cr_eval.per_instance_grid, cr_eval.run_generic,
    ]
    fns += [f for _, f in inspect.getmembers(embed, inspect.isfunction) if f.__module__ == embed.__name__]
    for _, cls in inspect.getmembers(embed, inspect.isclass):
        if cls.__module__ == embed.__name__:
            fns += [f for _, f in inspect.getmembers(cls, inspect.isfunction)]
    for fn in fns:
        assert not _signature_mentions_label(fn), fn.__qualname__
    for fn in cr_eval.CR1B_RETRIEVERS.values():
        first = list(inspect.signature(fn).parameters.values())[0]
        assert "QueryView" in str(first.annotation), fn.__name__
