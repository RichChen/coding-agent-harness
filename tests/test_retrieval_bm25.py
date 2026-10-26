import math

import numpy as np

from src.retrieval.bm25 import BM25Index, from_token_lists, query_term_ids


def _manual(docs, query, k1=1.2, b=0.75):
    n = len(docs)
    avgdl = sum(len(d) for d in docs) / n
    out = []
    for d in docs:
        s = 0.0
        for t in set(query):
            df = sum(1 for x in docs if t in x)
            if df == 0:
                continue
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            tf = d.count(t)
            s += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len(d) / avgdl))
        out.append(s)
    return out


DOCS = [
    ["query", "set", "filter", "filter"],
    ["http", "response", "header"],
    ["query", "http", "response", "response", "x", "y"],
    [],
]


def test_matches_manual_formula():
    idx, vocab = from_token_lists(DOCS)
    for q in (["filter"], ["response", "http"], ["query", "query", "zzz"]):
        got = idx.score([vocab.lookup(t) for t in q])
        np.testing.assert_allclose(got, _manual(DOCS, q), rtol=1e-12)


def test_duplicate_triples_are_summed():
    a = BM25Index(2, [0, 0, 1], [0, 0, 1], [1, 2, 1])
    b = BM25Index(2, [0, 1], [0, 1], [3, 1])
    np.testing.assert_allclose(a.score([0, 1]), b.score([0, 1]))
    assert a.df(0) == 1 and a.df(5) == 0


def test_query_term_ids_skips_unknown():
    _, vocab = from_token_lists(DOCS)
    ids = query_term_ids("QuerySet unknownword", vocab)
    assert [vocab.tokens[i] for i in ids] == ["query", "set"]


def test_empty_query_and_empty_index():
    idx, _ = from_token_lists(DOCS)
    assert not idx.score([]).any()
    empty = BM25Index(0, [], [], [])
    assert empty.score([1]).shape == (0,)
