"""Revision R7: a scan hit is excused only for an unmodified third-party file shown by its install record."""
import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.rebench.provenance import (DIGEST_CODE, PROVENANCE_CODE, base_image, base_matches, classify_hits,
                                        export_subst_paths, is_expanded_placeholder)

NEEDLE = "assert parse_config(path) == expected_config_value"
ENV = "/opt/conda/envs/testbed"
SITE = ENV + "/lib/python3.12/site-packages"


def put(root: Path, path: str, data: bytes) -> dict:
    target = root / path.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {"path": path, "sha256": hashlib.sha256(data).hexdigest()}


def record_hash(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def run(root: Path, hits: list[dict]) -> dict:
    proc = subprocess.run([sys.executable, "-I", "-S", "-c", PROVENANCE_CODE],
                          input=json.dumps({"root": str(root), "hits": hits}).encode(),
                          stdout=subprocess.PIPE, check=True)
    return json.loads(proc.stdout)


@pytest.fixture
def image(tmp_path):
    text = ("# third-party code\n" + NEEDLE + "\n").encode()
    hits = {}
    hits["tmp"] = put(tmp_path, "/tmp/test_answer.py", text)
    hits["setuptools"] = put(tmp_path, SITE + "/setuptools/discovery.py", text)
    put(tmp_path, SITE + "/setuptools-80.9.0.dist-info/RECORD",
        ("setuptools/discovery.py," + record_hash(text) + ",%d\n" % len(text)).encode())
    hits["stdlib"] = put(tmp_path, ENV + "/lib/python3.12/inspect.py", text)
    put(tmp_path, ENV + "/conda-meta/python-3.12.12-h0_1.json", json.dumps({"paths_data": {"paths": [
        {"_path": "lib/python3.12/inspect.py", "sha256": hits["stdlib"]["sha256"]}]}}).encode())
    hits["pkgs"] = put(tmp_path, "/opt/conda/pkgs/setuptools-80.9.0-py312_0/lib/python3.12/site-packages/x.py", text)
    put(tmp_path, "/opt/conda/pkgs/setuptools-80.9.0-py312_0/info/paths.json", json.dumps({"paths": [
        {"_path": "lib/python3.12/site-packages/x.py", "sha256": hits["pkgs"]["sha256"]}]}).encode())
    hits["dpkg"] = put(tmp_path, "/usr/lib/python3.11/inspect.py", text)
    put(tmp_path, "/var/lib/dpkg/info/libpython3.11-stdlib.md5sums",
        (hashlib.md5(text).hexdigest() + "  usr/lib/python3.11/inspect.py\n").encode())
    hits["modified"] = put(tmp_path, SITE + "/numpy/core.py", text)
    original = b"# numpy original\n"
    put(tmp_path, SITE + "/numpy-2.0.dist-info/RECORD", ("numpy/core.py," + record_hash(original) + ",17\n").encode())
    hits["own"] = put(tmp_path, SITE + "/mypkg/config.py", text)
    put(tmp_path, SITE + "/mypkg-9.9.dist-info/RECORD", ("mypkg/config.py," + record_hash(text) + ",1\n").encode())
    hits["cache"] = put(tmp_path, "/root/.cache/pip/wheels/setuptools/discovery.py", text)
    return tmp_path, hits


def test_test_patch_copied_to_tmp_fails(image):
    root, hits = image
    failing, excused = classify_hits([hits["tmp"]], run(root, [hits["tmp"]]), {"mypkg"})
    assert failing == ["/tmp/test_answer.py"] and excused == []


@pytest.mark.parametrize("key, source", [("setuptools", "dist-info RECORD"), ("stdlib", "conda-meta"),
                                         ("pkgs", "conda pkgs paths.json"), ("dpkg", "dpkg md5sums")])
def test_unmodified_third_party_file_is_excused(image, key, source):
    root, hits = image
    failing, excused = classify_hits([hits[key]], run(root, [hits[key]]), {"mypkg"})
    assert failing == [] and excused[0]["source"] == source


def test_modified_third_party_file_fails(image):
    root, hits = image
    origin = run(root, [hits["modified"]])
    assert origin[hits["modified"]["path"]] is None
    assert classify_hits([hits["modified"]], origin, {"mypkg"})[0] == [hits["modified"]["path"]]


def test_own_package_and_caches_are_never_excused(image):
    root, hits = image
    found = [hits["own"], hits["cache"]]
    origin = run(root, found)
    assert origin[hits["own"]["path"]]["package"] == "mypkg-9.9"
    failing, excused = classify_hits(found, origin, {"mypkg"})
    assert failing == sorted(h["path"] for h in found) and excused == []


def test_changed_bytes_and_archive_members_are_not_excused(image):
    root, hits = image
    stale = {**hits["setuptools"], "sha256": "0" * 64}
    member = {"path": SITE + "/setuptools/discovery.py!inner.py", "sha256": hits["setuptools"]["sha256"]}
    origin = run(root, [stale, member])
    assert origin[member["path"]] is None
    assert classify_hits([stale], origin, set())[0] == [stale["path"]]


BASE_STDLIB = "/opt/conda/lib/python3.12/idlelib/idle_test/test_undo.py"
BASE = {"ref": "continuumio/miniconda3:24.7.1-0", "id": "sha256:b", "python_bin": "/opt/conda/bin/python",
        "digest": "continuumio/miniconda3@sha256:b"}


def base_digests(root: Path, paths: list[str]) -> dict:
    proc = subprocess.run([sys.executable, "-I", "-S", "-c", DIGEST_CODE],
                          input=json.dumps({"root": str(root), "paths": paths}).encode(), stdout=subprocess.PIPE, check=True)
    return json.loads(proc.stdout)


def test_file_identical_to_the_base_image_is_excused_and_others_fail(tmp_path):
    text = ("# idle test\n" + NEEDLE + "\n").encode()
    pristine = put(tmp_path / "image", BASE_STDLIB, text)
    put(tmp_path / "base", BASE_STDLIB, text)
    edited = put(tmp_path / "image", "/opt/conda/lib/python3.12/inspect.py", text)
    put(tmp_path / "base", "/opt/conda/lib/python3.12/inspect.py", b"# original\n")
    added = put(tmp_path / "image", "/opt/conda/lib/python3.12/site-packages/extra.py", text)
    tmp = put(tmp_path / "image", "/tmp/test_answer.py", text)
    found = [pristine, edited, added, tmp]
    origin = run(tmp_path / "image", found)
    assert all(v is None for v in origin.values())
    merged = base_matches(found, origin, base_digests(tmp_path / "base", [h["path"] for h in found]), BASE)
    failing, excused = classify_hits(found, merged, {"mypkg"})
    assert excused == [{"path": BASE_STDLIB, "source": "base image", "package": BASE["digest"]}]
    assert failing == sorted([edited["path"], added["path"], tmp["path"]])


def test_base_image_must_be_a_layer_prefix_and_present():
    layers = {"task": ["sha256:1", "sha256:2", "sha256:3"], "base": ["sha256:1", "sha256:2"], "other": ["sha256:9"]}

    def inspect(ref):
        return {"id": "id-" + ref, "layers": layers[ref], "repo_digests": [ref + "@sha256:x"]} if ref in layers else None

    assert base_image("task", {"other": "py", "base": "/opt/conda/bin/python"}, inspect)["ref"] == "base"
    assert base_image("task", {"other": "py", "missing": "py"}, inspect) is None
    assert base_image("base", {"task": "py"}, inspect) is None


ARCHIVAL_IMAGE = ("node: $Format:%H$\nnode-date: $Format:%cI$\n"
                  "describe-name: $Format:%(describe:tags=true,match=*[0-9]*)$\nref-names: $Format:%D$\n")
ARCHIVAL_ARCHIVE = ("node: 0123456789abcdef0123456789abcdef01234567\nnode-date: 2025-10-01T12:00:00+02:00\n"
                    "describe-name: v2025.09.1-12-g0123456\nref-names: \n")


def test_export_subst_placeholder_is_recognised_only_in_its_exact_form():
    assert export_subst_paths(".git_archival.txt  export-subst\n*.py text\n") == {".git_archival.txt"}
    assert export_subst_paths("*.txt export-subst\n") == set()
    assert is_expanded_placeholder(ARCHIVAL_IMAGE, ARCHIVAL_ARCHIVE)
    assert not is_expanded_placeholder(ARCHIVAL_IMAGE, ARCHIVAL_ARCHIVE + "extra: line\n")
    assert not is_expanded_placeholder(ARCHIVAL_IMAGE.replace("node:", "commit:"), ARCHIVAL_ARCHIVE)
    assert not is_expanded_placeholder("version = '1.0'\n", "version = '1.1'\n")
    assert not is_expanded_placeholder(ARCHIVAL_IMAGE, ARCHIVAL_IMAGE)
