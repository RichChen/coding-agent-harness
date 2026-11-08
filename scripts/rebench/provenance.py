"""Provenance of answer-isolation scan hits (pilot design section 3.3 clause 2, revision R7).

A needle hit is excused only when the file is shown to be an unmodified third-party file: its path and sha256
match the record of the installed distribution that owns it. Records consulted, all read inside the image:

- the RECORD of a ``*.dist-info`` directory next to the file's top-level directory (pip and wheel installs);
- ``conda-meta/*.json`` ``paths_data`` of the conda prefix that contains the file (conda packages, the
  interpreter and its standard library included);
- ``info/paths.json`` of an extracted package under a conda ``pkgs`` cache, for files of that cache;
- ``/var/lib/dpkg/info/*.md5sums`` (Debian packages; md5 is the only digest dpkg records);
- the same path in a known public base image whose layers are a prefix of the image's layers (the miniconda
  base environment records no digest for most of its files, and its packages are not in the pkgs cache).

Everything else stays a hit: /tmp, /root, caches, archive members, the repository and the target project's
own packages (``exclusion_guarded``), and any third-party file whose bytes differ from its record.

Git export-subst placeholders: a GitHub source archive has ``$Format:...$`` placeholders expanded, a checkout
does not. A source-only mismatch is excused only for a file the archive's .gitattributes marks export-subst,
whose image copy is the archive copy with each expanded value put back as its ``$Format:...$`` placeholder.
"""
from __future__ import annotations

import json
import re
import subprocess

RULE = "provenance-v2"

BASE_IMAGES = {"continuumio/miniconda3:24.7.1-0": "/opt/conda/bin/python"}

PROVENANCE_CODE = r'''
import base64, csv, hashlib, json, os, posixpath, sys
job = json.load(sys.stdin)
root = job.get("root") or "/"

def real(path):
    return os.path.join(root, *[p for p in path.split("/") if p])

def ancestors(path):
    parts = [p for p in path.split("/") if p][:-1]
    for i in range(len(parts), 0, -1):
        yield "/" + "/".join(parts[:i])

def digest(path, name):
    h = hashlib.new(name)
    with open(real(path), "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def listdir(path):
    try:
        return sorted(os.listdir(real(path)))
    except OSError:
        return []

def load_json(path):
    try:
        with open(real(path), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None

cache = {}

def records(directory):
    key = ("record", directory)
    if key not in cache:
        found = {}
        for name in listdir(directory):
            if not name.endswith(".dist-info"):
                continue
            try:
                with open(real(directory + "/" + name + "/RECORD"), encoding="utf-8", errors="replace") as f:
                    rows = list(csv.reader(f.read().splitlines()))
            except OSError:
                continue
            for row in rows:
                if len(row) < 2 or not row[1].startswith("sha256="):
                    continue
                token = row[1][len("sha256="):]
                try:
                    value = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).hex()
                except Exception:
                    continue
                target = posixpath.normpath(posixpath.join(directory, row[0]))
                found[target] = (name[:-len(".dist-info")], value)
        cache[key] = found
    return cache[key]

def conda_meta(prefix):
    key = ("conda", prefix)
    if key not in cache:
        found = {}
        for name in listdir(prefix + "/conda-meta"):
            if not name.endswith(".json"):
                continue
            data = load_json(prefix + "/conda-meta/" + name) or {}
            for item in (data.get("paths_data") or {}).get("paths") or []:
                if item.get("_path") and item.get("sha256"):
                    found[posixpath.normpath(posixpath.join(prefix, item["_path"]))] = (name[:-len(".json")], item["sha256"])
        cache[key] = found
    return cache[key]

def package_cache(directory):
    key = ("pkgs", directory)
    if key not in cache:
        found = {}
        if posixpath.basename(posixpath.dirname(directory)) == "pkgs":
            data = load_json(directory + "/info/paths.json") or {}
            for item in data.get("paths") or []:
                if item.get("_path") and item.get("sha256"):
                    found[posixpath.normpath(posixpath.join(directory, item["_path"]))] = (posixpath.basename(directory), item["sha256"])
        cache[key] = found
    return cache[key]

def dpkg():
    if "dpkg" not in cache:
        found = {}
        for name in listdir("/var/lib/dpkg/info"):
            if not name.endswith(".md5sums"):
                continue
            try:
                with open(real("/var/lib/dpkg/info/" + name), encoding="utf-8", errors="replace") as f:
                    lines = f.read().splitlines()
            except OSError:
                continue
            for line in lines:
                parts = line.split(None, 1)
                if len(parts) == 2:
                    found["/" + parts[1].strip().lstrip("/")] = (name[:-len(".md5sums")], parts[0].lower())
        cache["dpkg"] = found
    return cache["dpkg"]

results = {}
for hit in job["hits"]:
    path, sha = hit["path"], hit["sha256"]
    results[path] = None
    if "!" in path or os.path.islink(real(path)) or not os.path.isfile(real(path)) or digest(path, "sha256") != sha:
        continue
    for directory in ancestors(path):
        for source, table in (("dist-info RECORD", records(directory)), ("conda-meta", conda_meta(directory)),
                              ("conda pkgs paths.json", package_cache(directory))):
            entry = table.get(path)
            if entry and entry[1] == sha:
                results[path] = {"source": source, "package": entry[0]}
                break
        if results[path]:
            break
    if results[path] is None:
        entry = dpkg().get(path)
        if entry and entry[1] == digest(path, "md5"):
            results[path] = {"source": "dpkg md5sums", "package": entry[0]}
print(json.dumps(results))
'''


DIGEST_CODE = r'''
import hashlib, json, os, sys
job = json.load(sys.stdin)
root = job.get("root") or "/"
out = {}
for path in job["paths"]:
    real = os.path.join(root, *[p for p in path.split("/") if p])
    out[path] = None
    if "!" in path or os.path.islink(real) or not os.path.isfile(real):
        continue
    h = hashlib.sha256()
    with open(real, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    out[path] = h.hexdigest()
print(json.dumps(out))
'''


def inspect_image(ref: str) -> dict | None:
    proc = subprocess.run(["docker", "--context", "desktop-linux", "image", "inspect", ref],
                          capture_output=True, text=True, timeout=120)
    if proc.returncode:
        return None
    info = json.loads(proc.stdout)[0]
    return {"id": info["Id"], "layers": info["RootFS"].get("Layers") or [], "repo_digests": info.get("RepoDigests") or []}


def base_image(image: str, candidates: dict = BASE_IMAGES, inspect=inspect_image) -> dict | None:
    """The first local candidate base image whose layers are a prefix of the image's layers."""
    target = inspect(image)
    for ref, python_bin in candidates.items():
        base = inspect(ref)
        if target and base and base["layers"] and target["layers"][:len(base["layers"])] == base["layers"]:
            return {"ref": ref, "id": base["id"], "python_bin": python_bin,
                    "digest": (base["repo_digests"] or [base["id"]])[0]}
    return None


def base_matches(found: list[dict], origin: dict, base_digests: dict, base: dict) -> dict:
    """origin plus a base-image record for every unexcused hit whose bytes equal the same path in the base image."""
    merged = dict(origin)
    for item in found:
        if merged.get(item["path"]) is None and base_digests.get(item["path"]) == item["sha256"]:
            merged[item["path"]] = {"source": "base image", "package": base["digest"]}
    return merged


def provenance(image: str, found: list[dict], timeout: float = 1800, candidates: dict = BASE_IMAGES) -> dict:
    """path -> {source, package} for every hit shown to be an unmodified third-party file, else None."""
    from scripts.build_clean_baseline import docker_python

    if not found:
        return {}
    payload = {"root": "/", "hits": [{"path": item["path"], "sha256": item["sha256"]} for item in found]}
    origin = json.loads(docker_python(image, PROVENANCE_CODE, json.dumps(payload).encode(), timeout=timeout))
    left = sorted({item["path"] for item in found if origin.get(item["path"]) is None and "!" not in item["path"]})
    base = base_image(image, candidates) if left else None
    if base:
        digests = json.loads(docker_python(base["id"], DIGEST_CODE, json.dumps({"root": "/", "paths": left}).encode(),
                                           python_bin=base["python_bin"], timeout=timeout))
        origin = base_matches(found, origin, digests, base)
    return origin


def classify_hits(found: list[dict], origin: dict, packages: set[str]) -> tuple[list[str], list[dict]]:
    """(failing hit paths, excused hits with their record). Repository and target-package paths never excused."""
    from scripts.build_clean_baseline import exclusion_guarded

    failing, excused = [], []
    for item in found:
        record = origin.get(item["path"])
        if record and not exclusion_guarded(item["path"], packages):
            excused.append({"path": item["path"], **record})
        else:
            failing.append(item["path"])
    return sorted(set(failing)), sorted(excused, key=lambda e: e["path"])


SOURCE_CODE = r'''
import hashlib, json, pathlib, sys
expected = json.load(sys.stdin)
root = pathlib.Path("/opt/repo")
bad = []
for path, digest in sorted(expected.items()):
    target = root / path
    if not target.is_file() or target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        bad.append(path)
print(json.dumps(bad))
'''

READ_CODE = r'''
import json, pathlib, sys
out = {}
for path in json.load(sys.stdin):
    target = pathlib.Path("/opt/repo") / path
    out[path] = target.read_text(encoding="utf-8") if target.is_file() and not target.is_symlink() else None
print(json.dumps(out))
'''


def source_check(image: str, archive, manifest: dict) -> dict:
    """Every archive file must be in /opt/repo with the same bytes, except confirmed export-subst placeholders."""
    import tarfile

    from scripts.build_clean_baseline import docker_python

    expected = {path: meta["sha256"] for path, meta in manifest["entries"].items() if meta.get("kind") == "file"}
    mismatched = json.loads(docker_python(image, SOURCE_CODE, json.dumps(expected).encode()))
    excused = []
    if mismatched and ".gitattributes" in expected:
        prefix = manifest["repo"].split("/")[1] + "-" + manifest["base_revision"] + "/"
        with tarfile.open(str(archive), "r:gz") as packed:
            def text(path):
                return packed.extractfile(prefix + path).read().decode("utf-8", "replace")
            candidates = sorted(set(mismatched) & export_subst_paths(text(".gitattributes")))
            copies = json.loads(docker_python(image, READ_CODE, json.dumps(candidates).encode())) if candidates else {}
            excused = [p for p in candidates if copies.get(p) is not None and is_expanded_placeholder(copies[p], text(p))]
    remaining = sorted(set(mismatched) - set(excused))
    return {"match": not remaining, "mismatch": remaining[:20], "mismatch_count": len(remaining),
            "export_subst_excused": excused}


PLACEHOLDER = re.compile(r"\$Format:[^$\n]*\$")


def export_subst_paths(gitattributes: str) -> set[str]:
    """Paths a root .gitattributes marks export-subst (literal patterns only; globs are not expanded)."""
    paths = set()
    for line in gitattributes.splitlines():
        parts = line.split()
        if len(parts) >= 2 and "export-subst" in parts[1:] and not any(c in parts[0] for c in "*?["):
            paths.add(parts[0].lstrip("/"))
    return paths


def is_expanded_placeholder(image_text: str, archive_text: str) -> bool:
    """The archive copy equals the image copy with every $Format:...$ placeholder expanded to some value."""
    if not PLACEHOLDER.search(image_text) or PLACEHOLDER.search(archive_text):
        return False
    pattern = r"[^\n$]*".join(re.escape(piece) for piece in PLACEHOLDER.split(image_text))
    return re.fullmatch(pattern, archive_text) is not None
