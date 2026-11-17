"""Resumable, serial no-model intake of frozen FIX-13 positions in five-position batches.

The approved continuation uses ``python -m scripts.rebench.fix13_prepare --continue-all --fetch-images``.
Without --fetch-images, missing local images stay pending. Docker pull is disabled.
"""
from __future__ import annotations

import argparse
import copy
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

from scripts.rebench import pilot_ingest, prepare
from scripts.rebench import fetch_image_verified as image_fetch
from scripts.rebench.fix13_sample import EXPOSURE, OUT, ROOT

DEST = ROOT / "runs/fix13-prep"
MANIFEST = ROOT / "configs/rebench/fix13-tasks.yaml"
RECORD = ROOT / "scripts/rebench/out/fix13_ingest.json"
EVENTS = DEST / "stages.jsonl"
PDM_TASK = "lmstudio-ai__venvstacks-197"
PDM_REVISION = "fix13-generated-pdm-dir-r1"
CACHE = ROOT / ".tmp/fix13-network/oci"
PROGRESS = ROOT / "docs/roadmap/FIX-13-PREP-STATUS.zh-CN.md"
MIN_FREE = {"D": 40_000_000_000, "E": 80_000_000_000}


class IntakeBlocked(RuntimeError):
    """A saved, bounded stop that requires diagnosis or primary-session review."""


class SourceIncompatible(ValueError):
    """A baseline tree rejected by an existing source-format rule, exclusion clause 1."""


def ordered_primaries(plan: dict) -> list[str]:
    return list(dict.fromkeys(row["instance_id"] for row in plan["run_order"]))


def free_space() -> dict:
    return {drive: shutil.disk_usage(drive + ":/").free for drive in MIN_FREE}


def check_space(*, download_bytes=0, image_bytes=0) -> dict:
    free = free_space()
    # Planning reserves, not a claim about the exact expansion ratio or shared-layer disk use.
    needed = {"D": download_bytes + 1_000_000_000, "E": image_bytes * 6 + 2_000_000_000}
    if any(free[drive] - needed[drive] < MIN_FREE[drive] for drive in MIN_FREE):
        raise IntakeBlocked("capacity reserve would be crossed: " + json.dumps({"free": free, "estimated_needed": needed}))
    return free


def preload(instance_id: str, image: str) -> None:
    if prepare.image_present(image):
        return
    work = prepare.WORK / instance_id
    work.mkdir(parents=True, exist_ok=True)
    name, tag = image.rsplit(":", 1)
    repo = image_fetch.Repo(name)
    body, kind, digest = repo.manifest(tag)
    if "sha256:" + hashlib.sha256(body).hexdigest() != digest:
        raise prepare.GraderInfraError("official manifest digest mismatch")
    manifests = [json.loads(body)]
    if kind in image_fetch.INDEX_TYPES:
        manifests = []
        for child in json.loads(body)["manifests"]:
            raw, _, _ = repo.manifest(child["digest"])
            if "sha256:" + hashlib.sha256(raw).hexdigest() != child["digest"]:
                raise prepare.GraderInfraError("official child manifest digest mismatch")
            manifests.append(json.loads(raw))
    blobs = {entry["digest"]: entry["size"] for manifest in manifests
             for entry in [manifest["config"], *manifest["layers"]]}
    shared = sum(size for key, size in blobs.items()
                 if any(path.stat().st_size == size for path in CACHE.glob("*/blobs/sha256/" + key[7:])))
    total = sum(blobs.values())
    free = check_space(download_bytes=total - shared, image_bytes=total)
    metadata = {"image": image, "official_digest": digest, "compressed_bytes": total,
                "shared_candidate_bytes": shared, "new_blob_bytes": total - shared,
                "largest_blob_bytes": max(blobs.values()), "free_bytes": free,
                "expansion_planning_factor": 6, "note": "Shared candidates are rehashed by the downloader."}
    prepare._save(work / "image-plan.json", metadata)
    event(task=instance_id, stage="image_fetch", status="starting", **metadata)
    started = time.monotonic()
    with (work / "image-fetch.log").open("a", encoding="utf-8") as stream, contextlib.redirect_stdout(stream):
        image_fetch.fetch_image(image)
    if prepare.official_digest(image)["digest"] != f"{name}@{digest}":
        raise prepare.GraderInfraError("imported identity changed from the capacity preflight manifest")
    event(task=instance_id, stage="image_fetch", status="done", seconds=time.monotonic() - started)


def extend_intake(plan: dict, record: dict, through: int, ingest, save) -> dict:
    """Persist each candidate before execution; keep accepted slots and rejected identities on resume."""
    rows = {row["instance_id"]: row for tasks in plan["tasks"].values() for row in tasks}
    originals = ordered_primaries(plan)
    if not 1 <= through <= len(originals):
        raise ValueError("requested position count outside the frozen plan")
    record["authorized_positions"] = originals[:max(through, len(record.get("authorized_positions", [])))]
    record.setdefault("infra_failures", {})
    slots = {slot["original"]: slot for slot in record["slots"]}
    for original in originals[:through]:
        primary = rows[original]
        if original not in slots:
            slot = {"stratum": primary["stratum"], "rank": primary["rank"], "original": original,
                    "final": None, "attempts": []}
            record["slots"].append(slot)
            slots[original] = slot
        slot = slots[original]
        while not slot["final"]:
            attempts = slot["attempts"]
            rejected = [a for a in attempts if a.get("failed_clauses") and not a.get("pending")]
            if len(rejected) >= 3:
                raise IntakeBlocked(f"{original}: three candidates rejected; no fourth candidate is authorized")
            if not attempts:
                current = original
            elif attempts[-1].get("pending"):
                current = attempts[-1]["instance_id"]
            else:
                tried = {a["instance_id"] for item in record["slots"] for a in item["attempts"]}
                taken = {rows[i]["repo"] for i in originals}
                taken.update(rows[item["final"]]["repo"] for item in record["slots"] if item["final"])
                queue = [r for r in plan["tasks"][primary["stratum"]] if r["role"] == "replacement"]
                replacement = pilot_ingest.choose_replacement(queue, tried, taken)
                if replacement is None:
                    raise IntakeBlocked(f"{original}: fixed replacement queue exhausted")
                current = replacement["instance_id"]
                previous = attempts[-1]
                record["substitutions"].append({"stratum": primary["stratum"], "rank": primary["rank"],
                    "original": original, "replaced": previous["instance_id"],
                    "failed_clauses": previous["failed_clauses"], "error": previous.get("error"),
                    "replacement": current})
            if not attempts or attempts[-1]["instance_id"] != current:
                attempts.append({"instance_id": current, "accepted": False, "failed_clauses": [],
                                 "pending": True, "error": "not_started"})
            save(record)
            if record["infra_failures"].get(current, 0) >= 2:
                raise IntakeBlocked(f"{current}: bounded infrastructure attempts exhausted; preserve pending")
            try:
                state = ingest(current, 3)
            except IntakeBlocked:
                raise
            except SourceIncompatible as exc:
                result = {"instance_id": current, "accepted": False, "failed_clauses": [1],
                          "error": str(exc)}
            except Exception as exc:
                result = {"instance_id": current, "accepted": False, "failed_clauses": [], "pending": True,
                          "error": type(exc).__name__ + ": " + str(exc)[-1500:]}
            else:
                result = pilot_ingest.attempt(current, 3, lambda instance_id, repeats: state)
            previous = attempts[-1]
            if previous.get("error") != "not_started":
                result["history"] = [*previous.get("history", []),
                                     {key: value for key, value in previous.items() if key != "history"}]
            attempts[-1] = result
            if result.get("pending"):
                record["infra_failures"][current] = record["infra_failures"].get(current, 0) + 1
            if result["accepted"]:
                slot["final"] = current
            save(record)
            if result.get("pending"):
                if record["infra_failures"][current] >= 2:
                    raise IntakeBlocked(f"{current}: infrastructure failure repeated twice: {result.get('error')}")
                time.sleep(15)
    return record


def pdm_image_stage(original_stage, row: dict, work: Path):
    """Apply the reviewed P1 repair only to the verified generated directory."""
    stage = original_stage(row, work)
    if row["instance_id"] != PDM_TASK:
        return stage

    def run(state):
        repair = json.loads((work / "pdm-repair-r1.json").read_text(encoding="utf-8"))
        if repair["task_id"] != PDM_TASK or repair["revision"] != PDM_REVISION:
            raise prepare.GraderInfraError("P1 repair record does not match the reviewed target")
        if set(repair["files"]) != {".gitignore", "venvstacks.pth"}:
            raise prepare.GraderInfraError("P1 generated directory contents changed")
        archive = work / "source.tar.gz"
        if hashlib.sha256(archive.read_bytes()).hexdigest() != repair["source_archive_sha256"]:
            raise prepare.GraderInfraError("P1 baseline archive changed")
        with tarfile.open(archive) as source:
            paths = [member.name.split("/", 1)[-1] for member in source]
        if any(path == ".pdm-build" or path.startswith(".pdm-build/") for path in paths):
            raise prepare.GraderInfraError("P1 target occurs in baseline source")
        original_dockerfile = prepare.dockerfile

        def guarded_dockerfile(base):
            if base != repair["official_digest"]:
                raise prepare.GraderInfraError("P1 official image identity changed")
            guard = f"""RUN /usr/local/bin/python -B -I -S - <<'FIX13_PDM'
import hashlib, json, pathlib, stat
original = json.loads(pathlib.Path('/tmp/original-files.json').read_text())
assert not any(p == '.pdm-build' or p.startswith('.pdm-build/') for p in original)
directory = pathlib.Path('/opt/repo/.pdm-build')
before = directory.lstat()
assert stat.S_ISDIR(before.st_mode) and stat.S_IMODE(before.st_mode) == 0o700
assert (before.st_uid, before.st_gid) == (0, 0)
expected = {repair['files']!r}
assert sorted(p.name for p in directory.iterdir()) == sorted(expected)
def check_files():
    for name, metadata in expected.items():
        path = directory / name
        info = path.lstat()
        assert stat.S_ISREG(info.st_mode)
        assert (stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid) == (metadata['mode'], metadata['uid'], metadata['gid'])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == metadata['sha256']
check_files()
directory.chmod(stat.S_IMODE(before.st_mode) | 0o555)
after = directory.lstat()
assert stat.S_IMODE(after.st_mode) == 0o755 and (after.st_uid, after.st_gid) == (0, 0)
check_files()
FIX13_PDM
"""
            text = original_dockerfile(base)
            marker = "RUN /usr/local/bin/python - <<'PY'\n"
            if text.count(marker) != 1:
                raise prepare.GraderInfraError("P1 clean-image recipe insertion point changed")
            return text.replace(marker, guard + marker, 1)

        prepare.dockerfile = guarded_dockerfile
        try:
            result = stage(state)
        finally:
            prepare.dockerfile = original_dockerfile
        result["fix13_revision"] = PDM_REVISION
        event(task=PDM_TASK, stage="pdm_repair", status="done", revision=PDM_REVISION,
              old_digest=repair["old_clean_digest"], new_digest=result["image"]["clean_digest"],
              build_input_sha256=result["image"]["build_input_sha256"])
        return result
    return run


def event(**fields) -> None:
    row = {"at": datetime.now(timezone.utc).isoformat(), **fields}
    with EVENTS.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(row, ensure_ascii=False), flush=True)


def download_source(task: dict, destination: Path) -> None:
    """Use the direct codeload route verified for FIX-13, retaining the raw download."""
    if destination.is_file() and destination.stat().st_size:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw = destination.parent / "source-download.tar.gz"
    url = f"https://codeload.github.com/{task['repo']}/tar.gz/{task['base_commit']}"
    started = time.monotonic()
    command = ["curl.exe", "--noproxy", "*", "--fail", "--location", "--connect-timeout", "15",
               "--max-time", "120", "--speed-limit", "1048576", "--speed-time", "30",
               "--retry", "1", "--output", str(raw), url]
    log = destination.parent / "source-download.log"
    with log.open("a", encoding="utf-8") as stream:
        result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=270)
    elapsed = time.monotonic() - started
    event(task=destination.parent.name, stage="download", seconds=elapsed, exit_code=result.returncode,
          bytes=raw.stat().st_size if raw.exists() else 0, log=prepare.rel(log))
    if result.returncode:
        raise prepare.GraderInfraError(f"bounded source download failed, see {prepare.rel(log)}")
    stamp_source_archive(raw, destination, task)


def archive_members(path: Path) -> list:
    """Fingerprint relative members independently of the transport archive's outer folder."""
    result = []
    with tarfile.open(path) as archive:
        for member in archive:
            relative = member.name.partition("/")[2]
            digest = hashlib.sha256(archive.extractfile(member).read()).hexdigest() if member.isfile() else None
            result.append((relative, member.type.decode(), member.mode, member.uid, member.gid, member.linkname, digest))
    return result


def stamp_source_archive(raw: Path, destination: Path, task: dict) -> None:
    expected = task["repo"].split("/")[1] + "-" + task["base_commit"]
    with tarfile.open(raw) as incoming:
        members = incoming.getmembers()
        roots = {member.name.partition("/")[0] for member in members}
        if len(roots) != 1:
            raise prepare.GraderInfraError("source archive must have one top-level folder")
        observed = roots.pop()
        alias = observed != expected
        if alias and (incoming.pax_headers.get("comment") != task["base_commit"]
                      or not observed.endswith("-" + task["base_commit"])):
            raise prepare.GraderInfraError("archive alias lacks matching original commit proof")
        stamped = destination.with_suffix(".stamped.tar.gz")
        with tarfile.open(stamped, "w:gz", format=tarfile.PAX_FORMAT,
                          pax_headers={"comment": task["base_commit"]}) as outgoing:
            for member in members:
                updated = copy.copy(member)
                if alias:
                    updated.pax_headers = dict(member.pax_headers)
                    updated.name = expected + member.name[len(observed):]
                    if "path" in updated.pax_headers:
                        updated.pax_headers["path"] = updated.name
                outgoing.addfile(updated, incoming.extractfile(member) if member.isfile() else None)
    if alias:
        if archive_members(raw) != archive_members(stamped):
            raise prepare.GraderInfraError("archive alias normalization changed a relative source member")
        prepare._save(destination.parent / "source-root-alias.json",
                      {"old_root": observed, "new_root": expected, "original_commit": task["base_commit"],
                       "relative_members_identical": True, "raw_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
                       "normalized_sha256": hashlib.sha256(stamped.read_bytes()).hexdigest()})
    stamped.replace(destination)


def configure() -> None:
    prepare.CONTEXT = "desktop-linux"
    prepare.WORK = DEST / "work"
    prepare.PUBLIC = DEST / "public"
    prepare.PRIVATE = DEST / "private"
    prepare.CLEAN_RECORDS = DEST / "clean-images"
    import scripts.sprint5_prepare_main as source
    source.download_archive = download_source
    original_docker = prepare.docker

    def local_docker(*arguments, **options):
        if arguments and arguments[0] == "pull":
            raise prepare.GraderInfraError("automatic image pull is disabled for FIX-13 preparation")
        return original_docker(*arguments, **options)

    prepare.docker = local_docker
    original_digest = prepare.official_digest

    def local_digest(image):
        if not prepare.image_present(image):
            raise prepare.GraderInfraError("official image not preloaded locally; automatic pull is disabled")
        return original_digest(image)

    prepare.official_digest = local_digest
    original_image_stage = prepare.image_stage
    prepare.image_stage = lambda row, work: pdm_image_stage(original_image_stage, row, work)
    original_stages = prepare.run_stages

    def timed_stages(path, stages, redo=()):
        wrapped = []
        for name, function in stages:
            def timed(state, stage=name, run=function):
                started = time.monotonic()
                event(task=path.parent.name, stage=stage, status="starting")
                try:
                    value = run(state)
                except Exception as exc:
                    event(task=path.parent.name, stage=stage, status="error",
                          seconds=time.monotonic() - started, error=type(exc).__name__ + ": " + str(exc)[-1000:])
                    raise
                event(task=path.parent.name, stage=stage, status="done", seconds=time.monotonic() - started)
                return value
            wrapped.append((name, timed))
        return original_stages(path, wrapped, redo=redo)

    prepare.run_stages = timed_stages


def ingest_task(instance_id: str, repeats: int, rows: dict, fetch_images: bool) -> dict:
    image = rows[instance_id]["docker_image"]
    if fetch_images:
        preload(instance_id, image)
    elif not prepare.image_present(image):
        raise prepare.GraderInfraError(f"preload required for {instance_id}, {image}")
    work = prepare.WORK / instance_id
    cached_path = work / "state.json"
    cached = json.loads(cached_path.read_text(encoding="utf-8")) if cached_path.exists() else {}
    redo = ()
    if instance_id == PDM_TASK and cached.get("fix13_revision") != PDM_REVISION:
        redo = ("image",)
    try:
        state = prepare.prepare_task(instance_id, repeats, redo=redo, keep_original_files=True, manifest=MANIFEST)
    except ValueError as exc:
        if str(exc) == "symlink does not name an internal regular file or directory":
            raise SourceIncompatible(str(exc)) from exc
        raise
    if state["accepted"]:
        from scripts.run_swe_task import grader_spec, rebench_calibration, resolve_image
        tasks = prepare.yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))["tasks"]
        task = next(t for t in tasks if t["id"] == instance_id)
        receipt = json.loads((ROOT / task["receipt"]).read_text(encoding="utf-8"))
        image, clean = resolve_image(task, receipt, legacy_image=False, mock=True)
        calibration = rebench_calibration(task)
        grader_spec(task)
        if calibration is None or image != state["image"]["clean_digest"] or not clean["answer_isolation_pass"]:
            raise prepare.GraderInfraError("FIX-13 output binding failed its runtime entry-point check")
        event(task=instance_id, stage="runtime_bindings", status="done", receipt=task["receipt"],
              clean_record=task["clean_record"], calibrated_missing=calibration["empty_missing"])
    return state


def save_intake(record: dict) -> None:
    record.update(pilot_ingest.summarize_slots(record))
    record["updated_at"] = datetime.now(timezone.utc).isoformat()
    for retry in range(6):
        try:
            prepare._save(RECORD, record)
            return
        except PermissionError:
            # Windows readers can briefly deny delete-sharing during atomic replacement.
            if retry == 5:
                raise
            time.sleep(min(0.2 * 2**retry, 2))


def progress(record: dict, through: int, status: str, reason: str = "") -> None:
    free = free_space()
    event(status=status, through=through, accepted=record["accepted"], free_bytes=free, reason=reason)
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    line = (f"\n- {stamp}，授权至位置 {through}，累计通过 {record['accepted']}/30。"
            f"D/E 可用 {free['D']/1e9:.1f}/{free['E']/1e9:.1f} GB。状态 {status}。{reason}\n")
    with PROGRESS.open("a", encoding="utf-8") as stream:
        stream.write(line)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--through", type=int, default=5, help="authorize the frozen prefix through this position")
    parser.add_argument("--continue-all", action="store_true", help="continue in five-position batches through 30")
    parser.add_argument("--fetch-images", action="store_true", help="verified proxy/cache preload, serial with intake")
    args = parser.parse_args()
    DEST.mkdir(parents=True, exist_ok=True)
    plan_bytes = OUT.read_bytes()
    plan = json.loads(plan_bytes)
    if hashlib.sha256(EXPOSURE.read_bytes()).hexdigest() != plan["exposure_sha256"]:
        raise SystemExit("frozen exposure snapshot changed")
    plan_hash = hashlib.sha256(plan_bytes).hexdigest()
    record = json.loads(RECORD.read_text(encoding="utf-8"))
    if record["plan_sha256"] != plan_hash:
        raise SystemExit("frozen intake plan changed")
    lock = DEST / "prepare.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise SystemExit("prepare.lock exists; inspect the recorded process before recovering")
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump({"pid": os.getpid(), "at": datetime.now(timezone.utc).isoformat()}, stream)
    configure()
    image_fetch.ROOT = CACHE
    image_fetch.PROXY_BLOBS = True
    image_fetch.WORKERS = 2
    rows = {r["instance_id"]: r for tasks in plan["tasks"].values() for r in tasks}
    if args.continue_all:
        first = max(10, ((record["accepted"] // 5) + 1) * 5)
        limits = list(range(min(first, 30), 31, 5))
    else:
        limits = [args.through]
    try:
        event(status="driver_start", pid=os.getpid(), limits=limits, model_calls=0)
        for through in limits:
            try:
                free = check_space()
                event(status="batch_start", through=through, accepted=record["accepted"], free_bytes=free)
                record.pop("halt_reason", None)
                extend_intake(plan, record, through,
                              lambda task, repeats: ingest_task(task, repeats, rows, args.fetch_images), save_intake)
                save_intake(record)
            except Exception as exc:
                record["halt_reason"] = type(exc).__name__ + ": " + str(exc)
                save_intake(record)
                progress(record, through, "blocked", record["halt_reason"])
                raise SystemExit(2) from exc
            progress(record, through, "batch_done")
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
