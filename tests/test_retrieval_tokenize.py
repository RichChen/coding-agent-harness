from src.retrieval.tokenize import Vocab, tokenize


def test_dotted_name():
    assert tokenize("QuerySet.filter") == ["queryset", "query", "set", "filter"]


def test_snake_with_caps():
    assert tokenize("get_FOO_display") == ["get_foo_display", "get", "foo", "display"]


def test_acronym_camel():
    assert tokenize("HTTPResponse") == ["httpresponse", "http", "response"]


def test_traceback_line():
    line = 'File "/usr/lib/python3.8/site-packages/django/db/models/query.py", line 1234, in filter'
    toks = tokenize(line)
    assert toks == [
        "file", "usr", "lib", "python3", "python", "site", "packages", "django",
        "db", "models", "query", "py", "line", "1234", "in", "filter",
    ]


def test_drops_single_chars_and_lowercases():
    assert tokenize("a = B + cD") == ["cd"]
    assert tokenize("x_y") == ["x_y"]


def test_dunder_keeps_full_and_part():
    assert tokenize("__init__") == ["__init__", "init"]


def test_exception_line():
    assert tokenize("ValueError: invalid literal for int()") == [
        "valueerror", "value", "error", "invalid", "literal", "for", "int",
    ]


def test_vocab_ids_stable():
    v = Vocab(["a", "b"])
    assert v.add("b") == 1 and v.add("c") == 2 and v.lookup("zz") is None and len(v) == 3
