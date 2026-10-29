#!/usr/bin/env python3
"""Real Sphinx capture -> explicit BM25 -> probe edits, without an agent/validator.

Run from any cwd with the project's existing environment. Output must be a new
directory; artifacts and the bounded result.json stay under that directory.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.retrieval.workspace import IndexConfig, WorkspaceIndex
from src.workspace.blobs import WorkspaceBlobStore
from src.workspace.capture import CaptureProvider
from src.workspace.contracts import blob_sha256
from src.workspace.control import CaptureControl, CaptureError
from src.workspace.coordinator import WorkspaceCoordinator
from src.workspace.docker_transport import DockerWorkspace

IMAGE = "coding-harness-a5-clean@sha256:1a6e20fb4b37194f8596111bc3658261cc40dbd85dc09a0e5be6d6690220b4e2"
REVISION = "82ef497a8c88f0f6e50d84520e7276bfbf65025d"
PROBE_PATH = "sphinx/_workspace_retrieval_mechanism_probe.py"
OLD_SYMBOL = "workspaceprobequasarsignal"
NEW_SYMBOL = "workspaceprobeorbitsignal"
ADDED = ("# Workspace retrieval mechanism probe; never a repair.\n\n"
         f"def {OLD_SYMBOL}(value):\n    return value + 1\n").encode()
MODIFIED = ("# Workspace retrieval mechanism probe; never a repair.\n\n\n\n\n"
            f"def {NEW_SYMBOL}(value):\n    return value + 2\n").encode()


class DemoError(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise DemoError(reason)


async def run_demo(output_dir: Path, *, context="desktop-linux", timeout=300):
    if type(timeout) is not int or timeout <= 0:
        raise DemoError("timeout_must_be_positive_seconds")
    output_dir = Path(output_dir).expanduser().absolute()
    try:
        output_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise DemoError("output_directory_exists") from exc
    started = time.monotonic()
    deadline = started + timeout
    report = dict(schema_version="workspace-retrieval-demo-v1", status="running",
                  started_at=datetime.now(timezone.utc).isoformat(), mode="bm25",
                  scope="module_integration_only", validation_run=False, model_calls=0,
                  image=IMAGE, base_revision=REVISION, probe_path=PROBE_PATH, stages=[],
                  artifact_store="artifacts", result_file="result.json")
    coordinator = WorkspaceCoordinator(DockerWorkspace(image=IMAGE, context=context))
    store = WorkspaceBlobStore(output_dir / "artifacts")
    provider = CaptureProvider(coordinator, store, base_revision=REVISION)
    index = WorkspaceIndex(store, config=IndexConfig(mode="bm25"))

    def control(operation):
        return CaptureControl(operation, deadline)

    async def snapshot(phase, queries, *, full_search=False):
        before = time.monotonic()
        capture = await provider.capture(control(phase))
        captured = time.monotonic()
        receipt = index.update(capture)
        indexed = time.monotonic()
        require(receipt.status == "ready", "index_unavailable:" + str(receipt.reason))
        require(capture.admissible, "probe_became_inadmissible")
        require(receipt.encoded == 0 and receipt.mode == "bm25", "unexpected_encoder_use")
        responses, query_records = [], []
        manifest = {entry.path: entry for entry in capture.entries}
        for query in queries:
            response = index.search(query, k=receipt.files if full_search else 5)
            require(response.status == "ready", "search_unavailable:" + str(response.reason))
            require((response.capture_id, response.candidate_id, response.corpus_id, response.index_id) ==
                    (capture.capture_id, capture.candidate_id, receipt.corpus_id, receipt.index_id),
                    "query_version_mismatch")
            for hit in response.hits:
                require(hit.path in manifest and hit.file_sha256 == manifest[hit.path].sha256,
                        "query_file_sha_mismatch")
            responses.append(response)
            query_records.append(dict(query=query, inspected_hits=len(response.hits),
                probe_present=any(hit.path == PROBE_PATH for hit in response.hits),
                hits=[dict(path=hit.path, file_sha256=hit.file_sha256, material_kind=hit.material_kind,
                           chunks=[dict(symbol=c.symbol, start_line=c.start_line, end_line=c.end_line)
                                   for c in hit.chunks]) for hit in response.hits[:2]]))
        control(phase).remaining(None)
        record = dict(phase=phase, capture_id=capture.capture_id, candidate_id=capture.candidate_id,
            manifest_id=capture.manifest_id, corpus_id=receipt.corpus_id, index_id=receipt.index_id,
            tree_files=len(capture.entries), tree_bytes=sum(e.size for e in capture.entries),
            transferred_python_files=len(capture.corpus_blobs), indexed_files=receipt.files, chunks=receipt.chunks,
            rebuilt=receipt.rebuilt, reused=receipt.reused, deleted_from_index=receipt.deleted,
            encoded=receipt.encoded, exclusions=[dict(path=n.path, reason=n.reason) for n in receipt.exclusions],
            warning_count=len(receipt.warnings), changed=[p for p, _ in capture.changed], deleted=list(capture.deleted),
            duration_ms=dict(capture=int((captured-before)*1000), index=int((indexed-captured)*1000),
                             query=int((time.monotonic()-indexed)*1000)), queries=query_records)
        report["stages"].append(record)
        print(json.dumps({key: record[key] for key in
              ("phase", "capture_id", "candidate_id", "corpus_id", "index_id", "indexed_files", "rebuilt", "reused")}), flush=True)
        return capture, receipt, responses

    async def write_probe(body, *, create):
        mode = "xb" if create else "wb"
        result = await coordinator.execute(["/usr/local/bin/python", "-I", "-S", "-c",
            "import sys; " + f"f=open('/workspace/{PROBE_PATH}',{mode!r}); "
            "f.write(sys.stdin.buffer.read()); f.close()"], control("write-probe"), stdin=body)
        require(result.exit_code == 0, "probe_write_failed")

    try:
        await coordinator.start(control("start"))
        base, initial, _ = await snapshot("baseline", ["docname_join", OLD_SYMBOL])
        report["container"] = coordinator.transport_identity
        require(PROBE_PATH not in {entry.path for entry in base.entries}, "probe_path_already_exists")
        require(len(base.corpus_blobs) == initial.files == 534, "unexpected_sphinx_python_file_count")
        same, reused, _ = await snapshot("unchanged", [OLD_SYMBOL])
        require(same.capture_id != base.capture_id and same.candidate_id == base.candidate_id,
                "unchanged_capture_identity_mismatch")
        require((reused.corpus_id, reused.index_id) == (initial.corpus_id, initial.index_id)
                and reused.rebuilt == 0 and reused.reused == 534, "unchanged_index_reuse_failed")

        await write_probe(ADDED, create=True)
        added, add_index, responses = await snapshot("added", [OLD_SYMBOL])
        hit = next((h for h in responses[0].hits if h.path == PROBE_PATH), None)
        require(hit is not None and hit.file_sha256 == blob_sha256(ADDED)
                and any(c.symbol == OLD_SYMBOL and c.start_line == 3 for c in hit.chunks),
                "added_symbol_location_not_found")
        require(added.candidate_id != base.candidate_id and add_index.corpus_id != initial.corpus_id
                and add_index.index_id != initial.index_id and add_index.files == 535
                and add_index.rebuilt == 1 and add_index.reused == 534, "added_index_identity_mismatch")

        await write_probe(MODIFIED, create=False)
        modified, modify_index, responses = await snapshot("modified", [OLD_SYMBOL, NEW_SYMBOL])
        hit = next((h for h in responses[1].hits if h.path == PROBE_PATH), None)
        require(hit is not None and hit.file_sha256 == blob_sha256(MODIFIED)
                and any(c.symbol == NEW_SYMBOL and c.start_line == 6 for c in hit.chunks),
                "modified_symbol_location_not_found")
        require(modified.candidate_id != added.candidate_id and modify_index.corpus_id != add_index.corpus_id
                and modify_index.index_id != add_index.index_id and modify_index.rebuilt == 1
                and modify_index.reused == 534, "modified_index_identity_mismatch")

        result = await coordinator.execute(["/usr/local/bin/python", "-I", "-S", "-c",
            f"import os; os.unlink('/workspace/{PROBE_PATH}')"], control("delete-probe"))
        require(result.exit_code == 0, "probe_delete_failed")
        deleted, delete_index, responses = await snapshot("deleted", [OLD_SYMBOL, NEW_SYMBOL], full_search=True)
        require(all(hit.path != PROBE_PATH for response in responses for hit in response.hits), "deleted_probe_still_indexed")
        require(deleted.entries == base.entries and deleted.candidate_id == base.candidate_id
                and delete_index.corpus_id == initial.corpus_id and delete_index.index_id == initial.index_id
                and delete_index.deleted == 1 and delete_index.files == 534 and delete_index.rebuilt == 0
                and delete_index.reused == 534, "deleted_state_did_not_restore_baseline")
        require(len({s["capture_id"] for s in report["stages"]}) == 5, "capture_id_reused")
        report.update(status="passed", checks=dict(full_container_tree=True, unchanged_reuse=True,
            added_symbol_current_sha=True, modified_symbol_current_sha_and_line=True,
            deleted_probe_absent_from_all_hits=True, original_tree_restored=True))
    except asyncio.CancelledError:
        report.update(status="cancelled", reason="cancelled")
        raise
    except Exception as exc:
        report.update(status="failed", reason=exc.reason if isinstance(exc, CaptureError) else str(exc),
                      error_type=type(exc).__name__)
    finally:
        cleanup_started = time.monotonic()
        cleanup_ok = await coordinator.close()
        report["cleanup"] = dict(ok=cleanup_ok, duration_ms=int((time.monotonic()-cleanup_started)*1000))
        if not cleanup_ok:
            report.update(status="failed", reason="owned_container_cleanup_failed")
        report["elapsed_ms"] = int((time.monotonic()-started)*1000)
        with (output_dir / "result.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        print(json.dumps(dict(status=report["status"], result_file=str(output_dir / "result.json"),
                              cleanup_ok=cleanup_ok, elapsed_ms=report["elapsed_ms"],
                              reason=report.get("reason"))), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path, help="New directory; existing paths are rejected")
    parser.add_argument("--context", default="desktop-linux")
    parser.add_argument("--timeout", default=300, type=int, help="Total monotonic deadline in seconds")
    args = parser.parse_args()
    try:
        report = asyncio.run(run_demo(args.output_dir, context=args.context, timeout=args.timeout))
    except (DemoError, OSError) as exc:
        print(json.dumps(dict(status="failed", reason=str(exc))), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
