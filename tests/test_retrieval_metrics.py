import pytest

from src.retrieval.labels import LabelView
from src.retrieval.metrics import cluster_bootstrap, paired_bootstrap, score_instance, summarize


def _lv(files, chunks=(), repo="o/r", iid="i"):
    return LabelView(iid, repo, tuple(files), tuple(chunks), tuple(chunks), 0, ())


def test_file_metrics():
    lv = _lv(["b.py", "d.py"])
    m = score_instance(lv, ["a.py", "b.py", "c.py", "d.py"], [])
    assert m["hit@1"] == 0 and m["hit@5"] == 1 and m["hit@10"] == 1
    assert m["recall@10"] == 1.0 and m["mrr"] == 0.5


def test_partial_recall_and_mrr_depth():
    lv = _lv(["z.py", "a.py"])
    ranked = [f"f{i}.py" for i in range(10)] + ["a.py"]
    m = score_instance(lv, ranked, [])
    assert m["hit@10"] == 0 and m["recall@10"] == 0 and m["mrr"] == pytest.approx(1 / 11)
    deep = [f"f{i}.py" for i in range(100)] + ["a.py"]
    assert score_instance(lv, deep, [])["mrr"] == 0.0


def test_function_metrics():
    lv = _lv(["a.py"], ["a.py::f@1"])
    m = score_instance(lv, ["a.py"], ["x", "y", "z", "w", "v", "a.py::f@1"])
    assert m["fn_hit@5"] == 0 and m["fn_hit@10"] == 1


def test_unretrievable_raises():
    with pytest.raises(ValueError):
        score_instance(_lv([]), ["a.py"], [])


def test_summarize_macro():
    rows = [
        {"repo": "a", "mrr": 1.0},
        {"repo": "a", "mrr": 0.0},
        {"repo": "a", "mrr": 1.0},
        {"repo": "b", "mrr": 0.0},
    ]
    s = summarize(rows, metrics=("mrr",))
    assert s["all"]["mrr"] == 0.5 and s["all"]["n"] == 4
    assert s["by_repo"]["a"]["mrr"] == pytest.approx(2 / 3)
    assert s["macro"]["mrr"] == pytest.approx(1 / 3)


def test_bootstraps_deterministic_and_bracket_mean():
    a = [1, 0, 1, 1, 0, 1, 1, 1]
    b = [0, 0, 1, 0, 0, 1, 0, 1]
    r1 = paired_bootstrap(a, b, n_boot=2000)
    assert r1 == paired_bootstrap(a, b, n_boot=2000)
    assert r1["lo"] <= r1["diff"] <= r1["hi"] and r1["diff"] == pytest.approx(3 / 8)
    c = cluster_bootstrap(a, b, ["x", "x", "y", "y", "z", "z", "w", "w"], n_boot=2000)
    assert c["lo"] <= c["diff"] <= c["hi"]
    zero = paired_bootstrap([1, 1], [1, 1], n_boot=100)
    assert zero == {"diff": 0.0, "lo": 0.0, "hi": 0.0}
