"""Pure journal reduction and fail-closed compatibility checks. No tools imported."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from src.tracing.store import StoreError


@dataclass
class RecoveredState:
    manifest: dict = field(default_factory=dict)
    loop: dict = field(default_factory=dict)
    messages: list = field(default_factory=list)
    operations: dict = field(default_factory=dict)
    budget: dict = field(default_factory=dict)
    plugin_state: dict = field(default_factory=dict)
    terminal: dict | None = None
    last_seq: int = 0
    pending_model: bool = False
    run_output: dict = field(default_factory=dict)
    attempts: list = field(default_factory=list)
    runtime_hash: str | None = None
    execution: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)
    workspace: dict = field(default_factory=dict)


def reduce_events(events) -> RecoveredState:
    state = RecoveredState()
    for event in events:
        if event["seq"] != state.last_seq + 1:
            raise StoreError("non-contiguous recovery events")
        state.last_seq = event["seq"]
        kind, p = event["event_type"], copy.deepcopy(event["payload"])
        if state.terminal is not None:
            raise StoreError("events after terminal state")
        if kind == "run.started":
            if state.manifest:
                raise StoreError("duplicate run start")
            state.manifest = p
        elif kind == "runtime.bound":
            if state.runtime_hash is not None:
                raise StoreError("runtime already bound")
            state.runtime_hash = p["hash"]
        elif kind == "state.saved":
            state.loop = p
            state.messages = p.get("messages", [])
            state.run_output = p.get("run_output", state.run_output)
            response = p.get("response")
            if response:
                import json
                assistant = dict(role="assistant", content=response["text"])
                if response["tool_calls"]:
                    assistant["tool_calls"] = [dict(id=c["id"], type="function", function=dict(
                        name=c["name"], arguments=c["raw_arguments"] or json.dumps(c["arguments"])))
                        for c in response["tool_calls"]]
                state.messages = state.messages + [assistant]
            state.pending_model = False
        elif kind == "model.started":
            state.budget = p["budget"]
            state.pending_model = True
        elif kind == "model.settled":
            state.budget = p["budget"]
            if "record" in p:
                state.attempts.append(p["record"])
            # Until the full response is committed in state.saved, it cannot be used.
        elif kind == "tool.intent":
            op = event["operation_id"]
            if op in state.operations:
                raise StoreError("duplicate operation intent")
            state.operations[op] = dict(intent=p, result=None, attempt_id=event["attempt_id"],
                                        tool_call_id=event["tool_call_id"])
            if state.workspace and p.get("side_effect") != "read_only":
                state.workspace["current_unknown"] = True
        elif kind == "tool.result":
            op = event["operation_id"]
            if op not in state.operations or state.operations[op]["result"] is not None:
                raise StoreError("result without unique intent")
            if "_run_output" in p:
                state.run_output = p.pop("_run_output")
            state.operations[op]["artifacts"] = p.pop("_artifacts", [])
            state.operations[op]["result"] = p
            content = p["output"]
            if p.get("error"):
                content += ("\n" if content else "") + "Error: " + p["error"]
            state.messages.append(dict(role="tool", tool_call_id=p["call_id"], content=content))
        elif kind == 'context.prepared':
            state.context = p
            if p['stats'].get('manager_state'):
                state.plugin_state['h4'] = p['stats']['manager_state']
        elif kind in ('summary.started', 'summary.settled'):
            state.plugin_state['h4'] = p['manager_state']
            if p.get('budget') is not None:
                state.budget = p['budget']
        elif kind == 'context.rejected':
            state.context = {}
        elif kind == "plugin.state":
            state.plugin_state[p["name"]] = p["state"]
        elif kind == "plugin.dispatch":
            receipt = p.get("receipt")
            if not isinstance(receipt, dict) or "observation_sha256" not in receipt:
                raise StoreError("invalid dispatcher receipt")
            manager = copy.deepcopy(p.get("manager_state") or state.plugin_state.get("h4", {}))
            manager.setdefault("dispatch_calls", 0)
            manager.setdefault("dispatch_receipts", [])
            manager.setdefault("dispatch_observations", [])
            # The manager snapshot already includes this receipt when written by
            # the hook; retain it exactly once during replay.
            state.plugin_state["h4"] = manager
        elif kind == 'execution.starting':
            if state.execution:
                raise StoreError('duplicate container session')
            state.execution = dict(starting=p)
        elif kind == 'execution.snapshot':
            if not state.execution or 'snapshot' in state.execution:
                raise StoreError('snapshot without unique container session')
            state.execution['snapshot'] = p
        elif kind == "workspace.started":
            if state.workspace:
                raise StoreError("duplicate workspace start; Docker continuation is unsupported")
            state.workspace = dict(started=p, captures={}, records={}, pending_validation={}, observations=[], feedback=[])
        elif kind.startswith("workspace.capture."):
            if not state.workspace or state.workspace.get("sealed"):
                raise StoreError("capture outside active workspace")
            if kind == "workspace.capture.committed":
                state.workspace["captures"][p["capture_id"]] = p
                state.workspace["last_capture_ref"] = p["capture_ref"]
                state.workspace["current_unknown"] = False
            elif kind == "workspace.capture.failed":
                state.workspace["current_unknown"] = True
                state.workspace["last_capture_failure"] = p
            elif kind not in ("workspace.capture.started", "workspace.capture.failed"):
                raise StoreError("unsupported workspace capture event")
        elif kind.startswith("validation."):
            if not state.workspace or state.workspace.get("sealed"):
                raise StoreError("validation outside active workspace")
            pending = state.workspace["pending_validation"]
            if kind == "validation.reserved":
                if p["operation_id"] in pending:
                    raise StoreError("duplicate validation reservation")
                pending[p["operation_id"]] = p
            elif kind == "validation.started":
                if p["operation_id"] not in pending:
                    raise StoreError("validation start without reservation")
                pending[p["operation_id"]].update(p)
            elif kind == "validation.committed":
                value = p.get("record") or p.get("attempt")
                if value["operation_id"] not in pending:
                    raise StoreError("validation commit without reservation")
                del pending[value["operation_id"]]
                if p.get("record"):
                    state.workspace["records"][value["record_id"]] = p
            elif kind not in ("validation.reused", "validation.not_run"):
                raise StoreError("unsupported validation event")
            if "budget" in p:
                state.workspace["validation_budget"] = p["budget"]
            if "artifacts" in p:
                state.workspace["validation_artifacts"] = p["artifacts"]
        elif kind == "workspace.baseline":
            if not state.workspace or "baseline" in state.workspace:
                raise StoreError("baseline without unique workspace")
            state.workspace["baseline"] = p
        elif kind in ("completion.feedback", "completion.observed"):
            if not state.workspace or state.workspace.get("sealed"):
                raise StoreError("observation outside active workspace")
            key = "feedback" if kind == "completion.feedback" else "observations"
            if p["record_id"] in state.workspace["feedback"] + state.workspace["observations"]:
                raise StoreError("validation observation already delivered")
            state.workspace[key].append(p["record_id"])
            if kind == "completion.feedback":
                state.messages.append(p["message"])
                state.workspace["validation_budget"] = p["budget"]
        elif kind == "completion.sealed":
            if not state.workspace or state.workspace.get("sealed"):
                raise StoreError("workspace already sealed")
            state.workspace["sealed"] = p
        elif kind == "run.finished":
            if state.terminal is not None:
                raise StoreError("duplicate terminal event")
            state.terminal = p
        else:
            raise StoreError(f"unsupported recovery event: {kind}")
    return state


def workspace_identity(path: Path):
    path = Path(path).resolve(strict=True)
    if not path.is_dir():
        raise StoreError("workspace is not a directory")
    stat = path.stat()
    return dict(path=str(path), device=stat.st_dev, inode=stat.st_ino)


def validate_resume(manifest, expected, artifacts):
    for key in ("code_id", "config_hash", "backend", "workspace", "schema_version"):
        if manifest.get(key) != expected.get(key):
            raise StoreError(f"resume incompatible: {key}")
    if manifest.get("schema_version") != "0.1":
        raise StoreError("unsupported recovery schema")
    if workspace_identity(Path(manifest["workspace"]["path"])) != manifest["workspace"]:
        raise StoreError("resume incompatible: workspace replaced")
    try:
        expiry = datetime.fromisoformat(manifest["expires_at"])
        expired = expiry <= datetime.now(timezone.utc)
    except (ValueError, TypeError, KeyError) as exc:
        raise StoreError("invalid absolute expiry") from exc
    if expired:
        raise StoreError("run absolute expiry reached; downtime consumes budget")


def file_hash(path: Path):
    import hashlib
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink():
        raise StoreError("reconciliation target is not a regular file")
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def reconcile_file(intent, workspace: Path):
    """Read-only proof of a completed atomic replacement; never re-execute a write."""
    proof = intent.get("replacement")
    if not proof:
        return False
    target = (workspace / proof["path"]).resolve()
    if not target.is_relative_to(workspace.resolve()):
        raise StoreError("reconciliation path outside workspace")
    # before==after cannot prove the logical operation happened.
    return proof["before"] != proof["after"] and file_hash(target) == proof["after"]
