"""Trusted standalone Linux/Python 3.9 helper, sent as fixed -I -S -c code.

No project imports: candidate Python is never imported. This file is also the
single container-side scanner used for initial/candidate/replica trees.
"""
import base64
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile

SLEEP_CODE = "import time; time.sleep(604800)"
CHUNK = 48 * 1024


class Rejected(Exception):
    def __init__(self, reason, evidence=None):
        self.reason, self.evidence = reason, evidence or {}


def path_ok(path):
    try:
        path.encode("utf-8", "strict")
    except UnicodeError:
        raise Rejected("invalid_path")
    if (not path or path.startswith("/") or "\\" in path or "\0" in path
            or (len(path) > 1 and path[0].isascii() and path[0].isalpha() and path[1] == ":")
            or any(p in ("", ".", "..") for p in path.split("/"))):
        raise Rejected("invalid_path")


def process_record(pid):
    try:
        text = open("/proc/%d/stat" % pid).read()
        start = int(text[text.rfind(")") + 2:].split()[19])
        threads = sorted(int(t) for t in os.listdir("/proc/%d/task" % pid))
    except (OSError, ValueError, IndexError):
        raise Rejected("workspace_not_quiescent", {"pid": pid, "state": "unreadable"})
    return {"pid": pid, "starttime": start, "threads": threads}


def processes(expected, python):
    pids = sorted(int(p) for p in os.listdir("/proc") if p.isdigit())
    records = [process_record(pid) for pid in pids]
    if pids != sorted([1, os.getpid()]) or any(r["threads"] != [r["pid"]] for r in records):
        raise Rejected("workspace_not_quiescent", {"processes": records})
    first = next(r for r in records if r["pid"] == 1)
    command = open("/proc/1/cmdline", "rb").read().split(b"\0")[:-1]
    if command != [python.encode(), b"-I", b"-S", b"-c", SLEEP_CODE.encode()]:
        raise Rejected("workspace_not_quiescent", {"pid": 1, "state": "identity_mismatch"})
    if expected is not None and first != expected:
        raise Rejected("workspace_not_quiescent", {"pid": 1, "state": "replaced"})
    if pids != sorted(int(p) for p in os.listdir("/proc") if p.isdigit()):
        raise Rejected("workspace_not_quiescent")
    return records


def signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def inventory(root, policy):
    """Metadata-only preflight, including excluded subtrees; no content leaks."""
    files, metadata, cached = [], {}, []
    spellings = {}

    def walk(fd, prefix, excluded=False):
        metadata[prefix] = signature(os.fstat(fd))
        for name in sorted(os.listdir(fd)):
            relative = prefix + "/" + name if prefix else name
            path_ok(relative)
            if name.startswith(".env") or name in policy["private_names"]:
                raise Rejected("private_path")
            folded = relative.casefold()
            if folded in spellings and spellings[folded] != relative:
                raise Rejected("path_collision")
            spellings[folded] = relative
            if len(spellings) > policy["max_nodes"]:
                raise Rejected("node_limit")
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            metadata[relative] = signature(info)
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise Rejected("unsupported_file_type")
            skip = (excluded or name in policy["excludes"]["directories"]
                    or name in policy["excludes"]["names"]
                    or any(name.endswith(s) for s in policy["excludes"]["suffixes"]))
            if skip:
                cached.append(relative)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    if signature(os.fstat(child)) != signature(info):
                        raise Rejected("workspace_changed")
                    walk(child, relative, skip)
                    if signature(os.fstat(child)) != signature(info):
                        raise Rejected("workspace_changed")
                finally:
                    os.close(child)
            elif not skip:
                files.append((relative, info))

    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        walk(descriptor, "")
    finally:
        os.close(descriptor)
    if len(files) > policy["max_entries"]:
        raise Rejected("manifest_limit")
    if sum(info.st_size for _, info in files) > policy["max_tree_bytes"]:
        raise Rejected("tree_byte_limit")
    return sorted(files, key=lambda pair: pair[0].encode()), metadata, cached


def safe_open(root, path):
    parts = path.split("/")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    finally:
        os.close(fd)


def scan(root, policy, base, keep):
    files, metadata, cached = inventory(root, policy)
    entries, bodies = [], {}
    for path, info in files:
        fd = safe_open(root, path)
        try:
            if signature(os.fstat(fd)) != signature(info):
                raise Rejected("workspace_changed")
            data = bytearray()
            while True:
                chunk = os.read(fd, CHUNK)
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > info.st_size:
                    raise Rejected("workspace_changed")
            if len(data) != info.st_size or signature(os.fstat(fd)) != signature(info):
                raise Rejected("workspace_changed")
        finally:
            os.close(fd)
        entry = dict(path=path, kind="regular", mode=stat.S_IMODE(info.st_mode), size=len(data),
                     sha256=hashlib.sha256(data).hexdigest())
        entries.append(entry)
        changed = base is not None and (path not in base or base[path]["sha256"] != entry["sha256"]
                                        or base[path]["size"] != entry["size"])
        python = path.endswith(".py") and len(data) <= policy["max_python_file_bytes"]
        if keep and (changed or python):
            bodies[path] = bytes(data)
    return entries, bodies, metadata, cached


def snapshot(request, root="/workspace", keep=True):
    policy, expected = request["policy"], request.get("pid1")
    before = processes(expected, request["python"])
    base = request.get("base")
    base = None if base is None else {e["path"]: e for e in base}
    first = scan(root, policy, base, keep)
    middle = processes(expected, request["python"])
    second = scan(root, policy, base, False)
    after = processes(expected, request["python"])
    if before != middle or before != after or first[0] != second[0] or first[2:] != second[2:]:
        raise Rejected("workspace_changed")
    entries, bodies, _, cached = first
    changed = [] if base is None else [e["path"] for e in entries if e["path"] not in base
               or (e["sha256"], e["size"]) != (base[e["path"]]["sha256"], base[e["path"]]["size"])]
    deleted = [] if base is None else sorted(set(base) - {e["path"] for e in entries})
    if len(changed) + len(deleted) > policy["max_delta_files"]:
        raise Rejected("delta_file_limit")
    if sum(e["size"] for e in entries if e["path"] in changed) > policy["max_delta_bytes"]:
        raise Rejected("delta_byte_limit")
    corpus = [e["path"] for e in entries if e["path"].endswith(".py") and e["size"] <= policy["max_python_file_bytes"]]
    if sum(e["size"] for e in entries if e["path"] in corpus) > policy["max_corpus_bytes"]:
        raise Rejected("corpus_byte_limit")
    return dict(entries=entries, bodies=bodies, changed=changed, deleted=deleted, corpus=corpus,
                excluded=[{"rule": "cache-v1", "count": len(cached)},
                          {"rule": "python-file-size", "count": sum(e["path"].endswith(".py")
                           and e["size"] > policy["max_python_file_bytes"] for e in entries)}],
                barrier={"protocol": "quiescence-v1", "before": before, "after": after,
                         "pid1": next(r for r in before if r["pid"] == 1)})


def apply(request):
    initial = snapshot(request, keep=False)
    if initial["entries"] != request["base"]:
        raise Rejected("base_manifest_mismatch")
    # Metadata preflight already rejected links/special/private files everywhere.
    for root, directories, files in os.walk("/workspace", topdown=False):
        for name in files:
            if name == ".coverage" or name.endswith((".pyc", ".pyo")):
                os.unlink(os.path.join(root, name))
        for name in directories:
            if name in ("__pycache__", ".pytest_cache"):
                shutil.rmtree(os.path.join(root, name))
    target = {e["path"]: e for e in request["target"]}
    for path in request["deleted"]:
        path_ok(path)
        os.unlink("/workspace/" + path)
        parent = os.path.dirname("/workspace/" + path)
        while parent != "/workspace":
            try:
                os.rmdir(parent)
            except OSError:
                break
            parent = os.path.dirname(parent)
    for path, encoded in request["changed"]:
        path_ok(path)
        data = base64.b64decode(encoded, validate=True)
        entry = target[path]
        if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise Rejected("blob_mismatch")
        destination = "/workspace/" + path
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".workspace-", dir=os.path.dirname(destination))
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
                os.fchmod(stream.fileno(), entry["mode"])
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    result = snapshot(request, keep=False)
    if result["entries"] != request["target"]:
        raise Rejected("materialized_manifest_mismatch")
    return result


def emit(result):
    for entry in result.pop("entries"):
        print(json.dumps({"entry": entry}, ensure_ascii=False, separators=(",", ":")))
    for path, data in result.pop("bodies", {}).items():
        # Even empty blobs have a record. Offset and manifest length are checked.
        for offset in range(0, max(1, len(data)), CHUNK):
            print(json.dumps({"blob": path, "offset": offset,
                              "data": base64.b64encode(data[offset:offset + CHUNK]).decode()},
                             ensure_ascii=False, separators=(",", ":")))
    print(json.dumps({"complete": True, **result}, ensure_ascii=False, separators=(",", ":")))


def main():
    try:
        request = json.load(sys.stdin)
        if request["op"] == "seed":
            processes(None, request["python"])
            inventory("/opt/repo", request["policy"])
            for name in os.listdir("/opt/repo"):
                source, target = "/opt/repo/" + name, "/workspace/" + name
                if os.path.isdir(source):
                    shutil.copytree(source, target)
                else:
                    shutil.copy2(source, target)
            result = snapshot(request)
        elif request["op"] == "capture":
            result = snapshot(request)
        elif request["op"] == "apply":
            result = apply(request)
        else:
            raise Rejected("invalid_operation")
        emit(result)
    except Rejected as exc:
        print(json.dumps({"error": exc.reason, "evidence": exc.evidence}))
        return 2
    except Exception:
        # Never include arbitrary filenames, contents or exception strings.
        print(json.dumps({"error": "capture_io_error"}))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
