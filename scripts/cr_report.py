"""CR-1c: statistics and tables from the frozen test run. No retrieval.

Inputs (read only):
    runs/cr/test-frozen-per-instance.jsonl   per-instance metrics written by cr_eval.py frozen
    runs/cr/test-frozen-rankings.jsonl       rankings written by cr_eval.py frozen
    docs/retrieval/cr1-split.json            test ids and the 62 holdout ids
    docs/retrieval/cr1-freeze.json           frozen embedding model (for the query-length lookup)
    runs/cr/labels.jsonl                     gold files, only to print the win/loss examples
    vector cache of the frozen model         stored token count of each issue (embedding truncation groups)
    QueryView.problem_statement              first characters of the issue in the examples

Outputs:
    docs/retrieval/cr1-test-per-instance.jsonl   one line per (instance, retriever): metrics and top 10 files
    docs/retrieval/cr1-test-summary.json         all statistics below
    runs/cr/cr1-test-tables.md                   markdown tables for CR-1-RESULTS
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.retrieval import dataset  # noqa: E402
from src.retrieval.embed import MODELS, VectorStore, query_text, text_sha  # noqa: E402
from src.retrieval.metrics import ALL_METRICS, cluster_bootstrap, paired_bootstrap, summarize  # noqa: E402

RUNS = ROOT / "runs" / "cr"
DOCS = ROOT / "docs" / "retrieval"
RETRIEVERS = ["random", "path_mention", "bm25", "embedding", "hybrid", "hybrid_mention"]
MAIN = ("hybrid", "bm25")
SECONDARY = [("embedding", "bm25"), ("hybrid", "embedding"), ("hybrid_mention", "path_mention"), ("hybrid_mention", "hybrid")]
FUNC_PAIRS = [("hybrid", "bm25"), ("embedding", "bm25"), ("hybrid", "embedding")]
N_EXAMPLES = 3


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def compare(by: dict, ids: list[str], a: str, b: str, metric: str) -> dict:
    xa = [by[(i, a)][metric] for i in ids]
    xb = [by[(i, b)][metric] for i in ids]
    repos = [by[(i, a)]["repo"] for i in ids]
    cluster = cluster_bootstrap(xa, xb, repos) if len(set(repos)) > 1 else None
    return {"n": len(ids), "paired": paired_bootstrap(xa, xb), "cluster": cluster}


def table(by: dict, ids: list[str], names=RETRIEVERS) -> dict:
    return {name: summarize([by[(i, name)] for i in ids], ALL_METRICS) for name in names}


def query_tokens(queries: dict, ids: list[str]) -> dict[str, int]:
    freeze = json.loads((DOCS / "cr1-freeze.json").read_text(encoding="utf-8"))
    spec = MODELS[freeze["embedding"]["model"]]
    vs = VectorStore(spec, freeze["embedding"]["max_len"])
    out = {}
    for i in ids:
        row = vs.db.execute("SELECT n_tokens FROM vec WHERE text_sha=?",
                            (text_sha(query_text(spec, queries[i].problem_statement)),)).fetchone()
        out[i] = int(row[0])
    vs.close()
    return out, freeze["embedding"]["max_len"]


def first_rank(mrr: float) -> str:
    return str(round(1 / mrr)) if mrr > 0 else ">100"


def main() -> int:
    split = json.loads((DOCS / "cr1-split.json").read_text(encoding="utf-8"))
    test_ids = set(split["test_ids"])
    holdout = set(split["holdout_ids"])
    rows = read_jsonl(RUNS / "test-frozen-per-instance.jsonl")
    rankings = {r["instance_id"]: r for r in read_jsonl(RUNS / "test-frozen-rankings.jsonl")}
    assert {r["instance_id"] for r in rows} == test_ids == set(rankings), "test run does not cover exactly the test split"

    unret = sorted({r["instance_id"] for r in rows if r["unretrievable"]})
    by = {(r["instance_id"], r["retriever"]): r for r in rows if not r["unretrievable"]}
    ids = sorted({i for i, _ in by})
    out: dict = {"n_test": len(test_ids), "n_unretrievable": len(unret), "unretrievable_ids": unret, "n_scored": len(ids)}

    with open(DOCS / "cr1-test-per-instance.jsonl", "w", encoding="utf-8", newline="\n") as fh:
        for r in sorted(rows, key=lambda r: (r["instance_id"], RETRIEVERS.index(r["retriever"]))):
            rec = {"instance_id": r["instance_id"], "repo": r["repo"], "retriever": r["retriever"],
                   "holdout": r["instance_id"] in holdout, "unretrievable": r["unretrievable"]}
            rec.update({m: r.get(m) for m in ALL_METRICS})
            rec["top10_files"] = rankings[r["instance_id"]]["rankings"][r["retriever"]]["files"][:10]
            fh.write(json.dumps(rec) + "\n")

    out["overall"] = table(by, ids)
    out["main"] = {m: compare(by, ids, *MAIN, m) for m in ("hit@5", "mrr")}
    out["secondary"] = {f"{a}-{b}": {m: compare(by, ids, a, b, m) for m in ("hit@5", "mrr")} for a, b in SECONDARY}
    out["function_level"] = {f"{a}-{b}": {m: compare(by, ids, a, b, m) for m in ("fn_hit@5", "fn_hit@10")} for a, b in FUNC_PAIRS}

    subsets = {
        "django": [i for i in ids if by[(i, "bm25")]["repo"] == "django/django"],
        "non_django": [i for i in ids if by[(i, "bm25")]["repo"] != "django/django"],
        "holdout": [i for i in ids if i in holdout],
        "mention": [i for i in ids if by[(i, "path_mention")]["n_mentioned"] > 0],
        "no_mention": [i for i in ids if by[(i, "path_mention")]["n_mentioned"] == 0],
    }
    out["subsets"] = {}
    for name, sub in subsets.items():
        out["subsets"][name] = {
            "n": len(sub),
            "table": {k: {"all": v["all"], "macro": v["macro"]} for k, v in table(by, sub).items()},
            "hybrid-bm25": {m: compare(by, sub, *MAIN, m) for m in ("hit@5", "mrr")},
        }
    out["subsets"]["holdout"]["ids"] = subsets["holdout"]
    pm = [by[(i, "path_mention")] for i in ids]
    out["path_mention"] = {"with_any_mention": sum(r["n_mentioned"] > 0 for r in pm),
                           "mention_includes_gold": sum(r["mention_has_gold"] for r in pm), "scored": len(pm)}

    queries = {q.instance_id: q for q in dataset.load_query_views() if q.instance_id in test_ids}
    toks, max_len = query_tokens(queries, ids)
    out["truncation"] = {"max_len": max_len}
    for name, sub in (("over", [i for i in ids if toks[i] > max_len]), ("within", [i for i in ids if toks[i] <= max_len])):
        g = {"n": len(sub)}
        for m in ("hit@5", "mrr"):
            e = [by[(i, "embedding")][m] for i in sub]
            b = [by[(i, "bm25")][m] for i in sub]
            g[m] = {"embedding": float(np.mean(e)), "bm25": float(np.mean(b)), "embedding-bm25": float(np.mean(e) - np.mean(b))}
        out["truncation"][name] = g

    gold = {r["instance_id"]: r["gold_files"] for r in read_jsonl(RUNS / "labels.jsonl") if r["instance_id"] in test_ids}
    diffs = [(by[(i, "hybrid")]["mrr"] - by[(i, "bm25")]["mrr"], i) for i in ids]
    wins = sorted(diffs, key=lambda t: (-t[0], t[1]))[:N_EXAMPLES]
    losses = sorted(diffs, key=lambda t: (t[0], t[1]))[:N_EXAMPLES]

    def example(d, i):
        rk = rankings[i]["rankings"]
        return {"instance_id": i, "repo": by[(i, "bm25")]["repo"], "mrr_diff": d,
                "issue_head": " ".join(queries[i].problem_statement.split())[:200], "gold_files": gold[i],
                "first_gold_rank": {k: first_rank(by[(i, k)]["mrr"]) for k in ("bm25", "embedding", "hybrid")},
                "top3": {k: rk[k]["files"][:3] for k in ("bm25", "embedding", "hybrid")}}

    out["examples"] = {"rule": "sort by hybrid MRR - bm25 MRR; wins = 3 largest, losses = 3 smallest; ties by instance_id",
                       "n_hybrid_better": sum(d > 0 for d, _ in diffs), "n_equal": sum(d == 0 for d, _ in diffs),
                       "n_hybrid_worse": sum(d < 0 for d, _ in diffs),
                       "wins": [example(d, i) for d, i in wins], "losses": [example(d, i) for d, i in losses]}

    (DOCS / "cr1-test-summary.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    (RUNS / "cr1-test-tables.md").write_text(render(out), encoding="utf-8")
    print(render(out))
    return 0


def f(x, n=3):
    return "—" if x is None else f"{x:.{n}f}"


def ci(p):
    return f"{p['diff']:+.3f} [{p['lo']:+.3f}, {p['hi']:+.3f}]"


def metric_table(tab: dict, macro: bool = True) -> list[str]:
    head = "| 检索器 | n | hit@1 | hit@5 | hit@10 | recall@10 | MRR | fn_hit@5 | fn_hit@10 |" + (" 宏平均 hit@5 | 宏平均 MRR |" if macro else "")
    lines = [head, "|" + "---|" * (head.count("|") - 1)]
    for name, s in tab.items():
        a = s["all"]
        line = f"| {name} | {a['n']} | " + " | ".join(f(a[m]) for m in ALL_METRICS) + " |"
        if macro:
            line += f" {f(s['macro']['hit@5'])} | {f(s['macro']['mrr'])} |"
        lines.append(line)
    return lines


def compare_rows(label: str, c: dict) -> str:
    cluster = ci(c["cluster"]) if c["cluster"] is not None else "单仓库，不适用"
    return f"| {label} | {c['n']} | {ci(c['paired'])} | {cluster} |"


def render(out: dict) -> str:
    L = [f"# CR-1c test tables (n_test={out['n_test']}, scored={out['n_scored']}, unretrievable={out['n_unretrievable']})", ""]
    L += ["## main", "| 比较 | n | 按题 paired [95%] | 按仓库 cluster [95%] |", "|---|---|---|---|"]
    for m, c in out["main"].items():
        L.append(compare_rows(f"hybrid − bm25 {m}", c))
    for section in ("secondary", "function_level"):
        L += ["", f"## {section}", "| 比较 | n | 按题 paired [95%] | 按仓库 cluster [95%] |", "|---|---|---|---|"]
        for k, d in out[section].items():
            for m, c in d.items():
                L.append(compare_rows(f"{k.replace('-', ' − ')} {m}", c))
    L += ["", "## overall"] + metric_table(out["overall"])
    L += ["", "## by repo (MRR / hit@5)"]
    repos = list(out["overall"]["bm25"]["by_repo"])
    L += ["| 仓库 | n | " + " | ".join(RETRIEVERS) + " |", "|" + "---|" * (len(RETRIEVERS) + 2)]
    for repo in repos + ["宏平均"]:
        if repo == "宏平均":
            cells = [f"{f(out['overall'][x]['macro']['mrr'])} / {f(out['overall'][x]['macro']['hit@5'])}" for x in RETRIEVERS]
            L.append(f"| **仓库宏平均** | {out['overall']['bm25']['macro']['n_repos']} 个仓库 | " + " | ".join(cells) + " |")
        else:
            br = out["overall"]["bm25"]["by_repo"][repo]
            cells = [f"{f(out['overall'][x]['by_repo'][repo]['mrr'])} / {f(out['overall'][x]['by_repo'][repo]['hit@5'])}" for x in RETRIEVERS]
            L.append(f"| {repo} | {br['n']} | " + " | ".join(cells) + " |")
    for name, s in out["subsets"].items():
        L += ["", f"## subset {name} (n={s['n']})"] + metric_table(s["table"])
        L += ["| 比较 | n | 按题 paired [95%] | 按仓库 cluster [95%] |", "|---|---|---|---|"]
        for m, c in s["hybrid-bm25"].items():
            L.append(compare_rows(f"hybrid − bm25 {m}", c))
    L += ["", "## path mention", json.dumps(out["path_mention"])]
    t = out["truncation"]
    L += ["", f"## issue length (> {t['max_len']} tokens of the frozen embedding tokenizer)",
          "| 组 | n | embedding hit@5 | bm25 hit@5 | 差 | embedding MRR | bm25 MRR | 差 |", "|---|---|---|---|---|---|---|---|"]
    for name in ("over", "within"):
        g = t[name]
        L.append(f"| {name} | {g['n']} | {f(g['hit@5']['embedding'])} | {f(g['hit@5']['bm25'])} | {g['hit@5']['embedding-bm25']:+.3f} | "
                 f"{f(g['mrr']['embedding'])} | {f(g['mrr']['bm25'])} | {g['mrr']['embedding-bm25']:+.3f} |")
    ex = out["examples"]
    L += ["", "## examples", ex["rule"],
          f"hybrid better {ex['n_hybrid_better']}, equal {ex['n_equal']}, worse {ex['n_hybrid_worse']}"]
    for kind in ("wins", "losses"):
        L.append(f"### {kind}")
        for e in ex[kind]:
            L.append(json.dumps(e, ensure_ascii=False))
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
