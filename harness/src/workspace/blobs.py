"""A dedicated content-addressed store; capture reference is the commit point.

The root is a trusted, host-owned directory, never supplied by a model. Interrupted
publications may leave unreferenced blobs; they never expose a partial capture.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from src.workspace.contracts import ArtifactRef, WorkspaceCapture, blob_sha256
from src.workspace.control import CaptureError


class WorkspaceBlobStore:
    def __init__(self, root: Path, *, limit_bytes=128 * 1024 * 1024):
        self.root = Path(root).absolute()
        if self.root.is_symlink():
            raise CaptureError("artifact_store_unsafe")
        self.root.mkdir(parents=True, exist_ok=True)
        self.limit_bytes = limit_bytes
        for name in ("blobs", "captures"):
            directory = self.root / name
            if directory.is_symlink():
                raise CaptureError("artifact_store_unsafe")
            directory.mkdir(exist_ok=True)

    def _path(self, ref):
        if ref.key != "blobs/" + ref.sha256:
            raise CaptureError("artifact_reference_invalid")
        path = self.root / ref.key
        if path.is_symlink() or path.parent.is_symlink():
            raise CaptureError("artifact_store_unsafe")
        return path

    def read(self, ref: ArtifactRef) -> bytes:
        path = self._path(ref)
        try:
            with path.open("rb") as stream:
                data = stream.read(ref.size + 1)
        except OSError as exc:
            raise CaptureError("artifact_unavailable") from exc
        if len(data) != ref.size or blob_sha256(data) != ref.sha256:
            raise CaptureError("artifact_corrupt")
        return data

    def _publish(self, path, data):
        used = sum(p.stat().st_size for directory in ("blobs", "captures")
                   for p in (self.root / directory).iterdir() if p.is_file())
        if used + len(data) > self.limit_bytes:
            raise CaptureError("artifact_quota")
        fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
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

    def put(self, data: bytes) -> ArtifactRef:
        digest = blob_sha256(data)
        ref = ArtifactRef(digest, len(data), "blobs/" + digest)
        path = self._path(ref)
        if path.exists():
            self.read(ref)
        else:
            self._publish(path, data)
        return ref

    def commit(self, capture: WorkspaceCapture) -> Path:
        capture.validate_blobs(self)
        path = self.root / "captures" / (capture.capture_id + ".json")
        if path.exists():
            if path.is_symlink() or path.read_bytes() != capture.to_json():
                raise CaptureError("capture_id_conflict")
        else:
            self._publish(path, capture.to_json())
        return path
