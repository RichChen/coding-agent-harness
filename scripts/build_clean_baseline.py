"""Build a clean baseline image from the original archive. No test or gold patch."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.preflight_a5_repo_baseline import image_build  # noqa: E402
from scripts.swebench_specs import fetch_requirements, install_commands, spec_row  # noqa: E402
from src.eval.a5_image_tree import archive_manifest  # noqa: E402

OUT = ROOT / "docs" / "sprint" / "clean-images"
WORK = ROOT / "runs" / "sprint4-clean"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_tasks(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))["tasks"]


def added_lines(patch: str, original_text: str):
    lines = []
    for line in patch.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        text = line[1:].strip()
        if len(text) >= 24 and text not in original_text:
            lines.append(text)
    return lines[:6]


def build_one(task: dict) -> dict:
    receipt = json.loads((ROOT / task["receipt"]).read_text(encoding="utf-8"))
    source_receipt = Path(receipt["source_attempt_ref"])
    archive = source_receipt.parent / "source.tar.gz"
    private = json.loads(Path(receipt["private_source_binding"]["path"]).read_text(encoding="utf-8"))
    family = task["family"]
    version = str(private.get("version") or "")
    manifest = archive_manifest(archive, task["repo"], receipt["base_revision"])
    work = WORK / task["id"]
    if work.exists():
        shutil.rmtree(work)
    context = work / "context"
    context.mkdir(parents=True)
    shutil.copy2(archive, context / "source.tar.gz")
    row = spec_row(task["repo"], version)
    install = " && ".join(install_commands(row))
    if row.get("packages") == "requirements.txt":
        (context / "swebench-requirements.txt").write_text(
            fetch_requirements(task["repo"], receipt["base_revision"], version),
            encoding="utf-8",
        )
    native = ""
    if family == "scikit-learn":
        native = ("RUN sed -i 's|http://deb.debian.org/debian-security|https://snapshot.debian.org/archive/debian-security/20240901T000000Z|g;"
                  "s|http://deb.debian.org/debian|https://snapshot.debian.org/archive/debian/20240901T000000Z|g' /etc/apt/sources.list && "
                  "apt-get -o Acquire::Check-Valid-Until=false update && apt-get install -y --no-install-recommends build-essential && "
                  "dpkg-query -W > /opt/grader-system-packages.txt\n")
    files = sorted(path for path, meta in manifest["entries"].items() if meta.get("kind") == "file")
    (context / "original-files.json").write_text(json.dumps(files), encoding="utf-8")
    requirements_copy = ""
    if (context / "swebench-requirements.txt").is_file():
        requirements_copy = "COPY swebench-requirements.txt /tmp/swebench-requirements.txt\n"
    dockerfile = (
        f"FROM {receipt['transport_image_digest']}\n" + native +
        "COPY original-files.json /tmp/original-files.json\n"
        "COPY source.tar.gz /tmp/source.tar.gz\n" + requirements_copy +
        "RUN /usr/local/bin/python - <<'PY'\n"
        "import pathlib, tarfile\n"
        "import shutil\n"
        "root = pathlib.Path('/workspace')\n"
        "if root.exists():\n"
        "    shutil.rmtree(root)\n"
        "root.mkdir(parents=True)\n"
        "with tarfile.open('/tmp/source.tar.gz') as archive:\n"
        "    for member in archive.getmembers():\n"
        "        name = member.name.split('/', 1)[1] if '/' in member.name else ''\n"
        "        if not name:\n"
        "            continue\n"
        "        member.name = name\n"
        "        archive.extract(member, root)\n"
        "pathlib.Path('/tmp/source.tar.gz').unlink()\n"
        "PY\n"
        "WORKDIR /workspace\n"
        f"RUN {install}\n"
        "RUN /usr/local/bin/python - <<'PY'\n"
        "import json, pathlib\n"
        "root = pathlib.Path('/workspace')\n"
        "original = set(json.loads(pathlib.Path('/tmp/original-files.json').read_text()))\n"
        "found = []\n"
        "for path in root.rglob('*.so'):\n"
        "    if path.is_file() and not path.is_symlink():\n"
        "        rel = path.relative_to(root).as_posix()\n"
        "        if rel not in original:\n"
        "            found.append(rel)\n"
        "pathlib.Path('/opt/native-artifacts.json').write_text(json.dumps(sorted(found)))\n"
        "PY\n"
        "RUN cp -a /workspace/. /opt/repo/ && rm -rf /workspace /tmp/original-files.json && mkdir /workspace\n"
        "RUN /usr/local/bin/python -m pip freeze --all > /opt/grader-environment.txt\n"
        "ENV PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=/workspace:/workspace/src\n"
    )
    (context / "Dockerfile").write_bytes(dockerfile.encode("utf-8"))
    tag = "coding-harness-a5-clean:" + task["id"].replace("__", "-")
    existing = OUT / (task["id"] + ".json")
    if existing.exists() and "--rebuild" not in sys.argv and json.loads(existing.read_text(encoding="utf-8")).get("answer_isolation_pass"):
        print(task["id"], "already isolated")
        return json.loads(existing.read_text(encoding="utf-8"))
    if existing.exists() and "--rebuild" not in sys.argv:
        previous = json.loads(existing.read_text(encoding="utf-8"))
        digest = previous["clean_image_digest"]
        inspect = {"Id": previous.get("clean_image_id")}
    else:
        digest, inspect = image_build(context, tag, work / "build.log")
    source_ok, source_hash = source_only_check(digest, manifest)
    scan = filesystem_scan(digest, archive, manifest, private.get("test_patch") or "", private.get("patch") or "")
    row = {
        "task_id": task["id"],
        "clean_image_digest": digest,
        "clean_image_id": inspect.get("Id"),
        "original_tree_hash": manifest["source_tree_hash"],
        "image_source_only_hash": source_hash,
        "source_only_match": source_ok,
        "native_artifacts": native_list(digest),
        "build_input_sha256": sha((archive.read_bytes() + profile.encode() + dockerfile.encode())),
        "filesystem_scan": scan,
        "answer_isolation_pass": bool(source_ok and scan.get("pass")),
    }
    dest = OUT / (task["id"] + ".json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(row, indent=2) + "\n", encoding="utf-8")
    print(task["id"], "isolation", row["answer_isolation_pass"], "natives", len(row["native_artifacts"]))
    return row


def docker_python(digest: str, code: str, stdin: bytes = b"", python_bin: str = "/usr/local/bin/python",
                  timeout: float = 600) -> str:
    argv = ["docker", "--context", "desktop-linux", "run", "--rm", "-i", "--network", "none", digest,
            python_bin, "-I", "-S", "-c", code]
    result = subprocess.run(argv, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", "replace")[-1000:])
    return result.stdout.decode("utf-8", "replace")


def source_only_check(digest: str, manifest: dict, overrides: dict[str, str] | None = None):
    """Every archive file must be in /opt/repo. Files in overrides must have the override sha instead."""
    payload = {
        path: meta["sha256"]
        for path, meta in manifest["entries"].items()
        if meta.get("kind") == "file"
    }
    unknown = sorted(set(overrides or {}) - set(payload))
    if unknown:
        raise ValueError("override for a file not in the archive: " + ", ".join(unknown))
    payload.update(overrides or {})
    code = r'''
import hashlib, json, pathlib, sys
expected = json.load(sys.stdin)
root = pathlib.Path("/opt/repo")
bad = []
for path, digest in expected.items():
    target = root / path
    if not target.is_file() or target.is_symlink():
        bad.append(path)
        continue
    if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        bad.append(path)
print(json.dumps({"mismatch": len(bad), "sample": bad[:5]}))
'''
    raw = docker_python(digest, code, json.dumps(payload).encode())
    parsed = json.loads(raw)
    ok = parsed["mismatch"] == 0
    return ok, manifest["source_tree_hash"] if ok else "mismatch:" + str(parsed["mismatch"])


def native_list(digest: str):
    raw = docker_python(digest, "print(open('/opt/native-artifacts.json').read())")
    return json.loads(raw)


def archive_text(archive: Path) -> str:
    import tarfile
    chunks = []
    with tarfile.open(archive, "r:gz") as packed:
        for member in packed.getmembers():
            if not member.isfile():
                continue
            extracted = packed.extractfile(member)
            if extracted:
                chunks.append(extracted.read().decode("utf-8", "ignore"))
    return "\n".join(chunks)


def repo_packages(manifest: dict) -> set[str]:
    """Top-level package names of the target project: directories with __init__.py at the root or under src/."""
    names = set()
    for path in manifest.get("entries") or {}:
        parts = PurePosixPath(path).parts
        if len(parts) == 2 and parts[1] == "__init__.py":
            names.add(parts[0])
        elif len(parts) == 3 and parts[0] == "src" and parts[2] == "__init__.py":
            names.add(parts[1])
    return names


def exclusion_guarded(path: str, packages: set[str]) -> bool:
    """A hit in /opt/repo or inside a target-project package directory is never excused."""
    for piece in path.split("!"):
        if piece == "/opt/repo" or piece.startswith("/opt/repo/"):
            return True
        if any(part in packages for part in PurePosixPath(piece).parts[:-1]):
            return True
    return False


def exclude_base_image_matches(found: list[dict], base_hashes: dict[str, str | None], packages: set[str] = frozenset()) -> tuple[list[dict], list[str]]:
    """Drop a needle hit when the same path in the base image has the same bytes."""
    hits = []
    same = []
    for item in found:
        path = item["path"]
        if not exclusion_guarded(path, packages) and base_hashes.get(path) and base_hashes.get(path) == item["sha256"]:
            same.append(path)
        else:
            hits.append(item)
    return hits, sorted(set(same))


def exclude_recorded_installs(found: list[dict], recorded_hashes: set[str], packages: set[str] = frozenset()) -> tuple[list[str], list[str]]:
    """Drop a hit whose bytes are exactly a file pip recorded at install time."""
    hits = []
    same = []
    for item in found:
        if not exclusion_guarded(item["path"], packages) and item["sha256"] in recorded_hashes:
            same.append(item["path"])
        else:
            hits.append(item["path"])
    return sorted(set(hits)), sorted(set(same))


def _base_image_hashes(base_image: str, paths: list[str], python_bin: str) -> dict[str, str | None]:
    code = r'''
import hashlib, json, pathlib, sys, tarfile, zipfile
found = {}
for rel in json.load(sys.stdin):
    digest = None
    if "!" in rel:
        outer, inner = rel.split("!", 1)
        path = pathlib.Path(outer)
        try:
            if outer.endswith((".tar", ".tar.gz", ".tgz")) and path.is_file():
                with tarfile.open(path) as packed:
                    extracted = packed.extractfile(inner)
                    if extracted:
                        digest = hashlib.sha256(extracted.read()).hexdigest()
            elif outer.endswith(".zip") and path.is_file():
                with zipfile.ZipFile(path) as packed:
                    digest = hashlib.sha256(packed.read(inner)).hexdigest()
        except Exception:
            digest = None
    else:
        path = pathlib.Path(rel)
        if path.is_file() and not path.is_symlink():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
    found[rel] = digest
print(json.dumps(found))
'''
    raw = docker_python(base_image, code, json.dumps(paths).encode(), python_bin=python_bin)
    return json.loads(raw)


def _recorded_install_hashes(digest: str, python_bin: str) -> set[str]:
    code = r'''
import base64, json, pathlib
hashes = []
for record in pathlib.Path("/").rglob("RECORD"):
    if ".dist-info" not in record.as_posix():
        continue
    try:
        text = record.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    for line in text.splitlines():
        parts = line.split(",")
        if len(parts) < 2 or not parts[1].startswith("sha256="):
            continue
        token = parts[1][len("sha256="):]
        try:
            raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        except Exception:
            continue
        hashes.append(raw.hex())
print(json.dumps(hashes))
'''
    raw = docker_python(digest, code, b"[]", python_bin=python_bin)
    return set(json.loads(raw))


def filesystem_scan(digest: str, archive: Path, manifest: dict, test_patch: str, gold_patch: str, python_bin: str = "/usr/local/bin/python", base_image: str | None = None, timeout: float = 600):
    packages = repo_packages(manifest)
    blob = archive_text(archive)
    needles = added_lines(test_patch, blob) + added_lines(gold_patch, blob)
    if not needles:
        return {"needle_count": 0, "hits": ["no-unique-needle"], "pass": False, "base_image_same": []}
    code = r'''
import hashlib, json, pathlib, sys, tarfile, zipfile
needles = json.load(sys.stdin)
roots = [p for p in pathlib.Path("/").iterdir() if p.name not in {"proc", "sys", "dev"}]
hits = []
seen = set()
def consider(rel, data):
    if rel in seen:
        return
    text = data.decode("utf-8", "ignore")
    if any(needle in text for needle in needles):
        seen.add(rel)
        hits.append({"path": rel, "sha256": hashlib.sha256(data).hexdigest()})
for root in roots:
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.as_posix()
        try:
            data = path.read_bytes()
        except OSError:
            continue
        consider(rel, data)
        name = path.name
        try:
            if name.endswith((".tar", ".tar.gz", ".tgz")):
                with tarfile.open(path) as packed:
                    for member in packed.getmembers():
                        if not member.isfile():
                            continue
                        extracted = packed.extractfile(member)
                        if extracted:
                            consider(rel + "!" + member.name, extracted.read())
            elif name.endswith(".zip"):
                with zipfile.ZipFile(path) as packed:
                    for member in packed.namelist():
                        consider(rel + "!" + member, packed.read(member))
        except Exception:
            continue
print(json.dumps({"found": hits}))
'''
    raw = docker_python(digest, code, json.dumps(needles).encode(), python_bin=python_bin, timeout=timeout)
    found = json.loads(raw).get("found") or []
    same: list[str] = []
    install_same: list[str] = []
    if base_image and found:
        base_hashes = _base_image_hashes(base_image, [item["path"] for item in found], python_bin)
        remaining, same = exclude_base_image_matches(found, base_hashes, packages)
    else:
        remaining = list(found)
    if remaining:
        recorded = _recorded_install_hashes(digest, python_bin)
        hits, install_same = exclude_recorded_installs(remaining, recorded, packages)
    else:
        hits = []
    return {
        "needle_count": len(needles),
        "hits": hits[:20],
        "hit_count": len(hits),
        "base_image_same": same[:20],
        "base_image_same_count": len(same),
        "install_record_same": install_same[:20],
        "install_record_same_count": len(install_same),
        "pass": not hits,
    }


def main():
    wanted = sys.argv[1:]
    tasks = load_tasks(ROOT / "configs/sprint/dev-tasks.yaml")
    if wanted:
        tasks = [task for task in tasks if task["id"] in wanted]
    for task in tasks:
        build_one(task)


if __name__ == "__main__":
    main()
