"""Agent workspace seeded from a baseline image. Deltas keep every edit, not a gold allowlist."""
from __future__ import annotations

import json
from pathlib import PurePosixPath

from src.execution.docker import DockerExecutionBackend

DELTA_FILE_LIMIT = 200
DELTA_BYTE_LIMIT = 4 * 1024 * 1024

COPY_REPO = "umask 000 && cp -R /opt/repo/. /workspace/"
LINK_NATIVE = r'''
import hashlib, json, pathlib, tarfile
root = pathlib.Path("/opt/repo")
workspace = pathlib.Path("/workspace")
manifest = pathlib.Path("/opt/native-artifacts.json")
linked = []
if manifest.exists():
    listed = json.loads(manifest.read_text())
    for rel in listed:
        source = root / rel
        target = workspace / rel
        if not source.is_file():
            raise ValueError("native artifact missing: " + rel)
        if target.is_symlink() or target.read_bytes() != source.read_bytes():
            raise ValueError("generated native copy mismatch")
        target.unlink()
        target.symlink_to(source)
        linked.append({"path": rel, "image_path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()})
else:
    archive = pathlib.Path("/opt/known-good.tar.gz")
    if not archive.exists():
        archive = pathlib.Path("/opt/baseline.tar.gz")
    if not archive.exists():
        if any(root.rglob("*.so")):
            raise ValueError("native artifact basis missing")
        pathlib.Path("/tmp/a5-native-links.json").write_text("[]")
        raise SystemExit(0)
    with tarfile.open(archive) as stream:
        source_names = {member.name for member in stream}
    for path in root.rglob("*.so"):
        rel = path.relative_to(root).as_posix()
        if rel in source_names:
            continue
        target = workspace / rel
        if target.is_symlink() or target.read_bytes() != path.read_bytes():
            raise ValueError("generated native copy mismatch")
        target.unlink()
        target.symlink_to(path)
        linked.append({"path": rel, "image_path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
pathlib.Path("/tmp/a5-native-links.json").write_text(json.dumps(linked))
'''

_SKIP_DIRS = frozenset({"__pycache__", ".pytest_cache", "build"})
_SKIP_SUFFIXES = (".pyc", ".pyo", ".so", ".pyd", ".o", ".dylib")
_SKIP_NAMES = frozenset({".coverage"})


def is_export_skipped(relative: str, original_files=None) -> bool:
    """Compiled artifacts and caches stay out of the delta. Source files do not.

    original_files: with export_keep_original_files, the paths of the original source tree among the
    candidates; those are never skipped, even under a directory named build."""
    if original_files is not None and relative in original_files:
        return False
    parts = PurePosixPath(relative).parts
    name = parts[-1] if parts else relative
    if any(part in _SKIP_DIRS or part.endswith(".egg-info") for part in parts):
        return True
    return name.endswith(_SKIP_SUFFIXES) or name in _SKIP_NAMES or name.endswith(".egg-info")


EXPORT_DELTA = r'''
import base64, json, pathlib, sys
root = pathlib.Path("/opt/repo")
work = pathlib.Path("/workspace")
skip_dirs = {"__pycache__", ".pytest_cache", "build"}
skip_names = {".coverage"}
skip_suffixes = (".pyc", ".pyo", ".so", ".pyd", ".o", ".dylib")
keep_original = False
# Files the image added to /opt/repo beyond the source archive; everything else there is original source.
extra = set(json.loads(pathlib.Path("/opt/repo-extra-files.json").read_text())) if keep_original else set()

def skipped(rel):
    if keep_original and rel.as_posix() not in extra and (root / rel).is_file() and not (root / rel).is_symlink():
        return False
    if any(part in skip_dirs or part.endswith(".egg-info") for part in rel.parts):
        return True
    return rel.name.endswith(skip_suffixes) or rel.name in skip_names or rel.name.endswith(".egg-info")

changed = {}
seen = set()
skipped_files = set()
total = 0
for path in work.rglob("*"):
    if not path.is_file() and not path.is_symlink():
        continue
    rel = path.relative_to(work)
    if path.is_symlink() or skipped(rel):
        if skipped(rel) or path.suffix in skip_suffixes:
            skipped_files.add(rel.as_posix())
        continue
    key = rel.as_posix()
    seen.add(key)
    data = path.read_bytes()
    original = root / rel
    if original.is_file() and not original.is_symlink() and original.read_bytes() == data:
        continue
    total += len(data)
    changed[key] = base64.b64encode(data).decode("ascii")
deleted = []
for path in root.rglob("*"):
    if path.is_symlink() or not path.is_file():
        continue
    rel = path.relative_to(root)
    key = rel.as_posix()
    if skipped(rel):
        skipped_files.add(key)
        continue
    if key not in seen:
        deleted.append(key)
if len(changed) + len(deleted) > 200 or total > 4 * 1024 * 1024:
    sys.stderr.write("delta_too_large\n")
    raise SystemExit(2)
json.dump({"changed": changed, "deleted": sorted(deleted), "skipped_files": len(skipped_files)}, sys.stdout)
'''


def export_script(repo_root: str, keep_original_files: bool = False) -> str:
    code = EXPORT_DELTA.replace('pathlib.Path("/opt/repo")', 'pathlib.Path(' + json.dumps(repo_root) + ')')
    if keep_original_files:
        code = code.replace("keep_original = False", "keep_original = True", 1)
    return code


class BaselineImageBackend(DockerExecutionBackend):
    def __init__(self, *, repo_root="/opt/repo", python_bin="/usr/local/bin/python", keep_original_files=False,
                 **kwargs):
        super().__init__(python_bin=python_bin, **kwargs)
        self.repo_root = repo_root
        self.python_bin = python_bin
        self.keep_original_files = keep_original_files
        self.candidate_delta = None
        self.snapshot_error = None
        self.export_skipped_files = None

    @property
    def capabilities(self):
        return {**super().capabilities, "transport": "baseline_image_full_delta"}

    async def start(self, files=None):
        if files:
            raise ValueError("baseline backend seeds from the image only")
        try:
            await super().start({})
            copy = COPY_REPO.replace("/opt/repo", self.repo_root)
            link = LINK_NATIVE.replace('pathlib.Path("/opt/repo")', 'pathlib.Path(' + json.dumps(self.repo_root) + ')')
            await self._ok("exec", self.name, "/bin/sh", "-c", copy, limit=64 * 1024, timeout=180, op="exec copy_repo")
            await self._ok("exec", self.name, self.python_bin, "-I", "-S", "-c", link, limit=64 * 1024, timeout=120, op="exec link_native")
        except BaseException:
            await self.close()
            raise

    async def export_delta(self):
        try:
            raw = await self._ok(
                "exec", "-i", self.name, self.python_bin, "-I", "-S", "-c",
                export_script(self.repo_root, self.keep_original_files),
                data=b"{}", limit=DELTA_BYTE_LIMIT + 1024 * 1024, timeout=120, op="snapshot",
            )
        except Exception as exc:
            text = str(exc)
            if "delta_too_large" in text:
                raise ValueError("delta_too_large") from exc
            raise
        delta = json.loads(raw)
        count = len(delta.get("changed") or {}) + len(delta.get("deleted") or [])
        if count > DELTA_FILE_LIMIT:
            raise ValueError("delta_too_large")
        self.candidate_delta = delta
        self.export_skipped_files = int(delta.get("skipped_files") or 0)
        return delta

    async def close(self, *, snapshot=False):
        if snapshot and self.active:
            try:
                await self.export_delta()
                self.snapshot = {}
            except (Exception, KeyboardInterrupt) as exc:
                self.snapshot_error = type(exc).__name__ + ": " + str(exc)
                if isinstance(exc, KeyboardInterrupt):
                    raise
        return await super().close(snapshot=False)
