"""Code run by the image's own Python must work on Python 3.6 (the oldest clean-image base)."""
import ast
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PY36_IMAGE = "python:3.6.15-slim-buster"

# Modules on the A5-main path (tools, backend, grader, dispatcher, pre-run checks)
# whose inline Python code is sent to a container.
MODULES = [
    "src/tools/container_files.py",
    "src/tools/search.py",
    "src/execution/docker.py",
    "src/execution/baseline_image_backend.py",
    "src/eval/image_grader.py",
    "src/policy/dispatcher.py",
    "scripts/preflight_a5_repo_baseline.py",
    "scripts/build_clean_baseline.py",
    "scripts/rewritten_sources.py",
    "scripts/rebench/provenance.py",
]
# Uploaded to /tmp/a5-runner.py and run as a whole file.
WHOLE_FILES = ["scripts/a5_django_observer.py"]
CONSTANTS = {"scripts/preflight_a5_repo_baseline.py": ["PYTEST_OBSERVER"]}

PY_FLAGS = {"-I", "-S", "-B"}


def _resolve(node, tree, seen=frozenset()):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return ["".join(v.value if isinstance(v, ast.Constant) else "x" for v in node.values)]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "replace":
        return _resolve(node.func.value, tree, seen)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id not in seen:
        found = []
        for item in ast.walk(tree):
            if isinstance(item, ast.FunctionDef) and item.name == node.func.id:
                for ret in ast.walk(item):
                    if isinstance(ret, ast.Return) and ret.value is not None:
                        found += _resolve(ret.value, tree, seen | {node.func.id})
        return found
    if isinstance(node, ast.Name) and node.id not in seen:
        found = []
        for item in ast.walk(tree):
            if isinstance(item, ast.Assign) and any(isinstance(t, ast.Name) and t.id == node.id for t in item.targets):
                found += _resolve(item.value, tree, seen | {node.id})
        return found
    return []


def _sequences(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)):
            yield node.elts
        elif isinstance(node, ast.Call):
            yield node.args


def container_sources():
    """Map a readable label to each piece of Python source that runs inside a container."""
    out, unresolved = {}, []
    for rel in MODULES:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        hits = []
        for elts in _sequences(tree):
            for i in range(1, len(elts) - 1):
                prev, cur = elts[i - 1], elts[i]
                if (isinstance(cur, ast.Constant) and cur.value == "-c"
                        and isinstance(prev, ast.Constant) and prev.value in PY_FLAGS):
                    hits.append(elts[i + 1])
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "docker_python" and len(node.args) > 1:
                hits.append(node.args[1])
        for name in CONSTANTS.get(rel, []):
            hits.append(ast.Name(id=name))
        for hit in hits:
            texts = _resolve(hit, tree)
            if not texts:
                unresolved.append(f"{rel}:{getattr(hit, 'lineno', '?')}")
            for text in texts:
                out.setdefault(f"{rel}#{len([k for k in out if k.startswith(rel)])}", text)
    for rel in WHOLE_FILES:
        out[rel] = (ROOT / rel).read_text(encoding="utf-8")
    deduped, seen = {}, set()
    for key, text in out.items():
        if text not in seen:
            seen.add(text)
            deduped[key] = text
    return deduped, unresolved


FORBIDDEN = [
    (r"\.is_relative_to\(", "Path.is_relative_to (3.9)"),
    (r"capture_output\s*=", "subprocess capture_output (3.7)"),
    (r"\btext\s*=\s*True", "subprocess text= (3.7)"),
    (r"\.removeprefix\(|\.removesuffix\(", "str.removeprefix/removesuffix (3.9)"),
    (r"functools\.cache\b|from functools import cache\b", "functools.cache (3.9)"),
    (r"from __future__ import annotations", "postponed annotations (3.7)"),
    (r"\bdataclasses\b", "dataclasses (3.7)"),
    (r"asyncio\.run\(", "asyncio.run (3.7)"),
    (r"\bbreakpoint\(", "breakpoint (3.7)"),
    (r"time\.time_ns|time\.monotonic_ns", "*_ns clocks (3.7)"),
    (r"contextlib\.nullcontext", "nullcontext (3.7)"),
    (r"math\.(prod|isqrt|dist|comb|perm)\(", "math additions (3.8)"),
    (r"shlex\.join\(", "shlex.join (3.8)"),
    (r"\.unlink\(\s*missing_ok", "Path.unlink(missing_ok) (3.8)"),
    (r"dirs_exist_ok", "copytree(dirs_exist_ok) (3.8)"),
    (r"\.readlink\(\)", "Path.readlink (3.9)"),
    (r"zoneinfo|graphlib", "3.9 modules"),
]
BUILTIN_GENERICS = {"list", "dict", "tuple", "set", "frozenset", "type"}


def _syntax_problems(text):
    tree = ast.parse(text)
    bad = []
    for node in ast.walk(tree):
        if type(node).__name__ == "NamedExpr":
            bad.append("walrus := (3.8)")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            sides = (node.left, node.right)
            if any(isinstance(s, (ast.Dict, ast.DictComp)) or (isinstance(s, ast.Call) and getattr(s.func, "id", None) == "dict") for s in sides):
                bad.append("dict | dict (3.9)")
        if isinstance(node, ast.arguments) and getattr(node, "posonlyargs", []):
            bad.append("positional-only parameters (3.8)")
        annotations = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            annotations += [a.annotation for a in node.args.args + node.args.kwonlyargs if a.annotation] + [node.returns]
        if isinstance(node, ast.AnnAssign):
            annotations.append(node.annotation)
        for ann in annotations:
            if isinstance(ann, ast.Subscript) and isinstance(ann.value, ast.Name) and ann.value.id in BUILTIN_GENERICS:
                bad.append(f"runtime {ann.value.id}[...] annotation (3.9)")
    return bad


def test_collector_finds_every_container_program():
    sources, unresolved = container_sources()
    assert unresolved == []
    joined = "\n".join(sources.values())
    for marker in [
        "file_limit",                   # FILE_CODE (read/list/write/edit/search_replace)
        "more matches omitted",         # SEARCH_CODE
        "time.sleep(604800)",           # container entrypoint
        "a5-native-links.json",         # LINK_NATIVE / prepare_repo_runtime
        "delta_too_large",              # EXPORT_DELTA
        "req.get(\"overlay\")",         # grader APPLY
        "a5-pytest-receipt.json",       # receipt read
        "dispatcher tool denied",       # B2 dispatcher RPC
        "/tmp/a5-runner.py",            # runner upload
        "/opt/repo",                    # source-only check
    ]:
        assert marker in joined, marker
    assert len(sources) >= 15


def test_container_code_avoids_python37_apis():
    sources, _ = container_sources()
    problems = []
    for label, text in sources.items():
        for pattern, why in FORBIDDEN:
            if re.search(pattern, text):
                problems.append(f"{label}: {why}")
        problems += [f"{label}: {why}" for why in _syntax_problems(text)]
    assert problems == []


def test_scan_rejects_known_bad_snippets():
    bad = "import pathlib\nif not pathlib.Path('/a').is_relative_to('/'): pass\n"
    assert any(re.search(p, bad) for p, _ in FORBIDDEN)
    assert "walrus := (3.8)" in _syntax_problems("if (n := 1): pass\n")
    assert "dict | dict (3.9)" in _syntax_problems("x = {} | {'a': 1}\n")
    assert _syntax_problems("def f(a: list[str]) -> None: pass\n")


def _py36_image_available():
    if not shutil.which("docker"):
        return False
    probe = subprocess.run(["docker", "--context", "desktop-linux", "image", "inspect", PY36_IMAGE],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return probe.returncode == 0


COMPILE_ALL = r'''
import json, os, py_compile, sys, tempfile
sources = json.load(sys.stdin)
errors = {}
work = tempfile.mkdtemp()
for i, (label, text) in enumerate(sorted(sources.items())):
    path = os.path.join(work, "m%d.py" % i)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        py_compile.compile(path, doraise=True)
    except py_compile.PyCompileError as exc:
        errors[label] = str(exc)[-400:]
json.dump({"version": list(sys.version_info[:3]), "count": len(sources), "errors": errors}, sys.stdout)
'''


@pytest.mark.skipif(not _py36_image_available(), reason="docker or python:3.6.15-slim-buster not available")
def test_container_code_compiles_on_python36():
    sources, _ = container_sources()
    proc = subprocess.run(
        ["docker", "--context", "desktop-linux", "run", "--rm", "-i", "--pull=never", "--network", "none",
         PY36_IMAGE, "python", "-I", "-S", "-c", COMPILE_ALL],
        input=json.dumps(sources).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-1000:]
    report = json.loads(proc.stdout)
    assert report["version"][:2] == [3, 6]
    assert report["count"] == len(sources)
    assert report["errors"] == {}


RUN_CASES = r'''
import json, os, subprocess, sys
job = json.load(sys.stdin)
os.makedirs("/workspace/pkg", exist_ok=True)
with open("/workspace/pkg/a.txt", "w") as f:
    f.write("hello\nneedle here\n")
os.symlink("/etc", "/workspace/link")
out = []
for code, req in job:
    p = subprocess.run([sys.executable, "-I", "-S", "-c", code], input=json.dumps(req),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    out.append([p.returncode, p.stdout[-400:], p.stderr[-400:]])
json.dump(out, sys.stdout)
'''


@pytest.mark.skipif(not _py36_image_available(), reason="docker or python:3.6.15-slim-buster not available")
def test_file_and_search_tools_run_on_python36_and_still_deny_escapes():
    from src.tools.container_files import FILE_CODE
    from src.tools.search import SEARCH_CODE

    def file_req(name, path, **params):
        return {"name": name, "params": {"path": path, **params}, "file_limit": 1 << 20,
                "line_limit": 100, "char_limit": 10000}
    cases = [
        [FILE_CODE, file_req("read_file", "pkg/a.txt")],
        [FILE_CODE, file_req("list_dir", "pkg")],
        [FILE_CODE, file_req("search_replace", "pkg/a.txt", search="hello", replace="bye")],
        [FILE_CODE, file_req("read_file", "link/passwd")],
        [SEARCH_CODE, {"pattern": "needle", "path": "pkg"}],
        [SEARCH_CODE, {"pattern": "root", "path": "link"}],
    ]
    proc = subprocess.run(
        ["docker", "--context", "desktop-linux", "run", "--rm", "-i", "--pull=never", "--network", "none",
         PY36_IMAGE, "python", "-I", "-S", "-c", RUN_CASES],
        input=json.dumps(cases).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-1000:]
    read, listing, edit, escape_read, search, escape_search = json.loads(proc.stdout)
    assert read[0] == 0 and "1|hello" in read[1]
    assert listing[0] == 0 and "[F] a.txt" in listing[1]
    assert edit[0] == 0 and "file updated" in edit[1]
    assert escape_read[0] != 0 and "path denied" in escape_read[2]
    assert search[0] == 0 and "needle here" in search[1]
    assert escape_search[0] != 0 and "path denied" in escape_search[2]
    for result in (escape_read, escape_search):
        assert "AttributeError" not in result[2]
