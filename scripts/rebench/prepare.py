"""Batch, resumable ingestion of SWE-rebench tasks into the clean-image path. No model calls.

For each task, in stages that are recorded in runs/rebench/work/<id>/state.json and skipped on a rerun:

    source       GitHub archive at base_commit and its manifest
    task_files   public/private task files in the sprint5 format
    image        official image pinned by digest, clean image derived FROM that digest (source moved to
                 /opt/repo without .git, /workspace empty, /testbed pointing at /workspace)
    isolation    answer-isolation checks from build_clean_baseline
    calibration  ImageGrader with the rebench options: collect, repair truncated node ids, grade the empty
                 delta and the gold delta several times, exclude P2P that fail under gold in the sandbox
    outputs      receipt and staging trees under runs/rebench/work/<id>/, clean record under
                 docs/rebench/clean-images/, manifest row in configs/rebench/tasks.yaml

Images are referenced by digest only: swerebench republishes :latest.

    prepare.py [--repeats N] [--redo STAGE ...] [--ids-file F] [ids...]   (default ids: the five pilot tasks)
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.run_swe_task import parse_id_list, usable_nodeid  # noqa: E402

DATA = ROOT / "runs" / "rebench" / "data" / "test-0.parquet"
WORK = ROOT / "runs" / "rebench" / "work"
PUBLIC = ROOT / "runs" / "rebench" / "public"
PRIVATE = ROOT / "runs" / "rebench" / "private"
CLEAN_RECORDS = ROOT / "docs" / "rebench" / "clean-images"
MANIFEST = ROOT / "configs" / "rebench" / "tasks.yaml"
CONTEXT = "desktop-linux"
PILOT = ["pypa__twine-1309", "delgan__loguru-1451", "pycqa__isort-2491", "holoviz__param-1117", "pypa__build-1027"]
CONDA_PYTHON = "/opt/conda/envs/testbed/bin/python"
GRADER_TIMEOUT = 600
SCAN_TIMEOUT = 3600  # the answer-isolation scan reads every file of the image, archives included
DIGEST = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
STAGES = ("source", "task_files", "image", "isolation", "calibration", "memory", "outputs")


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def docker(*argv: str, timeout: int = 600) -> str:
    result = subprocess.run(["docker", "--context", CONTEXT, *argv], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout)
    if result.returncode:
        raise RuntimeError(" ".join(argv[:3]) + ": " + result.stderr[-1000:])
    return result.stdout


def pinned(ref) -> str:
    if not isinstance(ref, str) or not DIGEST.match(ref):
        raise ValueError("image must be pinned by sha256 digest: " + repr(ref))
    return ref


# ---- resumable stages ----

def run_stages(path: Path, stages, redo=()) -> dict:
    """Run (name, fn(state) -> dict) stages in order, skipping finished ones. fn results merge into state."""
    state = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"done": []}
    redo = set(redo)
    if redo:
        names = [name for name, _ in stages]
        first = min(names.index(name) for name in redo if name in names)
        state["done"] = [name for name in state["done"] if names.index(name) < first]
    state.pop("error", None)
    for name, fn in stages:
        if name in state["done"]:
            continue
        try:
            state.update(fn(state) or {})
        except Exception as exc:
            state["error"] = {"stage": name, "message": type(exc).__name__ + ": " + str(exc)[-2000:]}
            _save(path, state)
            raise
        state["done"].append(name)
        _save(path, state)
    return state


def _save(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


# ---- source and task files ----

def load_row(instance_id: str) -> dict:
    import pandas as pd

    df = pd.read_parquet(DATA)
    row = df[df["instance_id"] == instance_id].iloc[0].to_dict()
    for key in ("FAIL_TO_PASS", "PASS_TO_PASS"):
        row[key] = json.dumps(parse_id_list(list(row[key]) if not isinstance(row[key], str) else row[key]))
    install = row.get("install_config")
    row["install_config"] = {k: (v.tolist() if hasattr(v, "tolist") else v) for k, v in (install or {}).items()}
    row["created_at"] = str(row["created_at"])
    return row


def source_stage(row: dict, work: Path):
    from scripts.sprint5_prepare_main import download_archive

    def run(state):
        archive = work / "source.tar.gz"
        download_archive({"repo": row["repo"], "base_commit": row["base_commit"]}, archive)
        sha = hashlib.sha256(archive.read_bytes()).hexdigest()
        (work / "source-receipt.json").write_text(json.dumps(
            {"repo": row["repo"], "base_commit": row["base_commit"], "archive": "source.tar.gz", "sha256": sha},
            indent=2) + "\n", encoding="utf-8")
        return {"archive_sha256": sha}
    return run


def write_task_files(row: dict) -> dict:
    issue = row["problem_statement"]
    public = {
        "task_id": row["instance_id"],
        "issue_text": issue,
        "issue_sha256": hashlib.sha256(issue.encode("utf-8")).hexdigest(),
        "repo_ref": {"repo_id": row["repo"], "revision": row["base_commit"], "revision_kind": "commit"},
    }
    private = {"instance_id": row["instance_id"], **{key: row[key] for key in ("FAIL_TO_PASS", "PASS_TO_PASS", "patch", "test_patch")}}
    for folder, value in ((PUBLIC, public), (PRIVATE, private)):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / (row["instance_id"] + ".json")).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    haystack = issue + "\n" + json.dumps(public)
    leaks = [key for key in ("patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS", "hints_text")
             if key in public or (str(row.get(key) or "").strip() not in ("", "[]")
                                  and str(row[key])[:200] in haystack)]
    if leaks:
        raise ValueError("public task contains private fields: " + ", ".join(leaks))
    return {"task_files": {"public": rel(PUBLIC / (row["instance_id"] + ".json")),
                           "private": rel(PRIVATE / (row["instance_id"] + ".json")),
                           "public_keys": sorted(public)}}


# ---- images ----

def official_digest(image: str) -> dict:
    """The local official image's RepoDigest for its own repository. Pulls the image when it is not local."""
    if not image_present(image):
        docker("pull", "-q", image, timeout=7200)
    inspect = json.loads(docker("image", "inspect", image))[0]
    repo = image.rsplit(":", 1)[0] if ":" in image.split("/")[-1] else image
    digests = [d for d in inspect.get("RepoDigests") or [] if d.split("@", 1)[0] == repo]
    if not digests:
        raise ValueError("official image has no RepoDigest for " + repo + "; pull it first")
    return {"digest": pinned(digests[0]), "size_bytes": inspect.get("Size")}


def official_git_state(image: str) -> dict:
    script = ("cd /testbed && echo head=$(git rev-parse HEAD) && echo refs=$(git for-each-ref | wc -l) && "
              "echo reflog=$(git reflog 2>/dev/null | wc -l) && "
              "echo unreachable=$(git fsck --unreachable --no-reflogs 2>/dev/null | wc -l) && "
              "echo dirty=$(git status --porcelain | wc -l) && echo stash=$(git stash list | wc -l)")
    out = docker("run", "--rm", "--network", "none", "--entrypoint", "/bin/bash", image, "-c", script)
    state = dict(line.split("=", 1) for line in out.split() if "=" in line)
    return {k: (v if k == "head" else int(v)) for k, v in state.items()}


def dockerfile(base: str) -> str:
    return (
        f"FROM {pinned(base)}\n"
        "USER root\n"
        "COPY original-files.json /tmp/original-files.json\n"
        "RUN set -eux; test ! -e /opt/repo; mkdir -p /opt/repo; cp -a /testbed/. /opt/repo/; "
        "rm -rf /opt/repo/.git /testbed /workspace; mkdir /workspace; ln -s /workspace /testbed; "
        f"ln -sf {CONDA_PYTHON} /usr/local/bin/python\n"
        "RUN /usr/local/bin/python - <<'PY'\n"
        "import json, pathlib\n"
        "root = pathlib.Path('/opt/repo')\n"
        "original = set(json.loads(pathlib.Path('/tmp/original-files.json').read_text()))\n"
        "found, extra = [], []\n"
        "for path in root.rglob('*'):\n"
        "    if path.is_file() and not path.is_symlink():\n"
        "        rel = path.relative_to(root).as_posix()\n"
        "        if rel not in original:\n"
        "            extra.append(rel)\n"
        "            if rel.endswith('.so'):\n"
        "                found.append(rel)\n"
        "pathlib.Path('/opt/native-artifacts.json').write_text(json.dumps(sorted(found)))\n"
        "pathlib.Path('/opt/repo-extra-files.json').write_text(json.dumps(sorted(extra)))\n"
        "pathlib.Path('/tmp/original-files.json').unlink()\n"
        "PY\n"
        "RUN /usr/local/bin/python -m pip freeze --all > /opt/grader-environment.txt\n"
        "WORKDIR /workspace\n"
        "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/workspace:/workspace/src\n"
    )


def clean_tag(instance_id: str) -> str:
    return "coding-harness-rebench-clean:" + instance_id.replace("__", "-").lower()


def image_stage(row: dict, work: Path):
    from scripts.preflight_a5_repo_baseline import image_build
    from src.eval.a5_image_tree import archive_manifest

    def run(state):
        official = official_digest(row["docker_image"])
        git = official_git_state(official["digest"])
        manifest = archive_manifest(work / "source.tar.gz", row["repo"], row["base_commit"])
        context = work / "context"
        if context.exists():
            shutil.rmtree(context)
        context.mkdir(parents=True)
        files = sorted(path for path, meta in manifest["entries"].items() if meta.get("kind") == "file")
        (context / "original-files.json").write_text(json.dumps(files), encoding="utf-8")
        text = dockerfile(official["digest"])
        (context / "Dockerfile").write_bytes(text.encode("utf-8"))
        log = work / "build.log"
        log.unlink(missing_ok=True)
        log.with_suffix(".json").unlink(missing_ok=True)
        digest, inspect = image_build(context, clean_tag(row["instance_id"]), log)
        return {"image": {"official_image": row["docker_image"], "official_digest": official["digest"],
                          "official_size_bytes": official["size_bytes"], "clean_tag": clean_tag(row["instance_id"]),
                          "clean_digest": pinned(digest), "clean_id": inspect.get("Id"),
                          "clean_size_bytes": inspect.get("Size"),
                          "build_input_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()},
                "official_git_state": git, "official_head_matches_base": git.get("head") == row["base_commit"]}
    return run


def image_present(digest: str) -> bool:
    try:
        docker("image", "inspect", digest, timeout=60)
        return True
    except RuntimeError:
        return False


# ---- isolation ----

def isolation_stage(row: dict, work: Path):
    from scripts.build_clean_baseline import docker_python, repo_packages, scan_needles
    from scripts.rebench.provenance import RULE, classify_hits, provenance, source_check
    from src.eval.a5_image_tree import archive_manifest

    def run(state):
        image = state["image"]["clean_digest"]
        archive = work / "source.tar.gz"
        manifest = archive_manifest(archive, row["repo"], row["base_commit"])
        source = source_check(image, archive, manifest)
        source_ok = source["match"]
        source_hash = manifest["source_tree_hash"] if source_ok else "mismatch:" + str(source["mismatch_count"])
        needles, found = scan_needles(image, archive, row["test_patch"] or "", row["patch"] or "", timeout=SCAN_TIMEOUT)
        failing, excused = classify_hits(found, provenance(image, found), repo_packages(manifest))
        if not needles:
            failing = ["no-unique-needle"]
        scan = {"rule": RULE, "needle_count": len(needles), "raw_hit_count": len(found), "hits": failing[:50],
                "hit_count": len(failing), "excused": excused[:200], "excused_count": len(excused),
                "pass": not failing}
        layout = json.loads(docker_python(image, (
            "import json, os, pathlib\n"
            "w = pathlib.Path('/workspace'); t = pathlib.Path('/testbed')\n"
            "extra = json.loads(pathlib.Path('/opt/repo-extra-files.json').read_text())\n"
            "print(json.dumps({'workspace_entries': len(list(w.iterdir())), 'testbed_link': os.readlink(t) if t.is_symlink() else None,"
            " 'repo_git': pathlib.Path('/opt/repo/.git').exists(), 'repo_extra_files': len(extra), 'repo_extra_sample': extra[:8],"
            " 'natives': json.loads(pathlib.Path('/opt/native-artifacts.json').read_text())}))\n")))
        passed = bool(source_ok and scan.get("pass") and layout["workspace_entries"] == 0 and not layout["repo_git"])
        return {"isolation": {"rule": RULE, "source_only_match": source_ok, "source_check": source,
                              "source_hash": source_hash, "filesystem_scan": scan, "layout": layout,
                              "answer_isolation_pass": passed}}
    return run


# ---- calibration ----

def staging(row: dict, work: Path) -> tuple[dict, dict, list[str], list[str]]:
    from scripts.preflight_a5_repo_baseline import apply_patch_tree, patch_paths

    root = work / "staging"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    apply_patch_tree(work / "source.tar.gz", row["repo"], row["base_commit"], row["test_patch"] or "", row["patch"] or "", root)
    test_paths = patch_paths(row["test_patch"] or "")
    overlay = {}
    for relative in test_paths:
        updated = root / "baseline" / relative
        if not updated.is_file():
            continue
        before = root / "original" / relative
        if updated.read_bytes() != (before.read_bytes() if before.is_file() else b""):
            overlay[relative] = base64.b64encode(updated.read_bytes()).decode("ascii")
    gold_paths = [p for p in patch_paths(row["patch"] or "") if p not in test_paths]
    changed = {p: base64.b64encode((root / "known-good" / p).read_bytes()).decode("ascii")
               for p in gold_paths if (root / "known-good" / p).is_file()}
    deleted = [p for p in gold_paths if not (root / "known-good" / p).exists()]
    return overlay, {"changed": changed, "deleted": deleted}, test_paths, gold_paths


def outcome_counts(result: dict, f2p: list[str], p2p: list[str]) -> dict:
    by_id = {case.get("nodeid"): case.get("outcome") for case in result.get("cases") or []}
    passed = lambda nodes: sum(1 for n in nodes if by_id.get(n) == "passed")  # noqa: E731
    return {"status": result.get("status"), "reason": result.get("reason"),
            "f2p": [passed(f2p), len(f2p)], "p2p": [passed(p2p), len(p2p)],
            "f2p_outcomes": {n: by_id.get(n) for n in f2p},
            "p2p_not_passed": sorted(n for n in p2p if by_id.get(n) != "passed"),
            "execution_status": result.get("execution_status")}


class GraderInfraError(RuntimeError):
    """The grader failed before any test ran. Not a property of the task; the stage is retried later."""


async def evaluate_retrying(grader, attempts: int = 3, **kwargs) -> dict:
    """ImageGrader.evaluate, repeated while it fails before test execution (Docker create/exec errors)."""
    for _ in range(attempts):
        result = await grader.evaluate(**kwargs)
        if not (result.get("execution_status") is None and result.get("reason")):
            return result
        await asyncio.sleep(10)
    raise GraderInfraError("grader failed before test execution: " + str(result.get("reason"))[:300])


def failed_clauses(*, isolation_ok: bool, runs: dict, stable: bool, repaired: bool, excluded: int, p2p: int) -> list[int]:
    """Exclusion clauses of the pilot design (section 3.3) that a calibrated task fails."""
    clauses = []
    if not isolation_ok:
        clauses.append(2)
    if not repaired or not all(r["f2p"][1] > 0 and r["f2p"][0] == r["f2p"][1] for r in runs["gold"]):
        clauses.append(3)
    if not all(r["f2p"][1] > 0 and r["f2p"][0] == 0 for r in runs["empty"]):
        clauses.append(4)
    if not stable:
        clauses.append(5)
    if excluded * 2 > p2p:
        clauses.append(8)
    return clauses


def calibration_stage(row: dict, work: Path, repeats: int, keep_original_files: bool = False):
    from src.eval.image_grader import (ImageGrader, pytest_options_from_test_cmd, repair_truncated_nodeids,
                                       sandbox_p2p_exclusions)

    async def calibrate(state):
        image = state["image"]["clean_digest"]
        overlay, gold, test_paths, gold_paths = staging(row, work)
        options = pytest_options_from_test_cmd(row["install_config"].get("test_cmd"))
        grader = ImageGrader(context=CONTEXT)
        common = dict(image=image, test_overlay=overlay, autoload_plugins=True, pytest_options=options,
                      keep_original_files=keep_original_files)
        raw_f2p = [n for n in parse_id_list(row["FAIL_TO_PASS"]) if usable_nodeid(n)]
        raw_p2p = [n for n in parse_id_list(row["PASS_TO_PASS"]) if usable_nodeid(n)]
        files = sorted({n.split("::", 1)[0] for n in raw_f2p + raw_p2p if n.split("::", 1)[0].endswith(".py")})
        collect_args = {k: v for k, v in common.items() if k != "keep_original_files"}
        collection = await grader.collect(paths=files, timeout=GRADER_TIMEOUT, **collect_args)
        collected = collection.get("collected") or []
        if not collected and collection.get("reason"):
            raise GraderInfraError("collection failed before running: " + str(collection["reason"])[:300])
        f2p, f2p_notes = repair_truncated_nodeids(raw_f2p, collected)
        p2p, p2p_notes = repair_truncated_nodeids(raw_p2p, collected)
        runs = {"empty": [], "gold": []}
        raw_gold = []
        for _ in range(repeats):
            for label, delta in (("empty", {"changed": {}, "deleted": []}), ("gold", gold)):
                result = await evaluate_retrying(grader, delta=delta, f2p=f2p, p2p=p2p, timeout=GRADER_TIMEOUT,
                                                 **common)
                runs[label].append(outcome_counts(result, f2p, p2p))
                if label == "gold":
                    raw_gold.append(result)
        excluded = sandbox_p2p_exclusions(raw_gold, p2p)
        empty_fails = all(r["f2p"][1] > 0 and r["f2p"][0] == 0 for r in runs["empty"])
        gold_ok = all(r["f2p"][1] > 0 and r["f2p"][0] == r["f2p"][1] for r in runs["gold"])
        stable = all(len({json.dumps(r["f2p_outcomes"], sort_keys=True) for r in runs[k]}) == 1 for k in runs)
        repaired = all(note["match_count"] for note in f2p_notes)
        clauses = failed_clauses(isolation_ok=state["isolation"]["answer_isolation_pass"], runs=runs, stable=stable,
                                 repaired=repaired, excluded=len(excluded), p2p=len(p2p))
        accepted = not clauses
        last_empty, last_gold = runs["empty"][-1], runs["gold"][-1]
        return {"calibration": {
            "keep_original_files": keep_original_files, "failed_clauses": clauses,
            "options": options, "collected": len(collected), "collect_reason": collection.get("reason"),
            "f2p_original": len(raw_f2p), "p2p_original": len(raw_p2p), "f2p": f2p, "p2p": p2p,
            "f2p_repaired": f2p_notes, "p2p_repaired": p2p_notes, "p2p_excluded": excluded,
            "repeats": repeats, "runs": runs, "test_paths": test_paths, "gold_paths": gold_paths,
            "empty_f2p_fails_every_run": empty_fails, "gold_f2p_passes_every_run": gold_ok,
            "f2p_outcomes_stable": stable, "accepted": accepted,
            "summary": {"empty_f2p": last_empty["f2p"], "empty_p2p": last_empty["p2p"],
                        "gold_f2p": last_gold["f2p"], "gold_p2p": last_gold["p2p"]}}}

    def run(state):
        return asyncio.run(calibrate(state))
    return run


ONE_GIB, TWO_GIB = 1024**3, 2 * 1024**3


def memory_stage(row: dict, work: Path, keep_original_files: bool = False):
    """Agent containers get 1 GiB. Grade the gold delta once at 1 GiB; when the outcome differs from the
    2 GiB calibration, the task gets 2 GiB in every group (pilot design section 3.3, clause 6)."""
    from src.eval.image_grader import ImageGrader

    async def probe(state):
        cal = state["calibration"]
        if not cal["accepted"]:
            return {"memory": {"probed": False, "memory_bytes": None}}
        overlay, gold, _, _ = staging(row, work)
        result = await evaluate_retrying(
            ImageGrader(context=CONTEXT), image=state["image"]["clean_digest"], delta=gold, test_overlay=overlay, f2p=cal["f2p"], p2p=cal["p2p"],
            timeout=GRADER_TIMEOUT, autoload_plugins=True, pytest_options=cal["options"],
            keep_original_files=keep_original_files, memory_bytes=ONE_GIB)
        counts = outcome_counts(result, cal["f2p"], cal["p2p"])
        reference = cal["runs"]["gold"][-1]
        same = counts["f2p"] == reference["f2p"] and counts["p2p_not_passed"] == reference["p2p_not_passed"]
        return {"memory": {"probed": True, "one_gib": counts, "matches_two_gib": same,
                           "memory_bytes": ONE_GIB if same else TWO_GIB}}

    def run(state):
        return asyncio.run(probe(state))
    return run


# ---- outputs ----

def clean_record(record: dict) -> dict:
    iso, image = record["isolation"], record["image"]
    return {"task_id": record["instance_id"], "answer_isolation_pass": iso["answer_isolation_pass"],
            "clean_image_digest": pinned(image["clean_digest"]), "clean_image_id": image.get("clean_id"),
            "official_image_digest": pinned(image["official_digest"]), "source_only_match": iso["source_only_match"],
            "image_source_only_hash": iso.get("source_hash"), "filesystem_scan": iso.get("filesystem_scan"),
            "native_artifacts": (iso.get("layout") or {}).get("natives"),
            "build_input_sha256": image.get("build_input_sha256")}


def receipt(record: dict, *, private_sha256: str) -> dict:
    work = WORK / record["instance_id"]
    cal = record["calibration"]
    return {"task_id": record["instance_id"], "repo": record["repo"], "base_revision": record["base_commit"],
            "baseline_image_digest": pinned(record["image"]["clean_digest"]),
            "official_image_digest": pinned(record["image"]["official_digest"]),
            "private_source_binding": {"path": str(ROOT / record["task_files"]["private"]), "sha256": private_sha256,
                                       "test_patch_paths": cal["test_paths"], "gold_patch_paths": cal["gold_paths"]},
            "source_attempt_ref": str(work / "source-receipt.json"), "model_calls": 0, "paid": False}


def manifest_row(record: dict) -> dict:
    task_id, cal = record["instance_id"], record["calibration"]
    work = WORK / task_id
    command = " ".join(["python", "-m", "pytest", *cal["options"], "<test path>"])
    return {"id": task_id, "repo": record["repo"], "family": "rebench",
            "created_at": record["created_at"], "base_commit": record["base_commit"],
            "receipt": rel(work / "receipt.json"), "original": rel(work / "staging" / "original"),
            "baseline": rel(work / "staging" / "baseline"), "public_task": str(ROOT / record["task_files"]["public"]),
            "clean_record": rel(CLEAN_RECORDS / (task_id + ".json")),
            "official_image_digest": pinned(record["image"]["official_digest"]),
            "grader_timeout_seconds": GRADER_TIMEOUT, "test_command": command,
            "grader": {"autoload_plugins": True, "pytest_options": list(cal["options"])},
            "grader_nodeids": {"f2p": list(cal["f2p"]), "p2p": list(cal["p2p"])},
            "p2p_excluded": list(cal["p2p_excluded"]), "calibration": dict(cal["summary"]),
            **({"export_keep_original_files": True} if cal.get("keep_original_files") else {}),
            **({"memory_bytes": record["memory"]["memory_bytes"]} if (record.get("memory") or {}).get("memory_bytes") else {})}


def merge_manifest(path: Path, rows: list[dict], drop=()) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else None
    data = data or {"schema": "rebench.tasks.v1",
                    "notes": "SWE-rebench tasks prepared by scripts/rebench/prepare.py. Images pinned by digest."}
    tasks = {task["id"]: task for task in data.get("tasks") or [] if task["id"] not in set(drop)}
    tasks.update({row["id"]: row for row in rows})
    data["tasks"] = [tasks[key] for key in sorted(tasks)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=200), encoding="utf-8")


def outputs_stage(row: dict, work: Path, manifest: Path = MANIFEST):
    def run(state):
        record = {**state, "instance_id": row["instance_id"], "repo": row["repo"], "base_commit": row["base_commit"],
                  "created_at": row["created_at"]}
        private = ROOT / state["task_files"]["private"]
        (work / "receipt.json").write_text(json.dumps(
            receipt(record, private_sha256=hashlib.sha256(private.read_bytes()).hexdigest()), indent=2) + "\n",
            encoding="utf-8")
        CLEAN_RECORDS.mkdir(parents=True, exist_ok=True)
        (CLEAN_RECORDS / (row["instance_id"] + ".json")).write_text(
            json.dumps(clean_record(record), indent=2) + "\n", encoding="utf-8")
        if state["calibration"]["accepted"]:
            merge_manifest(manifest, [manifest_row(record)])
        else:
            merge_manifest(manifest, [], drop={row["instance_id"]})
        return {"accepted": state["calibration"]["accepted"]}
    return run


def recheck_isolation_state(state: dict, isolation) -> bool:
    """Rerun the isolation stage under the current rule and redo clause 2 in place; calibration is kept.
    The earlier isolation result is kept in isolation_history. True when the state changed."""
    from scripts.rebench.provenance import RULE

    if "isolation" not in state.get("done", []) or (state.get("isolation") or {}).get("rule") == RULE:
        return False
    history = state.get("isolation_history") or []
    history.append(state["isolation"])
    state.update(isolation(state))
    state["isolation_history"] = history
    cal = state.get("calibration")
    if "calibration" in state["done"] and cal and "failed_clauses" in cal:
        clauses = [c for c in cal["failed_clauses"] if c != 2]
        if not state["isolation"]["answer_isolation_pass"]:
            clauses.append(2)
        cal["failed_clauses"] = sorted(clauses)
        cal["accepted"] = not clauses
    return True


def prepare_task(instance_id: str, repeats: int, redo=(), *, keep_original_files: bool = False,
                 manifest: Path = MANIFEST, recheck_isolation: bool = False) -> dict:
    row = load_row(instance_id)
    work = WORK / instance_id
    work.mkdir(parents=True, exist_ok=True)
    path = work / "state.json"
    redo = set(redo)
    if path.is_file():
        state = json.loads(path.read_text(encoding="utf-8"))
        digest = (state.get("image") or {}).get("clean_digest")
        if "image" in state.get("done", []) and (not digest or not image_present(digest)):
            redo.add("image")
        if "calibration" in state.get("done", []) and (
                bool((state.get("calibration") or {}).get("keep_original_files")) != keep_original_files
                or "failed_clauses" not in state["calibration"]):
            redo.add("calibration")
        if "outputs" in state.get("done", []) and state.get("manifest") != rel(manifest):
            redo.add("outputs")
    if recheck_isolation and path.is_file():
        state = json.loads(path.read_text(encoding="utf-8"))
        if recheck_isolation_state(state, isolation_stage(row, work)):
            _save(path, state)
            redo.add("memory")
    stages = [("source", source_stage(row, work)),
              ("task_files", lambda state: write_task_files(row)),
              ("image", image_stage(row, work)),
              ("isolation", isolation_stage(row, work)),
              ("calibration", calibration_stage(row, work, repeats, keep_original_files)),
              ("memory", memory_stage(row, work, keep_original_files)),
              ("outputs", lambda state: {**outputs_stage(row, work, manifest)(state), "manifest": rel(manifest)})]
    return run_stages(path, stages, redo=redo)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--redo", action="append", default=[], choices=STAGES, help="rerun from this stage")
    parser.add_argument("--ids-file", type=Path, help="one instance id per line")
    parser.add_argument("--keep-original-files", action="store_true",
                        help="export rule export_keep_original_files for calibration; recorded in the manifest row")
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("ids", nargs="*")
    args = parser.parse_args()
    ids = list(args.ids)
    if args.ids_file:
        ids += [line.strip() for line in args.ids_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    manifest = args.manifest if args.manifest.is_absolute() else ROOT / args.manifest
    results = {}
    for instance_id in ids or PILOT:
        print("==", instance_id, flush=True)
        try:
            state = prepare_task(instance_id, args.repeats, args.redo, keep_original_files=args.keep_original_files,
                                 manifest=manifest)
            cal = state["calibration"]
            results[instance_id] = {"accepted": state["accepted"], **cal["summary"],
                                    "failed_clauses": cal["failed_clauses"], "memory_bytes": state["memory"]["memory_bytes"],
                                    "p2p_excluded": len(cal["p2p_excluded"])}
        except Exception as exc:
            results[instance_id] = {"error": type(exc).__name__ + ": " + str(exc)[-500:]}
        print(instance_id, json.dumps(results[instance_id]), flush=True)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
