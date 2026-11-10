"""Gold-patch "where to change" references for the 30 FIX-12 pilot tasks (design §5.3, adaptation 1).

Read-only, no model calls, no Docker, no run data. Files are the non-test files gold.patch touches. For each hunk in
a Python file, every changed baseline line (a removed line, or for an insertion the pair of baseline lines around it)
is mapped to the innermost def or class whose span in the baseline source contains it, decorators included. Lines
outside every def and class count for the file only.

granularity: "def" when at least one reference is a def, "class" when only classes are found, "file" otherwise.
Writes out/references.json with the sha256 of each gold.patch and baseline file used.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HARNESS = HERE.parents[2]
sys.path.insert(0, str(HERE))
import rules  # noqa: E402

WORK = HARNESS / "runs" / "rebench" / "work"
MANIFEST = HARNESS / "configs" / "rebench" / "unleash-pilot-tasks.yaml"
OUT = HERE / "out"
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")


def task_ids() -> list[str]:
    import yaml

    return [t["id"] for t in yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))["tasks"]]


def sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def file_hunks(patch: str) -> dict[str, list[tuple[int, int]]]:
    """Path -> changed baseline positions as (first, last) line pairs: (k, k) for a removed line k, (k, k + 1) for
    lines inserted between baseline lines k and k + 1."""
    out: dict[str, list[tuple[int, int]]] = {}
    path, old = None, 0
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/(\S+) b/", line)
            path = m.group(1) if m else None
            out.setdefault(path, [])
        elif (m := HUNK.match(line)) and path:
            old = int(m.group(1))
            if m.group(2) == "0":
                old += 1
        elif path is None or line.startswith(("---", "+++", "index ", "new file", "deleted file", "\\")):
            continue
        elif line.startswith("-"):
            out[path].append((old, old))
            old += 1
        elif line.startswith("+"):
            out[path].append((old - 1, old))
        elif line.startswith(" "):
            old += 1
    return out


def spans(source: str) -> list[tuple[int, int, str, str]]:
    """(first line, last line, kind, qualified name) for every def and class."""
    found = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + child.name
                first = min([child.lineno] + [d.lineno for d in child.decorator_list])
                found.append((first, child.end_lineno, "class" if isinstance(child, ast.ClassDef) else "def", name))
                visit(child, name + ".")
            else:
                visit(child, prefix)

    visit(ast.parse(source), "")
    return found


def enclosing(nodes: list[tuple[int, int, str, str]], first: int, last: int) -> tuple[str, str] | None:
    inside = [n for n in nodes if n[0] <= first and last <= n[1]]
    if not inside:
        return None
    best = min(inside, key=lambda n: n[1] - n[0])
    return best[2], best[3]


def task_reference(task: str) -> dict:
    staging = WORK / task / "staging"
    hunks = file_hunks((staging / "gold.patch").read_text(encoding="utf-8", errors="replace"))
    files, refs, hashes = [], [], {}
    for path, positions in hunks.items():
        if rules.is_test_path(path):
            continue
        code = path.endswith(".py")
        files.append(dict(path=path, code=code))
        base = staging / "baseline" / path
        hashes[path] = sha256(base)
        if not code or not base.is_file():
            continue
        source = base.read_text(encoding="utf-8")
        nodes = spans(source)
        last_line = len(source.splitlines())
        seen = set()
        for first, last in positions:
            hit = enclosing(nodes, max(first, 1), min(last, last_line))
            if hit and hit not in seen:
                seen.add(hit)
                refs.append(dict(file=path, kind=hit[0], qualname=hit[1], name=hit[1].rsplit(".", 1)[-1]))
    kinds = {r["kind"] for r in refs}
    granularity = "def" if "def" in kinds else "class" if kinds else "file"
    return dict(files=files, refs=refs, granularity=granularity, reliability="gold",
                gold_patch_sha256=sha256(staging / "gold.patch"), baseline_sha256=hashes)


def main() -> None:
    refs = {t: task_reference(t) for t in task_ids()}
    OUT.mkdir(exist_ok=True)
    (OUT / "references.json").write_text(json.dumps(dict(schema="fix12-gold-references.v1", tasks=refs),
                                                    ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for t, r in refs.items():
        print(f"{t:50s} {r['granularity']:5s} {[f['path'] for f in r['files'] if f['code']]} "
              f"{[(x['kind'], x['qualname']) for x in r['refs']]}")


if __name__ == "__main__":
    main()
