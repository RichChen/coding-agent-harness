import numpy as np

from src.retrieval.fusion import (
    max_by_file,
    mention_first,
    mentioned_files,
    module_names,
    order_by_score,
    random_order,
    ranks_from_order,
    rrf,
)


def test_order_ties_by_key():
    assert order_by_score([1.0, 2.0, 1.0, 2.0], ["d", "c", "a", "b"]) == [3, 1, 2, 0]


def test_max_by_file():
    out = max_by_file([0.5, 2.0, 1.0], [0, 0, 2], 3)
    assert out[0] == 2.0 and out[1] == -np.inf and out[2] == 1.0


def test_random_order_deterministic_and_instance_specific():
    a = random_order(50, "x__x-1")
    assert a == random_order(50, "x__x-1")
    assert sorted(a) == list(range(50))
    assert a != random_order(50, "x__x-2")


def test_module_names():
    assert module_names("django/db/models/query.py") == ["django.db.models.query"]
    assert module_names("django/db/models/__init__.py") == ["django.db.models"]
    assert module_names("src/_pytest/python.py") == ["src._pytest.python", "_pytest.python"]
    assert module_names("setup.py") == []
    assert module_names("django/__init__.py") == []


PATHS = [
    "django/__init__.py",
    "django/db/models/__init__.py",
    "django/db/models/query.py",
    "django/forms/fields.py",
    "src/_pytest/python.py",
]


def test_mentioned_paths_and_tracebacks():
    text = 'File "/usr/lib/site-packages/django/db/models/query.py", line 3\nsee django\\forms\\fields.py'
    assert mentioned_files(text, PATHS) == {2, 3}


def test_mentioned_dotted_longest_prefix():
    assert mentioned_files("calling django.db.models.query.QuerySet.filter()", PATHS) == {2}
    assert mentioned_files("from django.db.models import Q", PATHS) == {1}
    assert mentioned_files("import django", PATHS) == set()
    assert mentioned_files("_pytest.python.Function", PATHS) == {4}


def test_no_partial_identifier_match():
    assert mentioned_files("mydjango.db.models.query", PATHS) == set()
    assert mentioned_files("models/query.pyc", PATHS) == set()


def test_mention_first_is_stable():
    assert mention_first([4, 2, 0, 3, 1], {3, 0}) == [0, 3, 4, 2, 1]


def test_ranks_from_order():
    assert ranks_from_order([2, 0, 1], 3).tolist() == [2, 3, 1]


def test_rrf_formula_and_weights():
    a = np.array([1, 2, 3])
    b = np.array([3, 2, 1])
    out = rrf([a, b], [1.0, 2.0], 60)
    assert np.allclose(out, [1 / 61 + 2 / 63, 1 / 62 + 2 / 62, 1 / 63 + 2 / 61])
    assert order_by_score(rrf([a, b], [1.0, 1.0], 10), ["x", "y", "z"]) == [0, 2, 1]  # symmetric tie -> key
