"""Workspace references in the existing session ArtifactStore; read-only export.

No database, private grader reads, container access, or expired-session resume.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.tracing.store import StoreError
from src.tracing.artifacts import ArtifactQuotaError
from src.workspace.contracts import ArtifactRef, WorkspaceCapture, canonical_json


class SessionWorkspaceArtifacts:
    def __init__(self, session, *, final_reserve=0):
        self.session = session
        self.store = session.artifacts
        self.final_reserve = final_reserve
        self.finalizing = False

    @property
    def finalizing(self):
        return self._finalizing

    @finalizing.setter
    def finalizing(self, value):
        self._finalizing = value
        self.store.reserve_bytes = 0 if value else self.final_reserve

    def require_capacity(self, amount):
        reserve = 0 if self.finalizing else self.final_reserve
        if self.store.used_bytes() + amount + reserve > self.store.limit_bytes:
            raise ArtifactQuotaError("workspace artifact reserve would be exhausted")

    def native(self, ref):
        if ref.key != ref.sha256:
            raise StoreError("workspace artifact key mismatch")
        return dict(sha256=ref.sha256, size_bytes=ref.size, path=ref.key,
                    run_id=self.store.run_id)

    def put(self, data):
        # ArtifactStore enforces the same reserve for every publisher, including
        # journal spills. Existing content is verified/reused without charging it.
        native = self.store.publish(data)
        return ArtifactRef(native["sha256"], native["size_bytes"], native["path"])

    publish = put

    def read(self, ref):
        return self.store.read(self.native(ref))

    def commit(self, capture):
        capture.validate_blobs(self)
        return self.put(capture.to_json())


def inspect_workspace_session(directory):
    from src.runtime.session import RunSession
    from src.tracing.store import EventStore
    from src.tracing.artifacts import ArtifactStore
    # Deliberately no expiry/resume check and no reconciliation of pending work.
    with EventStore(Path(directory), readonly=True) as events:
        session = RunSession(events, ArtifactStore(Path(directory) / "artifacts", events.run_id, readonly=True))
        _verified_workspace(session)
        state = session.state
        return dict(run_id=events.run_id, terminal=state.terminal,
                    workspace=state.workspace, pending_model=state.pending_model,
                    pending_tools=[key for key, value in state.operations.items() if value["result"] is None])


def _verified_workspace(session):
    """Verify nested committed evidence too, not only journal-owned JSON refs."""
    from src.validation.contracts import ValidationRecord
    bridge = SessionWorkspaceArtifacts(session)
    captures, records, blobs = {}, {}, {}
    for payload in session.state.workspace.get("captures", {}).values():
        raw = session.artifacts.read(payload["capture_ref"])
        capture = WorkspaceCapture.from_json(raw)
        blobs[payload["capture_ref"]["sha256"]] = raw
        if (capture.capture_id != payload["capture_id"] or capture.candidate_id != payload["candidate_id"]):
            raise StoreError("committed capture identity mismatch")
        capture.validate_blobs(bridge)
        captures[capture.capture_id] = capture
        for ref in [capture.barrier_receipt, *(r for _, r in capture.changed),
                    *(r for _, r in capture.corpus_blobs)]:
            blobs[ref.sha256] = bridge.read(ref)
    for key, payload in session.state.workspace.get("records", {}).items():
        raw = session.artifacts.read(payload["record_ref"])
        data = json.loads(raw)
        blobs[payload["record_ref"]["sha256"]] = raw
        record = ValidationRecord.from_dict(data)
        if record.record_id != key or data != payload["record"]:
            raise StoreError("committed validation identity mismatch")
        records[key] = record
        for ref in record.evidence:
            blobs[ref.sha256] = bridge.read(ref)
    baseline = session.state.workspace.get("baseline") or {}
    for name in ("spec", "environment", "binding"):
        if name in baseline:
            ref = baseline[name]
            blobs[ref["sha256"]] = session.artifacts.read(ref)
    return captures, records, blobs


def export_workspace_session(directory, output):
    """Export only committed, hash-verified artifacts. Never execute pending work."""
    from src.runtime.session import RunSession
    from src.tracing.store import EventStore
    from src.tracing.artifacts import ArtifactStore
    from src.validation.contracts import ValidationRecord

    directory, output = Path(directory), Path(output)
    with EventStore(directory, readonly=True) as events:
        native = ArtifactStore(directory / "artifacts", events.run_id, readonly=True)
        session = RunSession(events, native)
        _, records, blobs = _verified_workspace(session)
        state = session.state.workspace
        sealed = state.get("sealed") or {}
        capture_ref = sealed.get("capture_ref") or state.get("last_capture_ref")
        if not capture_ref:
            raise StoreError("no committed workspace capture to export")
        capture = WorkspaceCapture.from_json(native.read(capture_ref))
        bridge = SessionWorkspaceArtifacts(session)
        capture.validate_blobs(bridge)
        record = None
        record_ref = sealed.get("record_ref")
        if record_ref is None:
            # A crash after record commit but before seal still has valid
            # historical evidence for this committed candidate. Never run it again.
            record_ref = next((p["record_ref"] for p in reversed(list(state.get("records", {}).values()))
                               if p["record"]["candidate_id"] == capture.candidate_id), None)
        if record_ref:
            record = ValidationRecord.from_dict(json.loads(native.read(record_ref)))
            if record.candidate_id != capture.candidate_id:
                raise StoreError("sealed validation/candidate mismatch")
            for ref in record.evidence:
                blobs[ref.sha256] = bridge.read(ref)
        # All validation precedes writing. A new destination is mandatory.
        output.mkdir(parents=True, exist_ok=False)
        (output / "blobs").mkdir()
        for key, data in blobs.items():
            (output / "blobs" / key).write_bytes(data)
        (output / "captures").mkdir()
        for committed in session.state.workspace.get("captures", {}).values():
            data = native.read(committed["capture_ref"])
            (output / "captures" / (committed["capture_id"] + ".json")).write_bytes(data)
        (output / "capture.json").write_bytes(capture.to_json())
        if record:
            (output / "validation.json").write_bytes(canonical_json(record.to_dict()))
        baseline = state.get("baseline") or {}
        for name in ("spec", "environment", "binding"):
            if name in baseline:
                (output / (name + ".json")).write_bytes(blobs[baseline[name]["sha256"]])
        if baseline.get("record_id") in records:
            (output / "baseline-validation.json").write_bytes(canonical_json(records[baseline["record_id"]].to_dict()))
        for path, ref in capture.changed:
            destination = output / "delta" / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(blobs[ref.sha256])
        final_report = ((session.state.terminal or {}).get("workspace_v1") or sealed.get("report"))
        if session.state.terminal is None:
            final_report = dict(final_report or {}, run=dict(status="interrupted", reason="no_run_finished"))
        manifest = dict(schema_version="workspace-export-v1", candidate_id=capture.candidate_id,
                        final=bool(sealed and sealed.get("capture_ref")), deleted=list(capture.deleted),
                        record_id=record.record_id if record else None,
                        pending=state.get("pending_validation", {}), report=final_report,
                        baseline=baseline or None,
                        capture_ids=list(state.get("captures", {})),
                        captures=state.get("captures", {}),
                        records={key: value["record_ref"] for key, value in state.get("records", {}).items()})
        (output / "manifest.json").write_bytes(canonical_json(manifest))
        return manifest
