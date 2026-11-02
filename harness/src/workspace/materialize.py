"""Materialize one captured candidate into a *fresh* same-image coordinator.

Returns the recaptured manifest only after base, policy, references and final tree
match. No tests are launched here. Failed/partial replicas are destroyed by the
coordinator. The caller closes a successful replica after its own validation.
"""
import base64

from src.workspace.capture import CaptureProvider, parse_snapshot
from src.workspace.contracts import read_verified_blob, tree_id
from src.workspace.control import CaptureError


async def materialize(capture, reader, replica: CaptureProvider, control):
    if replica.last_capture is not None:
        raise CaptureError("replica_not_fresh")
    if not capture.admissible:
        raise CaptureError("unsupported_change")
    if capture.rules_id != replica.policy.rules_id or capture.base_revision != replica.base_revision:
        raise CaptureError("replica_rules_mismatch")
    # Verify all references before any container side effects.
    capture.validate_blobs(reader)
    base_capture = await replica.capture(control)
    async with replica.coordinator.operation(control) as transport:
        if (transport.image_digest != capture.image_digest or base_capture.manifest_id != capture.base_manifest_id
                or capture.origin.get("image_id") != transport.image_id):
            raise CaptureError("base_manifest_mismatch")
        capture.validate_against_base(base_capture.entries)
        if replica.policy.violations(base_capture.entries, capture.entries):
            raise CaptureError("unsupported_change")
        request = replica.request("apply")
        request.update(target=[e.to_dict() for e in capture.entries], deleted=list(capture.deleted), changed=[])
        for path, ref in capture.changed:
            control.remaining()
            request["changed"].append([path, base64.b64encode(read_verified_blob(reader, ref)).decode("ascii")])
        raw = await transport.helper(request, control)
        entries, _, footer, manifest_id = parse_snapshot(raw, replica.policy, control, bodies_required=False)
        if footer["barrier"]["pid1"] != replica._pid1 or manifest_id != capture.manifest_id:
            raise CaptureError("materialized_manifest_mismatch")
        # Explicit recomputation rather than trusting the helper's success code.
        if tree_id(entries) != capture.manifest_id:
            raise CaptureError("materialized_manifest_mismatch")
        return entries
