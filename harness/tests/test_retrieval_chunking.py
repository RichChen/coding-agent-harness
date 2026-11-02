import pytest

from src.retrieval.chunking import (
    WINDOW_LINES,
    chunk_source,
    is_corpus_path,
    is_test_path,
    locate_line,
)


@pytest.mark.parametrize(
    "path,expected",
    [
        ("django/db/models/query.py", False),
        ("tests/model_fields/models.py", True),
        ("sympy/core/tests/test_basic.py", True),
        ("testing/python/collect.py", True),
        ("src/_pytest/test/x.py", True),
        ("pkg/test_utils.py", True),
        ("pkg/utils_test.py", True),
        ("conftest.py", True),
        ("pkg/conftest.py", True),
        ("pkg/testing.py", False),
        ("pkg/tests.py", False),
        ("pkg/contest.py", False),
        ("pkg/attest/x.py", False),
        ("sympy/testing/runtests.py", True),
    ],
)
def test_is_test_path(path, expected):
    assert is_test_path(path) is expected


def test_is_corpus_path():
    assert is_corpus_path("a/b.py")
    assert not is_corpus_path("a/b.txt")
    assert not is_corpus_path("a/tests/b.py")


SRC = '''import os

X = 1


@decorator
def top(a):
    def inner():
        return a
    return inner


class Outer(Base):
    attr = 2

    def method(self):
        return 1

    class Inner:
        def deep(self):
            pass

    other = 3


async def coro():
    pass
'''


def _by_symbol(spans):
    return {s.symbol: s for s in spans}


def test_ast_chunks_symbols_and_ranges():
    spans, texts, ok = chunk_source(SRC)
    assert ok
    syms = _by_symbol(spans)
    assert spans[0].symbol == "<module>" and spans[0].kind == "module"
    assert set(syms) == {"<module>", "top", "Outer.method", "Outer.Inner.deep", "Outer.Inner", "Outer", "coro"}
    assert syms["top"].start_line == 6  # decorator line
    assert syms["top"].end_line == 10
    assert syms["Outer"].kind == "class"
    assert syms["Outer.method"].kind == "function"


def test_nested_function_is_part_of_outer():
    spans, texts, _ = chunk_source(SRC)
    top_text = texts[[s.symbol for s in spans].index("top")]
    assert "def inner" in top_text
    assert "inner" not in {s.symbol for s in spans}


def test_class_chunk_excludes_methods_and_module_excludes_defs():
    spans, texts, _ = chunk_source(SRC)
    t = dict(zip([s.symbol for s in spans], texts))
    assert "attr = 2" in t["Outer"] and "other = 3" in t["Outer"]
    assert "return 1" not in t["Outer"] and "def deep" not in t["Outer"]
    assert "import os" in t["<module>"] and "X = 1" in t["<module>"]
    assert "def top" not in t["<module>"] and "class Outer" not in t["<module>"]


def test_chunks_partition_file_lines():
    spans, texts, _ = chunk_source(SRC)
    total = sum(len(t.split("\n")) for t in texts)
    assert total == len(SRC.rstrip("\n").split("\n"))


def test_parse_failure_falls_back_to_windows():
    src = "print 'py2'\n" + "\n".join(f"x{i} = {i}" for i in range(149))
    spans, texts, ok = chunk_source(src)
    assert not ok
    assert [(s.start_line, s.end_line) for s in spans] == [(1, 60), (41, 100), (81, 140), (121, 150)]
    assert all(s.kind == "window" for s in spans)
    assert len(texts[0].split("\n")) == WINDOW_LINES


def test_short_failure_single_window():
    spans, _, ok = chunk_source("def (:\n")
    assert not ok and [(s.start_line, s.end_line) for s in spans] == [(1, 1)]


def test_empty_file_has_module_chunk():
    spans, texts, ok = chunk_source("")
    assert ok and len(spans) == 1 and spans[0].kind == "module"


def test_locate_line_innermost():
    spans, _, _ = chunk_source(SRC)
    name = lambda ln: spans[locate_line(spans, ln)].symbol
    assert name(1) == "<module>"
    assert name(6) == "top"          # decorator
    assert name(9) == "top"          # nested function body
    assert name(14) == "Outer"       # class attribute
    assert name(17) == "Outer.method"
    assert name(21) == "Outer.Inner.deep"
    assert name(19) == "Outer.Inner"
    assert name(23) == "Outer"
    assert name(0) == "<module>"


def test_locate_line_windows():
    src = "print 'x'\n" + "\n".join("y" for _ in range(99))
    spans, _, _ = chunk_source(src)
    assert spans[locate_line(spans, 50)].start_line == 1
    assert spans[locate_line(spans, 70)].start_line == 41
    assert spans[locate_line(spans, 0)].start_line == 1
