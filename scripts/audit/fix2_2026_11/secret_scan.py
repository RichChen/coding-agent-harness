"""Scan git-tracked content and full history of both repos for API keys. Report only; nothing is modified.

Covered per repo: tracked files as they are in the working tree, every blob reachable from any ref
(each distinct blob once), and all commit messages.

Real keys are the KEY/TOKEN/SECRET values in the two repos' untracked .env files. They are matched as
literals and never written out. Generic patterns (sk-..., Bearer ...) are reported as masked prefixes
with path counts, so fixture keys can be told apart from real ones.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
HARNESS = HERE.parents[2]
AGENT = HARNESS.parent / "coding-agent"
REPOS = [HARNESS, AGENT]
# The lookbehind keeps task-<hex> ids and mask-* labels from matching as sk-...
PATTERNS = [rb"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{16,}", rb"(?i:bearer)\s+[A-Za-z0-9._~+/=-]{16,}"]
EXAMPLES = 5


def git(repo: Path, *args: str, stdin: bytes | None = None) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], input=stdin, capture_output=True, check=True).stdout


def env_secrets() -> list[bytes]:
    values = set()
    for repo in REPOS:
        env = repo / ".env"
        if not env.exists():
            continue
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            name, sep, value = line.partition("=")
            value = value.strip().strip("\"'")
            if sep and re.search(r"KEY|TOKEN|SECRET", name) and len(value) >= 12:
                values.add(value.encode())
    return sorted(values)


def mask(token: bytes) -> str:
    return f"{token[:7].decode(errors='replace')}…({len(token)} chars)"


def history_blobs(repo: Path) -> dict[str, set[str]]:
    paths: dict[str, set[str]] = defaultdict(set)
    for line in git(repo, "rev-list", "--all", "--objects").decode("utf-8", "replace").splitlines():
        sha, _, path = line.partition(" ")
        paths[sha].add(path)
    shas = "\n".join(paths).encode() + b"\n"
    kinds = git(repo, "cat-file", "--batch-check=%(objectname) %(objecttype)", stdin=shas).decode().splitlines()
    return {sha: {p for p in paths[sha] if p} for sha, kind in (k.split() for k in kinds) if kind == "blob"}


def read_blobs(repo: Path, shas: list[str]):
    out = git(repo, "cat-file", "--batch", stdin=("\n".join(shas) + "\n").encode())
    pos = 0
    for _ in shas:
        header_end = out.index(b"\n", pos)
        sha, _kind, size = out[pos:header_end].split()
        start = header_end + 1
        yield sha.decode(), out[start:start + int(size)]
        pos = start + int(size) + 1


def scan_repo(repo: Path, regex: re.Pattern[bytes], secrets: list[bytes]) -> dict:
    real: set[str] = set()
    hits: dict[str, set[str]] = defaultdict(set)

    def record(where: str, data: bytes) -> None:
        for match in regex.finditer(data):
            token = match.group(0)
            if any(s in token for s in secrets):
                real.add(where)
            else:
                hits[mask(token)].add(where)

    tracked = [p for p in git(repo, "ls-files", "-z").decode("utf-8", "replace").split("\0") if p]
    tracked_bytes = 0
    for rel in tracked:
        path = repo / rel
        if path.is_file():
            data = path.read_bytes()
            tracked_bytes += len(data)
            record(f"worktree:{rel}", data)

    blobs = history_blobs(repo)
    blob_bytes = 0
    for sha, data in read_blobs(repo, sorted(blobs)):
        blob_bytes += len(data)
        for path in sorted(blobs[sha]) or ["?"]:
            record(f"history:{path}", data)

    messages = git(repo, "log", "--all", "--format=%H%x00%B%x00")
    parts = messages.split(b"\0")
    for commit, body in zip(parts[0::2], parts[1::2]):
        record(f"commit-message:{commit.strip().decode()[:12]}", body)

    real_commits = {}
    for where in sorted(real):
        if where.startswith("history:"):
            path = where.removeprefix("history:")
            log = git(repo, "log", "--all", "--format=%h", "--", path).decode().split()
            real_commits[where] = log[:EXAMPLES]
    return {
        "repo": repo.name,
        "head": git(repo, "rev-parse", "--short", "HEAD").decode().strip(),
        "commits": len(git(repo, "rev-list", "--all").split()),
        "tracked_files": len(tracked),
        "tracked_bytes": tracked_bytes,
        "history_blobs": len(blobs),
        "history_blob_bytes": blob_bytes,
        "real_secret_locations": sorted(real),
        "real_secret_commits": real_commits,
        "pattern_hits": [{"token": t, "locations": len(ws), "examples": sorted(ws)[:EXAMPLES]}
                         for t, ws in sorted(hits.items(), key=lambda kv: (-len(kv[1]), kv[0]))],
    }


def main() -> int:
    started = time.monotonic()
    secrets = env_secrets()
    regex = re.compile(b"|".join([*(re.escape(s) for s in secrets), *PATTERNS]))
    result = {
        "schema": "fix2.secret-scan.v2",
        "scope": "git tracked files (working tree), all blobs reachable from any ref, all commit messages",
        "real_secret_count": len(secrets),
        "repos": [scan_repo(repo, regex, secrets) for repo in REPOS if (repo / ".git").exists()],
    }
    result["elapsed_seconds"] = round(time.monotonic() - started)
    out = HERE / "secret_scan.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({r["repo"]: {"real": len(r["real_secret_locations"]), "pattern_tokens": len(r["pattern_hits"])}
                      for r in result["repos"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
