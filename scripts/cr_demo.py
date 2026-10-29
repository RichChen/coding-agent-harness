"""Code retrieval demo (CR-2e contract section 3). Offline: no model calls, no cost.

    cr_demo.py <instance_id> [--run <run_dir>] [--show-gold] [--out <file.md>]

Writes Markdown: the issue; the top 5 files of BM25, embedding and hybrid under the frozen CR-1 configuration
(cr1-freeze) on the base_commit snapshot, with the 2 best chunks of each hybrid file; with --show-gold the gold
files and their rank in each order; with --run the trajectory of that run, its retrieve_code results (or the
P-group prefetch list), the files it changed and the grader verdict.
Gold files are read only with --show-gold, through analyze_cr2.load_gold.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TOP = 5
ISSUE_CHARS = 500
ARG_CHARS = 80
PREFETCH = ROOT / "docs" / "retrieval" / "cr2-prefetch.json"
PATH_TOOLS = ("read_file", "edit_file", "search_replace", "write_file")
SOURCE_NOTE = "来源：base_commit 快照，`cr1-freeze`（jina-code @516f4baf，hybrid k=60、w_emb=2、文件级融合、BM25 块聚合）。"


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _cell(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def query_view(instance_id: str):
    from src.retrieval import dataset

    for q in dataset.load_query_views():
        if q.instance_id == instance_id:
            return q
    raise SystemExit("unknown instance_id " + instance_id)


def rankings(service, q) -> dict:
    """Full BM25 (chunk-aggregated), embedding and hybrid file orders, and the hybrid top files with chunks."""
    from scripts import cr_eval
    from src.retrieval import serve
    from src.retrieval.dataset import QueryView

    index = service.task_index(q.repo, q.base_commit)
    qv, source = service.query_vector(q.problem_statement)
    scores = serve._Scores(service.spec, index.matrix @ qv)
    query = QueryView(f"cr2:{q.repo}@{q.base_commit}", q.repo, q.base_commit, q.problem_statement)
    ix = index.ix
    ix.__dict__.pop("_materials", None)
    try:
        file_order, _, m = cr_eval._hybrid_orders(query, ix, scores, service.config)
        bm25, emb = list(m.bm25_chunkagg_file_order), list(m.emb_file_order)
    finally:
        ix.__dict__.pop("_materials", None)
    paths = ix.snap.paths
    hits, _ = index.search(q.problem_statement, TOP)
    hybrid = [paths[int(i)] for i in file_order]
    if [h.path for h in hits] != hybrid[:TOP]:
        raise SystemExit("hybrid order differs from TaskIndex.search")
    return {"bm25": [paths[int(i)] for i in bm25], "embedding": [paths[int(i)] for i in emb], "hybrid": hybrid,
            "hits": hits, "query_vector": source, "n_files": len(paths)}


def _rank(order: list[str], path: str):
    try:
        return order.index(path) + 1
    except ValueError:
        return None


def issue_section(q) -> list[str]:
    text = q.problem_statement[:ISSUE_CHARS]
    more = "……（截断，共 %d 字）" % len(q.problem_statement) if len(q.problem_statement) > ISSUE_CHARS else ""
    fence = "`" * max(3, max((len(run) for run in re.findall(r"`+", text)), default=0) + 1)
    return ["## Issue", "", f"- 题号：`{q.instance_id}`", f"- 仓库：`{q.repo}`", f"- base_commit：`{q.base_commit}`", "",
            fence + "text", text + more, fence, ""]


def retrieval_section(r: dict, gold: set | None) -> list[str]:
    mark = (lambda p: " ★" if p in gold else "") if gold is not None else (lambda p: "")
    lines = ["## 检索（查询 = issue 全文）", "", SOURCE_NOTE + f" 快照共 {r['n_files']} 个文件。", ""]
    lines += ["| 名次 | BM25 | embedding | hybrid |", "|---:|---|---|---|"]
    for i in range(TOP):
        cells = [f"`{r[k][i]}`{mark(r[k][i])}" if i < len(r[k]) else "" for k in ("bm25", "embedding", "hybrid")]
        lines.append(f"| {i + 1} | " + " | ".join(cells) + " |")
    lines += ["", "hybrid 前 5 个文件各自的前 2 个块（即 `retrieve_code` 返回的内容）：", ""]
    for n, hit in enumerate(r["hits"], 1):
        lines.append(f"{n}. `{hit.path}`{mark(hit.path)}")
        for c in hit.chunks:
            lines.append(f"   - `{c.symbol}`，第 {c.start_line}–{c.end_line} 行")
    lines.append("")
    if gold is not None:
        lines += ["## Gold 文件（--show-gold）", "", "| gold 文件 | BM25 名次 | embedding 名次 | hybrid 名次 |", "|---|---:|---:|---:|"]
        for path in sorted(gold):
            ranks = [_rank(r[k], path) for k in ("bm25", "embedding", "hybrid")]
            lines.append(f"| `{path}` | " + " | ".join(str(x) if x else "不在快照" for x in ranks) + " |")
        lines += ["", "★ 标记 gold 文件。名次从 1 开始。", ""]
    return lines


def _arg_summary(name: str, arguments: dict) -> str:
    if name == "run_command":
        return str(arguments.get("command", ""))[:ARG_CHARS]
    if name == "retrieve_code":
        return "query: " + str(arguments.get("query", ""))[:ARG_CHARS] + ("，k=%s" % arguments["k"] if "k" in arguments else "")
    if name == "search_code":
        return "pattern: " + str(arguments.get("pattern", ""))[:ARG_CHARS] + (" in " + str(arguments["path"]) if arguments.get("path") else "")
    if "path" in arguments:
        extra = [f"{k}={arguments[k]}" for k in ("offset", "limit") if k in arguments]
        return str(arguments["path"]) + (" (" + ", ".join(extra) + ")" if extra else "")
    return json.dumps(arguments, ensure_ascii=False)[:ARG_CHARS]


def trajectory(run_dir: Path) -> list[dict]:
    from scripts.analyze_cr2 import _read_jsonl, steps

    events = _read_jsonl(run_dir / "events.jsonl")
    success = {}
    for event in events:
        if event.get("event_type") == "tool.result":
            payload = event.get("payload") or {}
            success[payload.get("call_id")] = payload.get("success")
    rows = []
    for step, event in steps(events):
        if event.get("event_type") != "tool.intent":
            continue
        payload = event.get("payload") or {}
        name = payload.get("name")
        arguments = payload.get("arguments") or {}
        rows.append({"step": step, "tool": name, "args": _arg_summary(name, arguments),
                     "path": arguments.get("path") if name in PATH_TOOLS else None,
                     "success": success.get(event.get("tool_call_id"))})
    return rows


def run_section(run_dir: Path, gold: set | None) -> list[str]:
    from scripts.analyze_cr2 import _read_jsonl, normalize_path

    mark = (lambda p: " ★" if normalize_path(p) in gold else "") if gold is not None else (lambda p: "")
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    lines = ["## 运行", "", f"- run 目录：`{_rel(run_dir)}`", f"- 变体：`{summary.get('variant')}`，模型调用 {summary.get('model_calls')} 次，"
             f"停止原因 `{summary.get('stop_reason')}`，费用 {summary.get('cost_cny')} 元", ""]
    lines += ["### 轨迹", "", "| 步 | 工具 | 参数摘要 | 成功 |", "|---:|---|---|---|"]
    for row in trajectory(run_dir):
        ok = {True: "是", False: "否"}.get(row["success"], "—")
        star = mark(row["path"]) if row["path"] else ""
        lines.append(f"| {row['step']} | `{row['tool']}` | {_cell(row['args'])}{star} | {ok} |")
    lines.append("")
    calls = _read_jsonl(run_dir / "retrieval-calls.jsonl")
    lines += ["### retrieve_code 的返回", ""]
    if calls:
        for n, call in enumerate(calls, 1):
            lines.append(f"{n}. query：{_cell(str(call.get('query', ''))[:200])}（k={call.get('k')}）")
            for item in call.get("files") or []:
                chunks = "；".join(f"`{c[0]}` {c[1]}–{c[2]}" for c in item.get("chunks") or [])
                lines.append(f"   - `{item['path']}`{mark(item['path'])}：{chunks}")
    else:
        lines.append("本次运行没有调用 `retrieve_code`。")
    lines.append("")
    if summary.get("variant") == "CR2-P" and PREFETCH.is_file():
        rows = {row["task"]: row for row in json.loads(PREFETCH.read_text(encoding="utf-8"))["tasks"]}
        row = rows.get(summary.get("task_id"))
        if row:
            lines += ["### P 组预取列表（附在首条消息之后）", ""]
            lines += [f"{n}. `{item['path']}`{mark(item['path'])}" for n, item in enumerate(row["files"], 1)]
            lines.append("")
    changed = summary.get("changed_files") or []
    lines += ["### 最终改动的文件", ""]
    lines += [f"- `{p}`{mark(p)}" for p in changed] or ["（没有改动）"]
    lines += ["", "### grader 结论", "",
              f"- 解出：{'是' if summary.get('solved') else '否'}（status `{summary.get('status')}`，grader_status `{summary.get('grader_status')}`）", ""]
    return lines


def render(instance_id: str, run_dir: Path | None, show_gold: bool, service=None) -> str:
    q = query_view(instance_id)
    if run_dir is not None:
        summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
        if summary.get("task_id") != instance_id:
            raise SystemExit(f"{_rel(run_dir)} is a run of {summary.get('task_id')}, not {instance_id}")
    gold = None
    if show_gold:
        from scripts.analyze_cr2 import load_gold, normalize_path

        gold = {normalize_path(p) for p in load_gold([instance_id])[instance_id]}
    if service is None:
        from src.retrieval.serve import RetrievalService

        service = RetrievalService.from_freeze()
    lines = [f"# 代码检索演示：{instance_id}", "",
             "由 `scripts/cr_demo.py` 离线生成，不调用模型。" + (f"运行：`{_rel(run_dir)}`。" if run_dir else ""), ""]
    lines += issue_section(q)
    lines += retrieval_section(rankings(service, q), gold)
    if run_dir is not None:
        lines += run_section(run_dir, gold)
    return "\n".join(lines).rstrip() + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("instance_id")
    parser.add_argument("--run", type=Path, help="a run directory (with summary.json and events.jsonl)")
    parser.add_argument("--show-gold", action="store_true", help="mark the gold files and their ranks")
    parser.add_argument("--out", type=Path, help="write the Markdown here instead of stdout")
    args = parser.parse_args()
    run_dir = args.run if args.run is None or args.run.is_absolute() else ROOT / args.run
    text = render(args.instance_id, run_dir, args.show_gold)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8", newline="\n")
        print("wrote", _rel(args.out))
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
