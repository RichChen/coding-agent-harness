"""Atomic, content-addressed host artifacts with ownership and disk quotas."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from src.tracing.store import StoreError, digest


class ArtifactQuotaError(StoreError):
    """A declared capacity refusal; the store itself is still usable."""


def atomic_replace(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".publish-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ArtifactStore:
    def __init__(self, root: Path, run_id: str, *, limit_bytes=16 * 1024 * 1024,
                 item_limit_bytes=2 * 1024 * 1024, readonly=False):
        self.root = Path(root).resolve()
        self.run_id = run_id
        self.limit_bytes, self.item_limit_bytes = limit_bytes, item_limit_bytes
        self.readonly = readonly
        self.reserve_bytes = 0  # Opt-in workspace finalization; legacy default is zero.
        if not readonly:
            self.root.mkdir(mode=0o700, exist_ok=True)

    def publish(self, content: bytes, media_type="application/octet-stream"):
        if self.readonly:
            raise StoreError("read-only artifact store")
        if len(content) > self.item_limit_bytes:
            raise ArtifactQuotaError("artifact item quota exceeded")
        checksum = digest(content)
        path = self.root / checksum
        ref = dict(logical_id=checksum, sha256=checksum, size_bytes=len(content),
                   media_type=media_type, run_id=self.run_id, path=checksum, sensitive=True)
        if path.exists():
            self.read(ref)
            return ref
        # Include orphaned objects AND interrupted publications; never delete evidence automatically.
        if self.used_bytes() + len(content) + self.reserve_bytes > self.limit_bytes:
            raise ArtifactQuotaError("artifact disk quota exceeded (including orphans)")
        atomic_replace(path, content)
        return ref

    def used_bytes(self):
        used = sum(p.stat().st_size for p in self.root.iterdir() if p.is_file())
        commands = self.root.parent / "command-output"
        if commands.exists():
            used += sum(p.stat().st_size for p in commands.rglob("*") if p.is_file())
        return used

    def read(self, ref):
        checksum = ref.get("sha256", "")
        if (ref.get("run_id") != self.run_id or ref.get("path") != checksum
                or len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum)):
            raise StoreError("artifact ownership/path invalid")
        path = self.root / checksum
        if path.is_symlink() or path.resolve().parent != self.root or not path.is_file():
            raise StoreError("artifact missing or path replaced")
        size = path.stat().st_size
        if size != ref.get("size_bytes") or size > self.item_limit_bytes:
            raise StoreError("artifact size mismatch")
        data = path.read_bytes()
        if digest(data) != checksum:
            raise StoreError("artifact hash mismatch")
        return data
