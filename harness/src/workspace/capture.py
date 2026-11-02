"""Host verification and publication of a quiescent container snapshot."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from uuid import uuid4

from src.workspace.contracts import (FileEntry, WorkspaceCapture, blob_sha256, candidate_id,
                                     canonical_json, tree_id)
from src.workspace.control import CaptureError
from src.workspace.policy import CapturePolicy


def parse_snapshot(raw, policy, control, *, bodies_required=True):
    """Validate framing/offsets/full manifest and raw blob hashes before storing."""
    if len(raw) > control.wire_limit:
        raise CaptureError("wire_quota")
    entries, bodies, footer = [], {}, None
    try:
        for line in raw.splitlines():
            control.remaining()
            value = json.loads(line)
            if not isinstance(value, dict) or footer is not None:
                raise CaptureError("invalid_helper_protocol")
            if "error" in value:
                raise CaptureError(value["error"], evidence=value.get("evidence"))
            if set(value) == {"entry"}:
                entries.append(FileEntry.from_dict(value["entry"]))
            elif set(value) == {"blob", "offset", "data"}:
                path = value["blob"]
                data = base64.b64decode(value["data"], validate=True)
                previous = bodies.setdefault(path, bytearray())
                if type(value["offset"]) is not int or value["offset"] != len(previous) or len(data) > 48 * 1024:
                    raise CaptureError("invalid_blob_chunk")
                previous.extend(data)
                if sum(map(len, bodies.values())) > control.artifact_limit:
                    raise CaptureError("artifact_quota")
            elif value.get("complete") is True:
                footer = value
            else:
                raise CaptureError("invalid_helper_protocol")
        if footer is None:
            raise CaptureError("incomplete_capture")
        identity = tree_id(entries)  # includes path uniqueness/case/ancestor checks
        entries = tuple(sorted(entries, key=lambda e: e.path.encode("utf-8")))
        by_path = {e.path: e for e in entries}
        if len(entries) > policy.max_entries or sum(e.size for e in entries) > policy.max_tree_bytes:
            raise CaptureError("manifest_limit")
        for name in ("changed", "deleted", "corpus"):
            paths = footer[name]
            if not isinstance(paths, list) or len(set(paths)) != len(paths):
                raise CaptureError("invalid_helper_protocol")
        if len(footer["changed"]) + len(footer["deleted"]) > policy.max_delta_files:
            raise CaptureError("delta_file_limit")
        if sum(by_path[p].size for p in footer["changed"]) > policy.max_delta_bytes:
            raise CaptureError("delta_byte_limit")
        expected_corpus = {e.path for e in entries if e.path.endswith(".py") and e.size <= policy.max_python_file_bytes}
        if set(footer["corpus"]) != expected_corpus:
            raise CaptureError("corpus_set_mismatch")
        if sum(by_path[p].size for p in expected_corpus) > policy.max_corpus_bytes:
            raise CaptureError("corpus_byte_limit")
        expected_blobs = set(footer["changed"]) | expected_corpus if bodies_required else set()
        if set(bodies) != expected_blobs:
            raise CaptureError("blob_set_mismatch")
        for path, data in bodies.items():
            if len(data) != by_path[path].size or blob_sha256(bytes(data)) != by_path[path].sha256:
                raise CaptureError("blob_mismatch")
        barrier = footer["barrier"]
        if barrier["protocol"] != "quiescence-v1" or barrier["before"] != barrier["after"]:
            raise CaptureError("invalid_barrier")
        records = barrier["before"]
        if (len(records) != 2 or 1 not in {r["pid"] for r in records}
                or any(r["threads"] != [r["pid"]] for r in records)
                or barrier["pid1"] != next(r for r in records if r["pid"] == 1)):
            raise CaptureError("invalid_barrier")
        return entries, {p: bytes(data) for p, data in bodies.items()}, footer, identity
    except CaptureError:
        raise
    except (ValueError, KeyError, TypeError, StopIteration) as exc:
        raise CaptureError("invalid_helper_protocol") from exc


class CaptureProvider:
    """Owns one immutable base/policy and publishes only fully verified captures.

    First call after coordinator.start seeds from /opt/repo and captures the actual
    initial /workspace tree. Further calls are always deltas relative to that base.
    No failed call replaces ``last_capture``. Do not bypass the coordinator.
    """
    def __init__(self, coordinator, store, *, base_revision: str, policy: CapturePolicy | None = None):
        if not isinstance(base_revision, str) or not base_revision or "\0" in base_revision:
            raise ValueError("nonempty base revision required")
        self.coordinator, self.store = coordinator, store
        self._base_revision = base_revision
        self._policy = policy or CapturePolicy()
        self._rules_id = self._policy.rules_id
        self._base = None
        self._pid1 = None
        self.last_capture = None

    @property
    def policy(self):
        return self._policy

    @property
    def base_revision(self):
        return self._base_revision

    @property
    def base_entries(self):
        if self._base is None:
            raise CaptureError("base_not_captured")
        return self._base

    def request(self, op):
        pid1 = None if self._pid1 is None else dict(self._pid1, threads=list(self._pid1["threads"]))
        return dict(op=op, policy=self._policy.config(), pid1=pid1,
                    base=None if self._base is None else [e.to_dict() for e in self._base])

    async def capture(self, control):
        async with self.coordinator.operation(control) as transport:
            if self._policy.rules_id != self._rules_id:
                raise CaptureError("rules_changed")
            raw = await transport.helper(self.request("seed" if self._base is None else "capture"), control)
            entries, bodies, footer, manifest_id = parse_snapshot(raw, self._policy, control)
            if self._pid1 is not None and footer["barrier"]["pid1"] != self._pid1:
                raise CaptureError("workspace_not_quiescent")
            base = entries if self._base is None else self._base
            violations = self._policy.violations(base, entries)
            capture_id = str(uuid4())
            # All protocol and bytes have passed verification before any publication.
            if sum(map(len, bodies.values())) + len(canonical_json(footer["barrier"])) > control.artifact_limit:
                raise CaptureError("artifact_quota")
            refs = {}
            for path, data in bodies.items():
                control.remaining()
                refs[path] = self.store.put(data)
            barrier_ref = self.store.put(canonical_json(footer["barrier"]))
            identity = dict(base_revision=self.base_revision, image_digest=transport.image_digest,
                            base_manifest_id=tree_id(base), manifest_id=manifest_id, rules_id=self._rules_id)
            capture = WorkspaceCapture(
                schema_version="workspace-v1", capture_id=capture_id,
                origin=dict(self.coordinator.transport_identity, operation_id=control.operation_id),
                **identity, candidate_id=candidate_id(**identity), entries=entries,
                changed=tuple((p, refs[p]) for p in footer["changed"]), deleted=tuple(footer["deleted"]),
                corpus_blobs=tuple((p, refs[p]) for p in footer["corpus"]), excluded=tuple(footer["excluded"]),
                admissible=not violations, violations=violations,
                captured_at=datetime.now(timezone.utc).isoformat(), barrier_receipt=barrier_ref)
            capture.validate_against_base(base)
            capture.validate_blobs(self.store)
            if sum(map(len, bodies.values())) + len(canonical_json(footer["barrier"])) + len(capture.to_json()) > control.artifact_limit:
                raise CaptureError("artifact_quota")
            control.remaining()
            self.store.commit(capture)
            control.remaining()
            self._base, self._pid1, self.last_capture = base, footer["barrier"]["pid1"], capture
            return capture
