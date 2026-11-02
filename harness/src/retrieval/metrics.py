"""Retrieval metrics. Gold information arrives only as ``labels.LabelView``."""
from __future__ import annotations

from collections import defaultdict

import numpy as np

from src.retrieval.labels import LabelView

MRR_DEPTH = 100
BOOTSTRAP_SEED = 20261018
FILE_METRICS = ("hit@1", "hit@5", "hit@10", "recall@10", "mrr")
FUNC_METRICS = ("fn_hit@5", "fn_hit@10")
ALL_METRICS = FILE_METRICS + FUNC_METRICS


def first_rank(ranked: list[str], gold: set[str], depth: int = MRR_DEPTH) -> int | None:
    for r, item in enumerate(ranked[:depth], 1):
        if item in gold:
            return r
    return None


def score_instance(label: LabelView, ranked_files: list[str], ranked_chunks: list[str]) -> dict[str, float]:
    """Per-instance metrics for a retrievable instance."""
    if label.unretrievable:
        raise ValueError(f"{label.instance_id} has no in-corpus gold file")
    gold = set(label.gold_files)
    r = first_rank(ranked_files, gold)
    out = {f"hit@{k}": float(r is not None and r <= k) for k in (1, 5, 10)}
    out["recall@10"] = len(gold & set(ranked_files[:10])) / len(gold)
    out["mrr"] = 1.0 / r if r is not None else 0.0
    gold_chunks = set(label.gold_chunks)
    rc = first_rank(ranked_chunks, gold_chunks)
    for k in (5, 10):
        out[f"fn_hit@{k}"] = float(rc is not None and rc <= k)
    return out


def summarize(rows: list[dict], metrics=ALL_METRICS) -> dict:
    """rows: {"repo": str, <metric>: float, ...}. Returns overall mean,
    per-repo means and the repo macro average (each repo weighted equally)."""
    by_repo: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_repo[row["repo"]].append(row)
    per_repo = {
        repo: {"n": len(rs), **{m: float(np.mean([r[m] for r in rs])) for m in metrics}}
        for repo, rs in sorted(by_repo.items())
    }
    overall = {"n": len(rows), **{m: float(np.mean([r[m] for r in rows])) if rows else float("nan") for m in metrics}}
    macro = {
        "n_repos": len(per_repo),
        **{m: float(np.mean([v[m] for v in per_repo.values()])) if per_repo else float("nan") for m in metrics},
    }
    return {"all": overall, "by_repo": per_repo, "macro": macro}


def paired_bootstrap(a, b, n_boot: int = 10000, seed: int = BOOTSTRAP_SEED) -> dict[str, float]:
    """Mean of (a - b) over instances with a percentile 95% interval."""
    d = np.asarray(a, np.float64) - np.asarray(b, np.float64)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(d), size=(n_boot, len(d)))
    means = d[idx].mean(axis=1)
    return {"diff": float(d.mean()), "lo": float(np.percentile(means, 2.5)), "hi": float(np.percentile(means, 97.5))}


def cluster_bootstrap(a, b, clusters, n_boot: int = 10000, seed: int = BOOTSTRAP_SEED) -> dict[str, float]:
    """Resample clusters (repos) with replacement; statistic is the instance-level mean difference."""
    d = np.asarray(a, np.float64) - np.asarray(b, np.float64)
    labels = np.asarray(clusters)
    groups = [d[labels == c] for c in sorted(set(labels.tolist()))]
    sums = np.array([g.sum() for g in groups])
    sizes = np.array([len(g) for g in groups])
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, len(groups), size=(n_boot, len(groups)))
    means = sums[pick].sum(axis=1) / sizes[pick].sum(axis=1)
    return {"diff": float(d.mean()), "lo": float(np.percentile(means, 2.5)), "hi": float(np.percentile(means, 97.5))}
