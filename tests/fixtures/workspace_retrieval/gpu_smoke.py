"""Explicit offline GPU mechanism check; never collected as a pytest test.

Uses an existing pinned local Qwen snapshot and an isolated workspace cache.
Writes only small JSON evidence: no model weights or vector arrays.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
import json
import os
import runpy
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("HF_HUB_OFFLINE") != "1" or os.environ.get("TRANSFORMERS_OFFLINE") != "1":
        raise RuntimeError("both offline flags must be set before importing model libraries")
    # This diagnostic is deliberately confined to its worktree.
    for path in (args.cache_root, args.output):
        if not path.resolve().is_relative_to(ROOT):
            raise RuntimeError("cache/output must be inside this worktree")

    import numpy as np
    import torch
    from src.retrieval import workspace, workspace_encoder
    from src.retrieval.embed import MODELS
    from src.retrieval.workspace import IndexConfig, WorkspaceIndex
    from src.retrieval.workspace_encoder import EncoderConfig, LocalSentenceTransformerBackend, WorkspaceEncoder
    from src.workspace.contracts import blob_sha256

    for module in (workspace, workspace_encoder):
        assert Path(module.__file__).resolve().is_relative_to(ROOT)
    assert torch.cuda.is_available(), "CUDA unavailable; no CPU fallback"
    torch.cuda.set_device(0)
    torch.cuda.reset_peak_memory_stats()
    spec = MODELS["qwen3-0.6b"]
    config = EncoderConfig(spec.name, spec.revision, "cuda", "float16", 512,
                           spec.query_prefix, spec.doc_prefix,
                           runtime_versions=tuple(sorted((name, version(name)) for name in
                             ("numpy", "torch", "sentence-transformers", "transformers", "tokenizers"))))
    evidence = dict(schema="p2-a-local-gpu-smoke-v1", status="running",
                    started_at=datetime.now(timezone.utc).isoformat(),
                    code_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                    runner_sha256=blob_sha256(Path(__file__).read_bytes()),
                    fixture="tests/fixtures/workspace_contracts/fixture.py:C0/C1",
                    offline={name: os.environ[name] for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")},
                    no_solver_api=True, no_docker=True, no_old_cr_cache=True,
                    gpu=dict(name=torch.cuda.get_device_name(0),
                             total_memory_bytes=torch.cuda.get_device_properties(0).total_memory,
                             capability=list(torch.cuda.get_device_capability(0))),
                    encoder_config=config.to_dict(),
                    snapshot=dict(model=spec.name, revision=args.snapshot.name,
                                  weights_bytes=(args.snapshot / "model.safetensors").stat().st_size),
                    cache_root=args.cache_root.resolve().relative_to(ROOT).as_posix(),
                    backend_batches=[])
    phase = "loading"

    class TimedBackend:
        def __init__(self):
            begin = time.perf_counter()
            self.backend = LocalSentenceTransformerBackend(config, args.snapshot)
            torch.cuda.synchronize()
            evidence["load_seconds"] = round(time.perf_counter() - begin, 6)
            evidence["observed_parameter_dtypes"] = sorted({str(p.dtype) for p in self.backend.model.parameters()})
            print("Loaded pinned Qwen on " + evidence["gpu"]["name"], flush=True)

        def encode(self, texts):
            torch.cuda.synchronize()
            begin = time.perf_counter()
            output = self.backend.encode(texts)
            torch.cuda.synchronize()
            evidence["backend_batches"].append(dict(phase=phase, inputs=len(texts),
                seconds=round(time.perf_counter() - begin, 6),
                actual_text_sha256=[blob_sha256(text.encode("utf-8")) for text in texts]))
            return output

    def timed(call):
        torch.cuda.synchronize()
        begin = time.perf_counter()
        result = call()
        torch.cuda.synchronize()
        return result, round(time.perf_counter() - begin, 6)

    def batch_record(batch, seconds):
        norms = np.linalg.norm(batch.vectors.astype(np.float64), axis=1)
        assert np.isfinite(batch.vectors).all() and np.allclose(norms, 1, atol=1e-5)
        return dict(shape=list(batch.vectors.shape), dtype=str(batch.vectors.dtype),
                    finite=True, unit_norm=True, norms=norms.tolist(), encoded=batch.encoded,
                    reused=batch.reused, seconds=seconds, actual_text_sha256=batch.text_hashes)

    encoder = WorkspaceEncoder(config, TimedBackend, cache_root=args.cache_root)
    try:
        docs = ["# path: pkg/calc.py  symbol: add\ndef add(a, b): return a + b",
                "# path: tests/test_calc.py  symbol: test_add\ndef test_add(): assert add(1, 2) == 3"]
        phase = "standalone_documents"
        first, first_time = timed(lambda: encoder.encode(docs, role="document"))
        phase = "standalone_document_replay"
        again, again_time = timed(lambda: encoder.encode(docs, role="document"))
        phase = "standalone_query"
        query, query_time = timed(lambda: encoder.encode(["addition calculation"], role="query"))
        assert first.encoded == 2 and again.encoded == 0 and again.reused == 2
        assert np.array_equal(first.vectors, again.vectors)
        assert first.vectors.shape[1] == query.vectors.shape[1]
        evidence["standalone"] = dict(first=batch_record(first, first_time),
            replay=batch_record(again, again_time), query=batch_record(query, query_time),
            replay_exact=True, similarity_scores=(first.vectors @ query.vectors[0]).tolist())
        fixture = runpy.run_path(str(ROOT / "tests/fixtures/workspace_contracts/fixture.py"))
        captures, blobs = fixture["build_sequence"]()
        index = WorkspaceIndex(blobs, config=IndexConfig(mode="hybrid"), encoder=encoder)
        versions = []
        for name in ("C0", "C1"):
            phase = "index_update_" + name
            receipt, update_time = timed(lambda: index.update(captures[name]))
            assert receipt.status == "ready", receipt.reason
            phase = "index_query_" + name
            response, search_time = timed(lambda: index.search("add calculation function test", k=2))
            assert response.status == "ready" and len(response.hits) == 2
            assert response.capture_id == captures[name].capture_id
            assert response.candidate_id == captures[name].candidate_id
            current_hashes = {entry.path: entry.sha256 for entry in captures[name].entries}
            assert all(hit.file_sha256 == current_hashes[hit.path] for hit in response.hits)
            versions.append(dict(name=name, receipt=asdict(receipt), update_seconds=update_time,
                                  search_seconds=search_time, hits=[asdict(hit) for hit in response.hits]))
        assert versions[0]["receipt"]["candidate_id"] != versions[1]["receipt"]["candidate_id"]
        assert versions[0]["receipt"]["corpus_id"] != versions[1]["receipt"]["corpus_id"]
        assert versions[0]["receipt"]["index_id"] != versions[1]["receipt"]["index_id"]
        assert versions[0]["receipt"]["encoded"] == 4
        assert versions[1]["receipt"]["encoded"] == 1 and versions[1]["receipt"]["vectors_reused"] == 3
        phase = "index_replay_C1"
        before = len(evidence["backend_batches"])
        repeat, repeat_time = timed(lambda: index.update(captures["C1"]))
        repeated_response, repeat_search_time = timed(lambda: index.search("add calculation function test", k=2))
        assert repeat.encoded == 0 and repeat.vectors_reused == 4
        assert repeat.rebuilt == 0 and repeat.reused == 2
        assert repeated_response.status == "ready" and repeated_response.index_id == repeat.index_id
        assert len(evidence["backend_batches"]) == before
        evidence["workspace_index"] = dict(versions=versions, candidate_and_index_changed=True,
            replay_receipt=asdict(repeat), replay_seconds=repeat_time,
            replay_search_seconds=repeat_search_time, replay_backend_calls=0)
        # Inspect normalized index vectors without exporting the arrays.
        matrix = index._state.matrix
        assert matrix is not None
        evidence["workspace_index"]["matrix"] = batch_record(
            workspace_encoder.EncodedBatch(matrix, 0, 0, ()), 0)
        encoder.close()
        phase = "disk_cache_reopen"

        def forbidden_load():
            raise AssertionError("persisted cache unexpectedly reloaded model")

        reopened = WorkspaceEncoder(config, forbidden_load, cache_root=args.cache_root)
        try:
            disk, disk_time = timed(lambda: reopened.encode(docs, role="document"))
            assert disk.encoded == 0 and disk.reused == 2 and np.array_equal(disk.vectors, first.vectors)
            evidence["disk_reopen"] = batch_record(disk, disk_time)
            evidence["disk_reopen"]["exact_match"] = True
        finally:
            reopened.close()
        evidence["status"] = "passed"
    except Exception as exc:
        evidence["status"] = "failed"
        evidence["failure"] = dict(phase=phase, type=type(exc).__name__)
        raise
    finally:
        encoder.close()
        evidence["finished_at"] = datetime.now(timezone.utc).isoformat()
        evidence["gpu"]["peak_allocated_bytes"] = torch.cuda.max_memory_allocated()
        evidence["gpu"]["peak_reserved_bytes"] = torch.cuda.max_memory_reserved()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(dict(status=evidence["status"], gpu=evidence["gpu"]["name"],
                              load_seconds=evidence.get("load_seconds"),
                              output=args.output.resolve().relative_to(ROOT).as_posix())), flush=True)


if __name__ == "__main__":
    main()
