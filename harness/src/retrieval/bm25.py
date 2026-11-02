"""Okapi BM25 over integer term ids, numpy only."""
from __future__ import annotations

import numpy as np

from src.retrieval.tokenize import Vocab, tokenize

DEFAULT_K1 = 1.2
DEFAULT_B = 0.75


class BM25Index:
    """Built from (doc_idx, term_id, tf) triples; duplicate (doc, term) pairs are summed.

    idf = log(1 + (N - df + 0.5) / (df + 0.5)). Each distinct query term
    contributes once (query term frequency is ignored).
    """

    def __init__(self, n_docs: int, doc_idx, term_ids, tfs, k1: float = DEFAULT_K1, b: float = DEFAULT_B):
        self.n_docs = n_docs
        self.k1, self.b = k1, b
        doc_idx = np.asarray(doc_idx, np.int64)
        term_ids = np.asarray(term_ids, np.int64)
        tfs = np.asarray(tfs, np.float64)
        n_terms = int(term_ids.max()) + 1 if term_ids.size else 1
        keys = term_ids * n_docs + doc_idx
        uniq, inv = np.unique(keys, return_inverse=True)
        tf = np.bincount(inv, weights=tfs, minlength=len(uniq))
        self._post_term = uniq // n_docs if n_docs else uniq
        self._post_doc = uniq % n_docs if n_docs else uniq
        self._post_tf = tf
        self.doc_len = np.bincount(doc_idx, weights=tfs, minlength=n_docs).astype(np.float64)
        self.avgdl = float(self.doc_len.mean()) if n_docs else 0.0
        self._n_terms = n_terms
        self._starts = np.searchsorted(self._post_term, np.arange(n_terms + 1))

    def df(self, term_id: int) -> int:
        if term_id < 0 or term_id >= self._n_terms:
            return 0
        return int(self._starts[term_id + 1] - self._starts[term_id])

    def score(self, query_term_ids) -> np.ndarray:
        scores = np.zeros(self.n_docs, np.float64)
        if self.n_docs == 0:
            return scores
        norm = self.k1 * (1.0 - self.b + self.b * self.doc_len / (self.avgdl or 1.0))
        for t in sorted({int(t) for t in query_term_ids if t is not None}):
            if t < 0 or t >= self._n_terms:
                continue
            lo, hi = self._starts[t], self._starts[t + 1]
            if lo == hi:
                continue
            df = hi - lo
            idf = np.log1p((self.n_docs - df + 0.5) / (df + 0.5))
            docs = self._post_doc[lo:hi]
            tf = self._post_tf[lo:hi]
            scores[docs] += idf * tf * (self.k1 + 1.0) / (tf + norm[docs])
        return scores


def query_term_ids(text: str, vocab: Vocab) -> list[int]:
    """Query tokens that exist in the corpus vocabulary."""
    return [i for i in (vocab.lookup(t) for t in tokenize(text)) if i is not None]


def from_token_lists(docs: list[list[str]], k1: float = DEFAULT_K1, b: float = DEFAULT_B) -> tuple[BM25Index, Vocab]:
    vocab = Vocab()
    d_idx, t_idx = [], []
    for d, toks in enumerate(docs):
        for tok in toks:
            d_idx.append(d)
            t_idx.append(vocab.add(tok))
    return BM25Index(len(docs), d_idx, t_idx, np.ones(len(d_idx)), k1, b), vocab
