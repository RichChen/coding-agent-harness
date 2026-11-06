"""Erratum E4: recount the code-level facts behind README/docs wording. No model calls, no Docker.

Source facts are read from the committed HEAD (`git show HEAD:<path>`), so uncommitted work in progress
does not leak in. Test counts use `pytest --collect-only` over git-tracked test modules only (untracked ones
are --ignore'd; tracked test files with uncommitted edits are listed); collecting does not run tests, so it
cannot start containers. Pass/skip splits are not re-measured here. Writes e4_fact_checks.json.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import AGENT, HARNESS, write_output  # noqa: E402

PYTHON = HARNESS / ".venv" / "Scripts" / "python.exe"


def git_lines(repo: Path, *args: str) -> list[str]:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", check=True)
    return [line for line in out.stdout.splitlines() if line]


def head_text(path: str) -> str:
    out = subprocess.run(["git", "-C", str(HARNESS), "show", f"HEAD:{path}"], capture_output=True, check=True)
    return out.stdout.decode("utf-8")


def test_counts(repo: Path) -> dict:
    tracked = [p for p in git_lines(repo, "ls-files", "tests") if re.search(r"(^|/)test_[^/]*\.py$", p) and "/fixtures/" not in p]
    on_disk = [p.relative_to(repo).as_posix() for p in (repo / "tests").rglob("test_*.py") if "fixtures" not in p.parts]
    untracked = sorted(set(on_disk) - set(tracked))
    dirty = sorted(p for p in git_lines(repo, "diff", "--name-only", "HEAD", "--", "tests") if p in tracked)
    cmd = [str(PYTHON), "-m", "pytest", "tests", "-q", "--collect-only", "-p", "no:cacheprovider"]
    cmd += [f"--ignore={p}" for p in untracked]
    out = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace")
    match = re.search(r"(\d+) tests? collected", out.stdout)
    return {"tracked_test_modules": len(tracked), "untracked_test_modules_ignored": untracked,
            "tracked_test_modules_with_uncommitted_edits": dirty,
            "collected": int(match.group(1)) if match else None, "head": git_lines(repo, "rev-parse", "HEAD")[0]}


def docker_profile_checks() -> dict:
    source = head_text("src/execution/docker.py")
    block = source[source.index('host = inspect["HostConfig"]'):source.index('raise DockerUnavailable("daemon did not enforce')]
    fields = re.findall(r'host\["(\w+)"\]|host\.get\(\s*[\'"](\w+)[\'"]|inspect\["Config"\]\["(\w+)"\]|inspect\.get\("(\w+)"', block)
    names = [next(x for x in group if x) for group in fields]
    return {"file": "src/execution/docker.py (DockerExecutionBackend.start)", "checked_fields": names, "count": len(names)}


def event_store() -> dict:
    source = head_text("src/tracing/store.py")
    create = re.search(r"CREATE TABLE events \(([^)]*)\)", source).group(1)
    columns = [c.strip().split()[0] for c in create.split(",")]
    return {"events_columns": columns, "has_previous_hash_column": any("prev" in c for c in columns),
            "per_row_hash": "hash" in columns, "sequence_check": "seq != len(events) + 1" in source}


def truncation_configs() -> dict:
    result = {}
    for path in sorted(git_lines(HARNESS, "ls-tree", "-r", "--name-only", "HEAD", "configs")):
        if not path.endswith(".yaml"):
            continue
        match = re.search(r"tool_output_max_chars:\s*(\d+)", head_text(path))
        if match:
            result[path] = int(match.group(1))
    return result


def recovery_scope() -> dict:
    session = head_text("src/runtime/session.py")
    loop = head_text("src/runtime/loop.py")
    return {"write_file_only_proof": "before/after proof for write_file only" in session,
            "unknown_outcome_status": '"outcome_unknown"' in session,
            "docker_resume_refused": "Docker interrupted-session resume requires explicit reconciliation" in loop}


def main() -> None:
    result = {
        "schema": "errata-2026-11.e4.v1",
        "harness_tests": test_counts(HARNESS),
        "agent_tests": test_counts(AGENT),
        "docker_profile_checks": docker_profile_checks(),
        "event_store": event_store(),
        "tool_output_max_chars": truncation_configs(),
        "recovery_scope": recovery_scope(),
    }
    path = write_output("e4_fact_checks.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("wrote", path)


if __name__ == "__main__":
    main()
