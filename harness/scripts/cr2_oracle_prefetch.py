"""CR-2 r1: freeze the O-group list (contract section 7.1). The only CR-2 script that reads labels.

For each pool task, the list holds the gold files (labels.py file labels) in the frozen hybrid file order of the
agent-visible issue, at most five, then non-gold files in hybrid order until there are five. A gold file lists its
gold chunks (labels.py chunk labels) by start line, at most two; a filler file lists its two best hybrid chunks, as
in P. The text is rendered by the P renderer, so header, footer, format and the 1500-byte cap are the P ones.

    cr2_oracle_prefetch.py      write docs/retrieval/cr2-oracle-prefetch.json (refuses to overwrite)

The runner reads only the frozen file; it does not import this script or labels.py.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.handoff_sha import handoff_sha256  # noqa: E402
from scripts.run_cr2 import (  # noqa: E402
    ORACLE,
    ORACLE_SCHEMA,
    _display_path,
    _git_head,
    _sha,
    agent_issue,
    materialize_runner_task,
    pool_tasks,
)
from src.retrieval import serve  # noqa: E402


def oracle_hits(index, issue: str, gold_files, gold_chunks) -> tuple[list[serve.FileHit], str, dict]:
    """The O list for one task and how many gold files it lists."""
    file_order, chunk_order, source = index.orders(issue)
    ix = index.ix
    snap = ix.snap
    paths = [snap.paths[i] for i in file_order]
    gold = set(gold_files)
    missing = sorted(gold - set(paths))
    if missing:
        raise ValueError("gold files not in the snapshot: " + ", ".join(missing))
    ranked_gold = [p for p in paths if p in gold][:serve.PREFETCH_FILES]
    chosen = ranked_gold + [p for p in paths if p not in gold][:serve.PREFETCH_FILES - len(ranked_gold)]
    position = {cid: i for i, cid in enumerate(ix.chunk_ids)}
    unknown = sorted(cid for cid in gold_chunks if cid not in position)
    if unknown:
        raise ValueError("gold chunks not in the snapshot: " + ", ".join(unknown))

    def span(i):
        s = snap.chunk_spans[i]
        return serve.ChunkHit(s.symbol, s.start_line, s.end_line)

    best: dict[str, list[serve.ChunkHit]] = {p: [] for p in chosen if p not in gold}
    for i in chunk_order:
        got = best.get(snap.paths[int(snap.chunk_file[i])])
        if got is not None and len(got) < serve.CHUNKS_PER_FILE:
            got.append(span(i))
    hits = []
    for path in chosen:
        if path in gold:
            own = sorted((span(position[cid]) for cid in gold_chunks if cid.startswith(path + "::")),
                         key=lambda c: (c.start_line, c.symbol))
            hits.append(serve.FileHit(path, tuple(own[:serve.CHUNKS_PER_FILE])))
        else:
            hits.append(serve.FileHit(path, tuple(best[path])))
    return hits, source, {"files": len(gold), "listed": len(ranked_gold)}


def build_manifest(tasks: list[dict], service, label_views: dict) -> dict:
    rows = []
    for task in tasks:
        prepared = materialize_runner_task(task)
        issue = agent_issue(prepared)
        view = label_views[task["id"]]
        index = service.task_index(task["repo"], task["base_commit"])
        hits, source, gold = oracle_hits(index, issue, view.gold_files, view.gold_chunks)
        text = serve.render_prefetch(hits)
        rows.append({"task": task["id"], "repo": task["repo"], "base_commit": task["base_commit"],
                     "issue_sha256": _sha(issue), "query_vector": source, "files": serve.hits_record(hits),
                     "text": text, "sha256": _sha(text), "bytes": len(text.encode("utf-8")), "gold": gold})
        print(task["id"], rows[-1]["bytes"], gold, flush=True)
    sizes = [row["bytes"] for row in rows]
    return {
        "schema": ORACLE_SCHEMA,
        "code_commit": _git_head(),
        "cr1_freeze_sha256": handoff_sha256(serve.FREEZE_PATH),
        "frozen": serve.FROZEN,
        "files_per_task": serve.PREFETCH_FILES,
        "chunks_per_file": serve.CHUNKS_PER_FILE,
        "max_bytes": serve.PREFETCH_MAX_BYTES,
        "header": serve.PREFETCH_HEADER,
        "footer": serve.PREFETCH_FOOTER,
        "bytes": {"min": min(sizes), "median": statistics.median(sizes), "max": max(sizes)},
        "tasks_more_than_5_gold_files": sum(1 for row in rows if row["gold"]["files"] > serve.PREFETCH_FILES),
        "tasks_without_gold_files": sum(1 for row in rows if row["gold"]["files"] == 0),
        "tasks": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.parse_args()
    if ORACLE.exists():
        raise SystemExit(_display_path(ORACLE) + " exists; the oracle text is frozen once")
    from src.retrieval import dataset, labels

    tasks = pool_tasks()
    ids = {task["id"] for task in tasks}
    views = labels.load_label_views([q for q in dataset.load_query_views() if q.instance_id in ids])
    manifest = build_manifest(tasks, serve.RetrievalService.from_freeze(), views)
    ORACLE.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print("oracle", manifest["bytes"], "more than 5 gold files:", manifest["tasks_more_than_5_gold_files"],
          "without gold:", manifest["tasks_without_gold_files"], handoff_sha256(ORACLE))


if __name__ == "__main__":
    main()
