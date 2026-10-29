"""Shared, deliberately artificial C0/C1/C2 fixture for workspace consumers.

Load with importlib.util.spec_from_file_location or runpy.run_path. This is not a
capture provider. Expected IDs use independent JSON/hashlib construction, never
the production identity helpers. All stored strings are encoded directly as UTF-8.
"""
import hashlib
import json

from src.workspace.contracts import WorkspaceCapture


BASE_FILES = {
    "pkg/calc.py": b"def add(a, b):\n    return 0\n",
    "tests/test_calc.py": b"from pkg.calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
    "pytest.ini": b"[pytest]\ntestpaths = tests\n",
    "data/rules.json": b'{"operation":"add"}\n',
}
RULES = {
    "version": "fixture-v1", "editable": ["pkg/*.py"],
    "excluded_directories": [".git", "__pycache__", ".pytest_cache"],
    "excluded_files": [".coverage", "*.pyc", "*.pyo"],
    "reject_mode_changes": True, "max_changed_files": 200,
    "max_changed_bytes": 4194304, "max_manifest_entries": 10000,
    "max_base_bytes": 67108864, "max_corpus_bytes": 8388608,
    "frozen": ["tests/**", "pytest.ini", "data/**"],
    "path_policy": "workspace-v1", "kind_policy": "regular-only",
}
CORPUS_RULES = {"version": "fixture-v1", "suffix": ".py", "max_file_bytes": 1048576,
                "decode": "pep263", "decode_error": "exclude", "ast_error": "window"}
INDEX_CONFIG = {
    "chunker": {"version": "fixture-window-v1", "lines": 40},
    "tokenizer": {"version": "fixture-words-v1"},
    "encoder": {"model": "fixture", "revision": "1", "preprocessing": "raw-v1",
                "max_len": 128, "precision": "float32"},
    "bm25": {"k1": "1.5", "b": "0.75"},
    "rrf": {"k": 60, "weights": ["1", "1"]}, "mode": "hybrid",
}


def reference_id(domain, value):
    wire = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")
    return domain + ":" + hashlib.sha256(domain.encode("ascii") + b"\0" + wire).hexdigest()


def reference_entries(files, modes=None):
    return [dict(path=path, kind="regular", mode=(modes or {}).get(path, 0o644),
                 size=len(files[path]), sha256=hashlib.sha256(files[path]).hexdigest())
            for path in sorted(files, key=lambda path: path.encode("utf-8"))]


class FixtureBlobs:
    def __init__(self):
        self.blobs = {}

    def add(self, data):
        checksum = hashlib.sha256(data).hexdigest()
        key = "blobs/" + checksum
        self.blobs[key] = data
        return dict(sha256=checksum, size=len(data), key=key)

    def read(self, ref):
        return self.blobs[ref.key]


def build_capture(files=None, *, modes=None, violations=(), number=0, blobs=None):
    """Return (capture, controlled in-memory blobs); input is the complete tree."""
    files = dict(BASE_FILES if files is None else files)
    blobs = blobs if blobs is not None else FixtureBlobs()
    entries = reference_entries(files, modes)
    base_id = reference_id("tree-v1", reference_entries(BASE_FILES))
    manifest_id = reference_id("tree-v1", entries)
    policy_id = reference_id("rules-v1", RULES)
    identity = dict(base_revision="fixture-base", image_digest="sha256:" + "1" * 64,
                    base_manifest_id=base_id, manifest_id=manifest_id, rules_id=policy_id)
    raw = dict(schema_version="workspace-v1", capture_id=f"00000000-0000-4000-8000-{number:012d}",
               origin={"run": "fixture", "container": "fixture-only", "workspace": "/workspace"},
               **identity, candidate_id=reference_id("candidate-v1", identity), entries=entries,
               changed=[[path, blobs.add(data)] for path, data in files.items()
                        if path not in BASE_FILES or data != BASE_FILES[path]],
               deleted=sorted(set(BASE_FILES) - set(files)),
               corpus_blobs=[[path, blobs.add(data)] for path, data in files.items()
                             if path.endswith(".py")],
               excluded=[], admissible=not violations, violations=list(violations),
               captured_at="2026-10-30T00:00:00Z", barrier_receipt=blobs.add(b"fixture barrier"))
    return WorkspaceCapture.from_dict(raw), blobs


def build_sequence():
    """C0 returns 0, C1 returns a-b, C2 returns a+b; tests/config/data frozen."""
    blobs = FixtureBlobs()
    captures = {}
    for number, (name, body) in enumerate((("C0", b"0"), ("C1", b"a - b"), ("C2", b"a + b"))):
        files = dict(BASE_FILES)
        files["pkg/calc.py"] = b"def add(a, b):\n    return " + body + b"\n"
        captures[name], _ = build_capture(files, number=number, blobs=blobs)
    return captures, blobs
