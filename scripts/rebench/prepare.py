"""Pilot ingestion of SWE-rebench leaderboard tasks into the existing clean-image path. No model calls.

For each task: download the GitHub archive at base_commit, write public/private task files in the
sprint5 format, derive a clean image from the official swerebench image (source moved to /opt/repo
without .git, /workspace left empty, /testbed pointing at /workspace), run the answer-isolation checks
from build_clean_baseline, then grade the empty delta and the gold delta with the unchanged ImageGrader
several times.

Usage: python scripts/rebench/prepare.py [--repeats N] [--skip-build] [ids...]
Large artifacts go under runs/rebench/; per-task result records go to scripts/rebench/out/pilot/.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.build_clean_baseline import docker_python, filesystem_scan, source_only_check  # noqa: E402
from scripts.calibrate_clean_image import counts  # noqa: E402
from scripts.preflight_a5_repo_baseline import apply_patch_tree, image_build, patch_paths  # noqa: E402
from scripts.rebench.grader import RebenchGrader, repair_nodeids, test_cmd_options  # noqa: E402
from scripts.run_swe_task import parse_id_list, usable_nodeid  # noqa: E402
from scripts.sprint5_prepare_main import download_archive  # noqa: E402
from src.eval.a5_image_tree import archive_manifest  # noqa: E402
from src.eval.image_grader import ImageGrader  # noqa: E402

DATA = ROOT / "runs" / "rebench" / "data" / "test-0.parquet"
WORK = ROOT / "runs" / "rebench" / "work"
PUBLIC = ROOT / "runs" / "rebench" / "public"
PRIVATE = ROOT / "runs" / "rebench" / "private"
OUT = Path(__file__).resolve().parent / "out" / "pilot"
CONTEXT = "desktop-linux"
PILOT = ["pypa__twine-1309", "delgan__loguru-1451", "pycqa__isort-2491", "holoviz__param-1117", "pypa__build-1027"]
CONDA_PYTHON = "/opt/conda/envs/testbed/bin/python"


def docker(*argv: str, timeout: int = 600) -> str:
    result = subprocess.run(["docker", "--context", CONTEXT, *argv], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout)
    if result.returncode:
        raise RuntimeError(" ".join(argv[:3]) + ": " + result.stderr[-1000:])
    return result.stdout


def load_row(instance_id: str) -> dict:
    df = pd.read_parquet(DATA)
    row = df[df["instance_id"] == instance_id].iloc[0].to_dict()
    for key in ("FAIL_TO_PASS", "PASS_TO_PASS"):
        row[key] = json.dumps(parse_id_list(list(row[key]) if not isinstance(row[key], str) else row[key]))
    return row


def write_task_files(row: dict) -> dict:
    issue = row["problem_statement"]
    public = {
        "task_id": row["instance_id"],
        "issue_text": issue,
        "issue_sha256": hashlib.sha256(issue.encode("utf-8")).hexdigest(),
        "repo_ref": {"repo_id": row["repo"], "revision": row["base_commit"], "revision_kind": "commit"},
    }
    private = {key: row[key] for key in ("FAIL_TO_PASS", "PASS_TO_PASS", "patch", "test_patch")}
    private = {"instance_id": row["instance_id"], **private}
    for folder, value in ((PUBLIC, public), (PRIVATE, private)):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / (row["instance_id"] + ".json")).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    leaks = [key for key in ("patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS", "hints_text")
             if key in public or (row.get(key) and str(row[key])[:200] in json.dumps(public))]
    return {"public": str((PUBLIC / (row["instance_id"] + ".json")).relative_to(ROOT)),
            "private": str((PRIVATE / (row["instance_id"] + ".json")).relative_to(ROOT)),
            "public_keys": sorted(public), "public_contains_private_fields": leaks}


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
        f"FROM {base}\n"
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


def build_clean(row: dict, archive: Path, manifest: dict) -> dict:
    official = row["docker_image"]
    inspect = json.loads(docker("image", "inspect", official))[0]
    base = (inspect.get("RepoDigests") or [official])[0]
    work = WORK / row["instance_id"] / "context"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    files = sorted(path for path, meta in manifest["entries"].items() if meta.get("kind") == "file")
    (work / "original-files.json").write_text(json.dumps(files), encoding="utf-8")
    (work / "Dockerfile").write_bytes(dockerfile(base).encode("utf-8"))
    tag = "coding-harness-rebench-clean:" + row["instance_id"].replace("__", "-").lower()
    log = WORK / row["instance_id"] / "build.log"
    if log.exists():
        log.unlink()
    log.with_suffix(".json").unlink(missing_ok=True)
    digest, clean_inspect = image_build(work, tag, log)
    return {"official_image": official, "official_digest": base, "official_size_bytes": inspect.get("Size"),
            "clean_tag": tag, "clean_digest": digest, "clean_id": clean_inspect.get("Id"),
            "clean_size_bytes": clean_inspect.get("Size")}


def isolation(row: dict, image: dict, archive: Path, manifest: dict) -> dict:
    tag = image["clean_tag"]
    source_ok, source_hash = source_only_check(tag, manifest)
    scan = filesystem_scan(tag, archive, manifest, row["test_patch"] or "", row["patch"] or "")
    layout = json.loads(docker_python(tag, (
        "import json, os, pathlib\n"
        "w = pathlib.Path('/workspace'); t = pathlib.Path('/testbed')\n"
        "extra = json.loads(pathlib.Path('/opt/repo-extra-files.json').read_text())\n"
        "print(json.dumps({'workspace_entries': len(list(w.iterdir())), 'testbed_link': os.readlink(t) if t.is_symlink() else None,"
        " 'repo_git': pathlib.Path('/opt/repo/.git').exists(), 'repo_extra_files': len(extra), 'repo_extra_sample': extra[:8],"
        " 'natives': json.loads(pathlib.Path('/opt/native-artifacts.json').read_text())}))\n")))
    return {"source_only_match": source_ok, "source_hash": source_hash, "filesystem_scan": scan, "layout": layout,
            "answer_isolation_pass": bool(source_ok and scan.get("pass") and layout["workspace_entries"] == 0
                                          and not layout["repo_git"])}


def overlay_and_gold(row: dict, archive: Path) -> tuple[dict, dict, list[str]]:
    staging = WORK / row["instance_id"] / "staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    apply_patch_tree(archive, row["repo"], row["base_commit"], row["test_patch"] or "", row["patch"] or "", staging)
    test_paths = patch_paths(row["test_patch"] or "")
    overlay = {}
    for relative in test_paths:
        updated = staging / "baseline" / relative
        if not updated.is_file():
            continue
        before = staging / "original" / relative
        if updated.read_bytes() != (before.read_bytes() if before.is_file() else b""):
            overlay[relative] = base64.b64encode(updated.read_bytes()).decode("ascii")
    changed = {}
    for relative in patch_paths(row["patch"] or ""):
        path = staging / "known-good" / relative
        if relative not in test_paths and path.is_file():
            changed[relative] = base64.b64encode(path.read_bytes()).decode("ascii")
    deleted = [rel for rel in patch_paths(row["patch"] or "")
               if rel not in test_paths and not (staging / "known-good" / rel).exists()]
    return overlay, {"changed": changed, "deleted": deleted}, test_paths


def nodeid_selection(row: dict, baseline: Path) -> tuple[list[str], list[str], dict]:
    kept, dropped = {}, []
    for side, key in (("f2p", "FAIL_TO_PASS"), ("p2p", "PASS_TO_PASS")):
        kept[side] = []
        for node in parse_id_list(row[key]):
            reason = None
            if not usable_nodeid(node):
                reason = "unusable_nodeid"
            elif node.count("[") != node.count("]"):
                reason = "unbalanced_brackets"
            elif "[" in node and "::" in node[node.find("["):]:
                reason = "parameter_contains_node_separator"
            elif node.split("::", 1)[0].endswith(".py") and not (baseline / node.split("::", 1)[0]).is_file():
                reason = "test_file_missing"
            if reason:
                dropped.append({"side": side, "nodeid": node, "reason": reason})
            else:
                kept[side].append(node)
    report = {"f2p": {"original": len(parse_id_list(row["FAIL_TO_PASS"])), "used": len(kept["f2p"])},
              "p2p": {"original": len(parse_id_list(row["PASS_TO_PASS"])), "used": len(kept["p2p"])},
              "dropped": dropped}
    return kept["f2p"], kept["p2p"], report


def brief(result: dict, f2p: list[str], p2p: list[str]) -> dict:
    c = counts(result, f2p, p2p)
    return {"status": result.get("status"), "reason": result.get("reason"),
            "f2p": [c["f2p_passed"], c["f2p_total"]], "p2p": [c["p2p_passed"], c["p2p_total"]],
            "f2p_outcomes": {n: c["outcomes"][n] for n in f2p},
            "p2p_not_passed": {n: o for n, o in c["outcomes"].items() if n in p2p and o != "passed"},
            "collected": result.get("collected_count"), "collection_errors": (result.get("collection_errors") or [])[:3],
            "runner_error": result.get("runner_error"), "exit_code": result.get("exit_code"),
            "execution_status": result.get("execution_status")}


async def calibrate(row: dict, image: dict, archive: Path, repeats: int) -> dict:
    overlay, gold, test_paths = overlay_and_gold(row, archive)
    f2p, p2p, report = nodeid_selection(row, WORK / row["instance_id"] / "staging" / "baseline")
    empty_delta = {"changed": {}, "deleted": []}
    existing = ImageGrader(context=CONTEXT)
    kwargs = dict(image=image["clean_digest"], test_overlay=overlay, f2p=f2p, p2p=p2p, timeout=600)
    existing_runs = {label: brief(await existing.evaluate(delta=delta, **kwargs), f2p, p2p)
                     for label, delta in (("empty", empty_delta), ("gold", gold))}

    options = test_cmd_options((row["install_config"] or {}).get("test_cmd"))
    grader = RebenchGrader(context=CONTEXT, options=options)
    raw_f2p, raw_p2p = parse_id_list(row["FAIL_TO_PASS"]), parse_id_list(row["PASS_TO_PASS"])
    files = sorted({n.split("::", 1)[0] for n in raw_f2p + raw_p2p if ".py" in n.split("::", 1)[0]})
    collection = await grader.collect(image=image["clean_digest"], overlay=overlay, paths=files)
    f2p, f2p_notes = repair_nodeids([n for n in raw_f2p if usable_nodeid(n)], collection["collected"])
    p2p, p2p_notes = repair_nodeids([n for n in raw_p2p if usable_nodeid(n)], collection["collected"])
    repair = {"options": options, "collected": len(collection["collected"]),
              "collect_errors": collection.get("collection_errors")[:3], "collect_reason": collection.get("reason"),
              "f2p_original": len(raw_f2p), "f2p_used": len(f2p), "p2p_original": len(raw_p2p), "p2p_used": len(p2p),
              "f2p_repaired": f2p_notes, "p2p_repaired": p2p_notes}
    kwargs = dict(image=image["clean_digest"], test_overlay=overlay, f2p=f2p, p2p=p2p, timeout=600)
    runs = {"empty": [], "gold": []}
    for _ in range(repeats):
        for label, delta in (("empty", empty_delta), ("gold", gold)):
            runs[label].append(brief(await grader.evaluate(delta=delta, **kwargs), f2p, p2p))
    empty_fails = all(r["f2p"][1] > 0 and r["f2p"][0] < r["f2p"][1] for r in runs["empty"])
    gold_ok = all(r["f2p"][1] > 0 and r["f2p"][0] == r["f2p"][1] for r in runs["gold"])
    gold_full = all(r["status"] == "pass" for r in runs["gold"])
    stable = all(len({json.dumps(r["f2p_outcomes"], sort_keys=True) for r in runs[k]}) == 1 for k in runs)
    p2p_flaky = sorted({n for r in runs["gold"] for n in r["p2p_not_passed"]})
    return {"test_paths": test_paths, "overlay_files": sorted(overlay), "gold_changed": sorted(gold["changed"]),
            "gold_deleted": gold["deleted"], "existing_grader": {"nodeids": report, "runs": existing_runs},
            "nodeid_repair": repair, "repeats": repeats, "runs": runs,
            "empty_f2p_fails_every_run": empty_fails, "gold_f2p_passes_every_run": gold_ok,
            "gold_full_pass_every_run": gold_full, "f2p_outcomes_stable": stable, "gold_p2p_not_passed": p2p_flaky}


async def one(instance_id: str, repeats: int, skip_build: bool) -> dict:
    row = load_row(instance_id)
    print("==", instance_id, flush=True)
    record = {"instance_id": instance_id, "repo": row["repo"], "created_at": str(row["created_at"]),
              "base_commit": row["base_commit"], "python": (row["install_config"] or {}).get("python"),
              "install": (row["install_config"] or {}).get("install"),
              "test_cmd": (row["install_config"] or {}).get("test_cmd"), "model_calls": 0, "paid": False}
    archive = WORK / instance_id / "source.tar.gz"
    download_archive({"repo": row["repo"], "base_commit": row["base_commit"]}, archive)
    manifest = archive_manifest(archive, row["repo"], row["base_commit"])
    record["task_files"] = write_task_files(row)
    record["official_git_state"] = official_git_state(row["docker_image"])
    record["official_head_matches_base"] = record["official_git_state"].get("head") == row["base_commit"]
    if skip_build:
        tag = "coding-harness-rebench-clean:" + instance_id.replace("__", "-").lower()
        clean_id = json.loads(docker("image", "inspect", tag))[0]["Id"]
        record["image"] = {"official_image": row["docker_image"], "clean_tag": tag,
                           "clean_digest": tag.split(":")[0] + "@" + clean_id}
    else:
        record["image"] = build_clean(row, archive, manifest)
    record["isolation"] = isolation(row, record["image"], archive, manifest)
    print(instance_id, "isolation", record["isolation"]["answer_isolation_pass"], flush=True)
    record["calibration"] = await calibrate(row, record["image"], archive, repeats)
    cal = record["calibration"]
    record["accepted"] = bool(record["isolation"]["answer_isolation_pass"] and cal["empty_f2p_fails_every_run"]
                              and cal["gold_f2p_passes_every_run"] and cal["f2p_outcomes_stable"]
                              and all(n["match_count"] for n in cal["nodeid_repair"]["f2p_repaired"]))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / (instance_id + ".json")).write_text(json.dumps(record, indent=2, default=str) + "\n", encoding="utf-8")
    print(instance_id, "accepted", record["accepted"], "empty", [r["f2p"] for r in cal["runs"]["empty"]],
          "gold", [r["f2p"] for r in cal["runs"]["gold"]], "gold_p2p", [r["p2p"] for r in cal["runs"]["gold"]], flush=True)
    return record


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("ids", nargs="*")
    args = parser.parse_args()
    for instance_id in args.ids or PILOT:
        try:
            await one(instance_id, args.repeats, args.skip_build)
        except Exception as exc:
            OUT.mkdir(parents=True, exist_ok=True)
            (OUT / (instance_id + ".error.json")).write_text(
                json.dumps({"instance_id": instance_id, "error": type(exc).__name__ + ": " + str(exc)[-3000:]}, indent=2)
                + "\n", encoding="utf-8")
            print(instance_id, "ERROR", type(exc).__name__, str(exc)[-800:], flush=True)


if __name__ == "__main__":
    asyncio.run(main())
