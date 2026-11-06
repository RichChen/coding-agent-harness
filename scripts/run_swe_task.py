#!/usr/bin/env python3
"""Run B0 on SWE-bench tasks listed in a dev manifest.

Gold patches, test patches, and FAIL_TO_PASS / PASS_TO_PASS stay on the host
grader path. They are not copied into the agent workspace or the prompt.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.eval.candidate_error import (
    classify_missing,
    classify_timeout,
    expected_empty,
    missing_count,
    regrade_verdict,
    timed_out_with_reason,
)
from src.eval.grader import tree_hash
from src.eval.image_grader import ImageGrader
from src.eval.repo_grader import RepositoryGrader, RepositorySpec
from src.eval.tool_errors import harness_tool_errors, tool_schemas
from src.execution.baseline_image_backend import BaselineImageBackend
from src.execution.docker import MAX_FILES
from src.harness.factory import build_agent, load_config
from src.llm.client import LLMClient, LLMResponse, LLMStreamEvent, StreamCapability
from src.policy.cny_budget import A5BudgetPlan, CnyBudgetLedger, PriceSnapshot
from src.runtime.session import RunSession, compatibility, plain

# Pilot targets from coding-agent assignments.json. Never tune on these.
HOLDOUT = {
    "pytest-dev__pytest-9359",
    "pytest-dev__pytest-7432",
    "pytest-dev__pytest-8365",
    "pytest-dev__pytest-7220",
    "scikit-learn__scikit-learn-25747",
    "scikit-learn__scikit-learn-25500",
    "scikit-learn__scikit-learn-25570",
    "scikit-learn__scikit-learn-25638",
    "sphinx-doc__sphinx-8595",
    "sphinx-doc__sphinx-8713",
    "sphinx-doc__sphinx-10451",
    "sympy__sympy-20442",
    "sympy__sympy-20154",
    "sympy__sympy-24152",
    "sympy__sympy-18532",
    "django__django-15781",
    "django__django-13265",
    "django__django-13448",
    "django__django-13590",
}
class _MockLLM(LLMClient):
    def __init__(self):
        self.model = "mock-model"
        self.supports_streaming = False
        self._owns_http_client = False
        self._http = None
        self._closed = False

    async def chat(self, messages, tools=None):
        return LLMResponse(
            text="mock finish without edits",
            finish_reason="stop",
            usage={"prompt_tokens": 0, "completion_tokens": 0, "provenance": "unknown"},
        )

    async def stream_chat(self, messages, tools=None):
        response = await self.chat(messages, tools)
        yield LLMStreamEvent(
            "llm_stream_done",
            {
                "finish_reason": "stop",
                "usage": response.usage,
                "has_tool_calls": False,
                "capability": StreamCapability.NON_STREAMING.value,
                "response": response,
            },
        )

    async def aclose(self):
        self._closed = True


EDIT_TOOLS = {"edit_file", "search_replace", "write_file"}
PYTHON_PATHS = {
    "django": (".",),
    "pytest-dev": ("src",),
    "scikit-learn": (".",),
    "sphinx-doc": (".",),
    "sympy": (".",),
}


def load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def git_state(repo: Path) -> dict:
    def run(*args):
        return subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, check=False
        )

    head = run("rev-parse", "HEAD")
    dirty = run("status", "--porcelain")
    return {
        "path": str(repo),
        "head": head.stdout.strip() if head.returncode == 0 else None,
        "dirty": bool(dirty.stdout.strip()) if dirty.returncode == 0 else None,
    }


def read_tree(root: Path) -> dict[str, bytes]:
    files = {}
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        if ".git" in relative.split("/"):
            continue
        files[relative] = path.read_bytes()
    return files


def usable_nodeid(node: str) -> bool:
    if not isinstance(node, str) or not node or node.startswith(("[", "-")):
        return False
    return "\n" not in node and "\x00" not in node


def parse_id_list(value) -> list[str]:
    if isinstance(value, str):
        return list(json.loads(value))
    return list(value or [])


def prefix_sympy_nodeids(nodes: list[str], test_paths, root=None) -> list[str]:
    files = [path for path in test_paths if "/tests/" in path.replace("\\", "/") and str(path).endswith(".py")]
    if not files:
        return list(nodes)
    locations: dict[str, list[str]] = {}
    if root is not None:
        root = Path(root)
        for relative in files:
            file = root / relative
            if not file.is_file():
                continue
            text = file.read_text(encoding="utf-8", errors="replace")
            for node in nodes:
                if "::" in node or node.endswith(".py"):
                    continue
                if f"def {node}(" in text:
                    locations.setdefault(node, []).append(relative)
    prefixed = []
    for node in nodes:
        if "::" in node or node.endswith(".py"):
            prefixed.append(node)
        elif locations.get(node):
            prefixed.extend(f"{relative}::{node}" for relative in locations[node])
        elif root is None:
            prefixed.append(f"{files[0]}::{node}")
    return prefixed


def django_nodeids(raw: list[str]) -> list[str]:
    converted = []
    for node in raw:
        if " (" in node and node.endswith(")"):
            name, rest = node.split(" ", 1)
            module = rest.strip("()")
            converted.append(module if module.endswith("." + name) else module + "." + name)
        else:
            converted.append(node)
    return converted


TEST_COMMANDS = {
    "django": "python tests/runtests.py <module>",
    "pytest-dev": "python -m pytest <test path>",
    "scikit-learn": "python -m pytest <test path>",
    "sphinx-doc": "python -m pytest <test path>",
    "sympy": "python -m pytest <test path>",
}


def issue_text(public: dict) -> str:
    issue = public.get("issue_text")
    if not isinstance(issue, str) or not issue.strip():
        raise ValueError("public task has no issue_text")
    if issue.startswith("Public issue for "):
        raise ValueError("public task issue_text is a placeholder")
    return issue


def public_issue(task: dict) -> str:
    public = load_json(Path(task["public_task"]))
    if public.get("gold_patch") or public.get("test_patch") or public.get("FAIL_TO_PASS"):
        raise ValueError("public task embeds private grader fields")
    issue = issue_text(public)
    ref = public.get("repo_ref")
    if not isinstance(ref, dict) or not ref.get("repo_id") or not ref.get("revision"):
        raise ValueError("public task has no repo_ref.repo_id and revision")
    repo = ref["repo_id"]
    revision = ref["revision"]
    if task.get("family") not in TEST_COMMANDS:
        raise ValueError("no test command for family " + repr(task.get("family")))
    test_command = TEST_COMMANDS[task["family"]]
    return (
        "You are repairing an open-source repository from a public issue only.\n"
        f"Repository: {repo}\nBase revision: {revision}\n"
        "The source tree is mounted in the workspace. Dependencies are already installed. "
        "There is no network. Do not pip install. "
        f"Run tests from the repository root with `{test_command}`.\n"
        "Inspect the tree, reproduce the bug, edit the source, and stop when you believe the fix is in place.\n\n"
        f"Public issue:\n{issue}"
    )


def build_prompt(task: dict, prompt_suffix: str | None = None) -> str:
    """The first user message. A non-empty suffix follows the issue after one blank line."""
    prompt = public_issue(task)
    if prompt_suffix:
        prompt += "\n\n" + prompt_suffix
    return prompt


def grader_spec(task: dict, base: dict[str, bytes] | None = None) -> RepositorySpec:
    receipt = load_json(ROOT / task["receipt"])
    binding = receipt["private_source_binding"]
    private = load_json(Path(binding["path"]))
    if private.get("instance_id") != task["id"]:
        raise ValueError("private source row does not match the task id")
    family = task["family"]
    if not binding.get("test_patch_paths") or not binding.get("gold_patch_paths"):
        raise ValueError("receipt has no test_patch_paths or gold_patch_paths")
    test_paths = tuple(binding["test_patch_paths"])
    selected = task.get("grader_nodeids")
    if selected is not None:
        f2p = list(selected["f2p"])
        p2p = list(selected["p2p"])
    else:
        f2p = parse_id_list(private.get("FAIL_TO_PASS"))
        p2p = parse_id_list(private.get("PASS_TO_PASS"))
        if family == "django":
            f2p = django_nodeids(f2p)
            p2p = django_nodeids(p2p)
        if family == "sympy":
            f2p = prefix_sympy_nodeids(f2p, test_paths)
            p2p = prefix_sympy_nodeids(p2p, test_paths)
        f2p = [node for node in f2p if usable_nodeid(node)]
        p2p = [node for node in p2p if usable_nodeid(node)]
    excluded = set(task.get("p2p_excluded") or [])
    if excluded:
        p2p = [node for node in p2p if node not in excluded]
    editable = tuple(binding["gold_patch_paths"])
    baseline_root = ROOT / task["baseline"]
    original_root = ROOT / task["original"]
    overlay = {}
    for relative in test_paths:
        candidate_path = baseline_root / relative
        if not candidate_path.is_file():
            continue
        updated = candidate_path.read_bytes()
        original = None if base is None else base.get(relative)
        if original is None:
            original_path = original_root / relative
            original = original_path.read_bytes() if original_path.is_file() else b""
        if updated != original:
            overlay[relative] = base64.b64encode(updated).decode("ascii")
    return RepositorySpec(
        task_id=task["id"],
        base_hash=hashlib.sha256(task["id"].encode()).hexdigest(),
        image=receipt["baseline_image_digest"],
        editable_paths=editable,
        test_paths=test_paths,
        python_paths=PYTHON_PATHS[family],
        expected_nodeids=tuple(f2p + p2p),
        test_overlay=overlay,
        baseline_passed=tuple(p2p),
        timeout_seconds=float(task["grader_timeout_seconds"]),
    )


def apply_delta(base: dict[str, bytes], delta: dict | None) -> dict[str, bytes]:
    candidate = dict(base)
    if not delta:
        return candidate
    for name in delta.get("deleted") or []:
        candidate.pop(name, None)
    for name, change in (delta.get("changed") or {}).items():
        encoded = change["content"] if isinstance(change, dict) else change
        candidate[name] = base64.b64decode(encoded)
    return candidate


class _NodeidGrader(RepositoryGrader):
    """Run the declared node ids. test_paths stays a file prefix for the overlay check."""

    def __init__(self, nodeids: tuple[str, ...]):
        super().__init__(context="desktop-linux")
        self._nodeids = list(nodeids)

    async def execute(self, files, **kwargs):
        kwargs["selectors"] = self._nodeids
        return await super().execute(files, **kwargs)


def solved(evaluation: dict) -> bool:
    cases = evaluation.get("cases") or []
    if not cases or evaluation.get("status") != "pass":
        return False
    f2p_failed = evaluation.get("failed") or 0
    assigned = evaluation.get("regression_assigned")
    passed = evaluation.get("regression_passed")
    regression_ok = assigned in (None, 0) or passed == assigned
    return f2p_failed == 0 and regression_ok and all(case.get("satisfied") is True for case in cases)


def _event_tool_name(event: dict) -> str:
    payload = event.get("payload") or {}
    return str(payload.get("name") or payload.get("tool") or "")


def summarize_events(events: list[dict], result_plain: dict | None, run_error: str | None, artifacts_dir: Path | None = None) -> dict:
    """Count tool.intent events. Edit round is the model.started count before the first edit."""
    tool_calls = 0
    model_rounds = 0
    first_edit = None
    saw_edit = False
    for event in events:
        kind = event.get("event_type")
        if kind == "model.started":
            model_rounds += 1
        elif kind == "tool.intent":
            tool_calls += 1
            if _event_tool_name(event) in EDIT_TOOLS:
                saw_edit = True
                if first_edit is None:
                    first_edit = model_rounds
    return {
        "tool_calls": tool_calls,
        "first_edit_step": first_edit,
        "saw_edit": saw_edit,
        "stop_reason": _stop_reason(events, result_plain or {}, run_error, artifacts_dir),
    }


def _named_reason(payload: dict | None) -> str | None:
    if not payload:
        return None
    for key in ("termination_kind", "failure_reason", "stop_reason"):
        value = payload.get(key)
        if value and str(value).lower() not in {"none", "null"}:
            return str(value)
    if payload.get("success") is True:
        return "completed"
    return None


def _deref_finished(payload: dict, artifacts_dir: Path | None) -> dict:
    if set(payload) != {"artifact_ref"} or artifacts_dir is None:
        return payload
    ref = payload["artifact_ref"]
    name = ref if isinstance(ref, str) else ref.get("sha256") or ref.get("path")
    path = Path(artifacts_dir) / str(name)
    return json.loads(path.read_text(encoding="utf-8"))


def _stop_reason(events: list[dict], result_plain: dict, run_error: str | None, artifacts_dir: Path | None = None) -> str:
    if run_error:
        return run_error
    from_result = _named_reason(result_plain)
    if from_result:
        return from_result
    finished = None
    for event in events:
        if event.get("event_type") == "run.finished":
            finished = event.get("payload") or {}
    if isinstance(finished, dict):
        try:
            finished = _deref_finished(finished, artifacts_dir)
        except (OSError, json.JSONDecodeError, TypeError):
            finished = None
    return _named_reason(finished) or "unknown"


def delta_changes(delta: dict | None) -> list[str]:
    if not delta:
        return []
    names = list((delta.get("changed") or {}).keys())
    names.extend(delta.get("deleted") or [])
    return sorted(names)


def delta_lost(events_summary: dict, delta: dict | None) -> bool:
    return bool(events_summary.get("saw_edit")) and not delta_changes(delta)


def make_ledger(state_path: Path) -> CnyBudgetLedger:
    price = PriceSnapshot(
        provider="DeepSeek",
        model="deepseek-flash",
        snapshot_id="deepseek-flash@DeepSeek-V4.1-Flash",
        currency="CNY",
        prompt_cache_hit_per_million=Decimal("0.040"),
        prompt_cache_miss_per_million=Decimal("2.000"),
        completion_per_million=Decimal("8.000"),
        source_url="https://api-docs.deepseek.com/quick_start/pricing",
        retrieved_on="2026-09-21",
        verified=True,
    )
    # One run stays inside the sprint cap of 1 CNY. USD budget is not used.
    plan = A5BudgetPlan(
        total_cny=Decimal("1.000"),
        build_cny=Decimal("0.000"),
        pilot_cny=Decimal("1.000"),
        reserve_cny=Decimal("0.000"),
        build_calls=0,
        pilot_calls=40,
    )
    return CnyBudgetLedger(price, plan, state_path=state_path)


def select_tasks(manifest: dict, requested: list[str]) -> list[dict]:
    tasks = manifest["tasks"]
    if requested:
        wanted = set(requested)
        tasks = [task for task in tasks if task["id"] in wanted]
        missing = wanted - {task["id"] for task in tasks}
        if missing:
            raise SystemExit("unknown task id: " + ", ".join(sorted(missing)))
    blocked = [task["id"] for task in tasks if task["id"] in HOLDOUT]
    if blocked:
        raise SystemExit("hold-out task refused: " + ", ".join(blocked))
    return tasks


DEV_HANDOFF = ROOT / "docs" / "sprint" / "memory-source-runs.json"
MAIN_TARGETS = ROOT / "configs" / "sprint" / "main-targets.yaml"
MAIN_CALIBRATION = ROOT / "docs" / "sprint" / "main-calibration"


async def empty_regrade(task: dict, image: str, spec, f2p: list[str]) -> dict:
    """Grade the same image and test overlay with an empty delta. No model calls."""
    return await ImageGrader().evaluate(
        image=image,
        delta={"changed": {}, "deleted": []},
        test_overlay=spec.test_overlay,
        f2p=f2p,
        p2p=list(spec.baseline_passed),
        django=task.get("family") == "django",
        sympy=False,
        timeout=float(task["grader_timeout_seconds"]),
    )


def calibration_row(task_id: str, root: Path = MAIN_CALIBRATION) -> dict | None:
    path = root / (task_id + ".json")
    return load_json(path) if path.is_file() else None


def parse_created_at(value, what: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("created_at missing: " + what)
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("created_at cannot be parsed for " + what + ": " + value) from exc
    if parsed.tzinfo is None:
        raise ValueError("created_at has no timezone for " + what + ": " + value)
    return parsed


def handoff_dates(path: Path = DEV_HANDOFF) -> dict:
    dates = {}
    if path.is_file():
        for run in load_json(path).get("runs") or []:
            dates.setdefault(run["source_id"], {"repo": run.get("repo"), "created_at": run.get("created_at")})
    return dates


def target_created_at(task_id: str, dev_dates: dict, targets_path: Path = MAIN_TARGETS) -> str:
    """Pool targets take their date from main-targets.yaml. Dev targets take it from the dev handoff."""
    pool = {}
    if targets_path.is_file():
        pool = {row["id"]: row.get("created_at") for row in load_yaml(targets_path).get("tasks") or []}
    if task_id in pool:
        value = pool[task_id]
    else:
        value = (dev_dates.get(task_id) or {}).get("created_at")
    if not value:
        raise SystemExit("B2 needs the target created_at and none was found: " + task_id)
    parse_created_at(value, task_id)
    return value


def dated_cards(path: Path) -> Path:
    """Fill created_at from the handoff when the card file does not have it. An undated card is an error."""
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if rows and all(row.get("created_at") for row in rows):
        return path
    dates = {source: meta.get("created_at") for source, meta in handoff_dates().items()}
    missing = []
    for row in rows:
        created = row.get("created_at") or dates.get(row.get("source_id"))
        if not created:
            missing.append(str(row.get("source_id")))
            continue
        row["created_at"] = created
    if missing:
        raise ValueError("memory cards without created_at: " + ", ".join(missing))
    out = ROOT / "runs" / "sprint5-cards" / (path.stem + "-dated.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    return out


def temporal_filter(cards, *, task_id: str, repo: str, created_at: str, dates: dict):
    """Keep same-repo cards whose source task was created earlier. Drop the current task.

    Repo and date come from the card. A handoff row for the same source must agree with it.
    """
    target = parse_created_at(created_at, task_id)
    kept, dropped = [], []
    for card in cards:
        source = getattr(card, "source_id", None)
        card_repo = getattr(card, "repo", None)
        if not card_repo:
            raise ValueError("memory card has no repo: " + str(source))
        card_date = parse_created_at(getattr(card, "created_at", None), str(source))
        meta = dates.get(source) or {}
        if meta.get("repo") and meta["repo"] != card_repo:
            raise ValueError("card repo disagrees with the handoff for " + str(source))
        if meta.get("created_at") and parse_created_at(meta["created_at"], str(source)) != card_date:
            raise ValueError("card created_at disagrees with the handoff for " + str(source))
        if source == task_id or card_repo != repo or card_date >= target:
            dropped.append(source)
        else:
            kept.append(card)
    return tuple(kept), dropped


def leave_one_out(cards, task_id: str):
    """Drop cards mined from the task being solved. Those cards are the answer."""
    kept, dropped = [], []
    for card in cards:
        if getattr(card, "source_id", None) == task_id:
            dropped.append(card.source_id)
        else:
            kept.append(card)
    return tuple(kept), dropped


def memory_summary(variant: str, cards_path: Path, dropped: list[str], decision: dict) -> dict:
    items = []
    for event in decision.get("events") or []:
        items.extend(event.get("items") or [])
    return {
        "variant": variant,
        "cards_sha256": hashlib.sha256(cards_path.read_bytes()).hexdigest(),
        "cards_before": None,
        "cards_after": None,
        "filtered_card_ids": dropped,
        "injected": [
            {"id": item["source_id"], "bytes": item["bytes"]}
            for item in items if item.get("status") == "selected"
        ],
        "rejected": [
            {"id": item["source_id"], "reason": item.get("reason")}
            for item in items if item.get("status") == "rejected_invalid"
        ],
    }


def resolve_image(task: dict, receipt: dict, *, legacy_image: bool, mock: bool):
    """Every real run uses a clean image. A legacy image is mock-only."""
    if legacy_image:
        if not mock:
            raise SystemExit("legacy images cannot call the model")
        return receipt["baseline_image_digest"], None
    clean_path = ROOT / "docs" / "sprint" / "clean-images" / (task["id"] + ".json")
    if not clean_path.is_file():
        raise SystemExit("clean image required: " + task["id"])
    clean = load_json(clean_path)
    if not clean.get("answer_isolation_pass"):
        return clean.get("clean_image_digest"), clean
    return clean["clean_image_digest"], clean


def prerun_check(task: dict, *, mock: bool, legacy_image: bool = False):
    """Every check before the first model call. Returns (grader spec, image, clean record, failure reason or None)."""
    spec = grader_spec(task)
    receipt = load_json(ROOT / task["receipt"])
    image, clean = resolve_image(task, receipt, legacy_image=legacy_image, mock=mock)
    if clean is not None and not clean.get("answer_isolation_pass"):
        return spec, image, clean, "clean image failed the filesystem scan"
    if clean is not None:
        from scripts.build_clean_baseline import source_only_check
        from scripts.rewritten_sources import overrides_for
        from src.eval.a5_image_tree import archive_manifest
        archive = Path(receipt["source_attempt_ref"]).parent / "source.tar.gz"
        manifest = archive_manifest(archive, task["repo"], receipt["base_revision"])
        matched, _hash = source_only_check(image, manifest, overrides_for(task, manifest))
        if not matched:
            return spec, image, clean, "opt/repo source-only hash mismatch"
    return spec, image, clean, None


def run_infra_reason(run_error, backend, delta, snapshot_error):
    """Why the agent phase cannot be graded, or None. Such a run is infra_error; it is never graded as an empty patch."""
    if run_error == "StoreError":
        return "StoreError"
    unavailable = getattr(backend, "unavailable_error", None)
    if unavailable:
        return "container start failed: " + str(unavailable)[:300]
    if snapshot_error:
        return "candidate snapshot failed: " + str(snapshot_error)[:300]
    if delta is None:
        return "candidate snapshot missing"
    if getattr(backend, "cleanup_ok", None) is False:
        return "container cleanup failed"
    return None


def grader_infra_reason(evaluation: dict):
    """A grader that failed before the tests started is infra_error. A timeout after they started is a normal failure."""
    if evaluation.get("execution_status") is None and evaluation.get("reason"):
        return "grader failed before test execution: " + str(evaluation["reason"])[:300]
    return None


def clean_backend(image: str, reap_on_timeout: bool = False) -> BaselineImageBackend:
    return BaselineImageBackend(
        image=image,
        context="desktop-linux",
        memory_bytes=1024**3,
        workspace_bytes=256 * 1024**2,
        tmpfs_bytes=64 * 1024**2,
        network="none",
        reap_on_timeout=reap_on_timeout,
    )


SMOKE_FILE = "harness_smoke_tmp.txt"


async def tool_smoke(image: str, profile: Path) -> dict:
    """Call every agent tool once in the clean image. No model call. Nothing is exported or graded."""
    import tempfile
    from src.eval.tool_errors import traceback_exception
    from src.tools.base import ToolContext

    config = load_config(profile)
    backend = clean_backend(image, config["policy"]["command"].get("docker_reap_on_timeout", False))
    config["llm"]["api_key"] = config["llm"]["api_key"] or "smoke"
    calls = []
    record = {"calls": calls, "passed": False, "error": None, "python": None}
    with tempfile.TemporaryDirectory() as tmp:
        agent = build_agent(config, Path(tmp), execution_backend=backend)

        async def call(name, params):
            ctx = ToolContext(workspace_root=Path(tmp), sandbox=agent.sandbox,
                              file_size_limit_bytes=agent.file_size_limit_bytes,
                              tool_output_max_chars=agent.tool_output_max_chars,
                              read_file_max_lines=agent.read_file_max_lines,
                              call_id=f"smoke-{len(calls)}")
            result = await agent.tools.get(name).execute(dict(params), ctx)
            exc = traceback_exception(result.output or "")
            calls.append({"tool": name, "params": params, "success": result.success,
                          "error_kind": result.error_kind, "exception": exc,
                          "ok": bool(result.success) and exc is None})
            return result.output or ""

        try:
            await backend.start({})
            listing = await call("list_dir", {"path": "."})
            files = [line[4:] for line in listing.splitlines() if line.startswith("[F] ")]
            known = "setup.py" if "setup.py" in files else (files[0] if files else None)
            if known is None:
                raise ValueError("repository root lists no files")
            await call("read_file", {"path": known, "limit": 20})
            await call("search_code", {"pattern": "import", "path": known, "max_results": 3})
            version = await call("run_command", {"command": f"printf 'alpha\\nbeta\\n' > {SMOKE_FILE} && /usr/local/bin/python -V"})
            record["python"] = next((line.split()[1] for line in version.splitlines() if line.startswith("Python ")), None)
            await call("search_replace", {"path": SMOKE_FILE, "search": "alpha", "replace": "gamma"})
            await call("edit_file", {"path": SMOKE_FILE, "old_string": "beta", "new_string": "delta"})
            final = await call("read_file", {"path": SMOKE_FILE})
            record["edits_applied"] = "gamma" in final and "delta" in final
        except Exception as exc:
            record["error"] = type(exc).__name__ + ": " + str(exc)[:300]
        finally:
            await backend.close()
    record["cleanup_ok"] = backend.cleanup_ok
    names = {c["tool"] for c in calls}
    required = {"read_file", "list_dir", "search_code", "search_replace", "edit_file", "run_command"}
    record["passed"] = (record["error"] is None and required <= names and all(c["ok"] for c in calls)
                        and record.get("edits_applied") is True and backend.cleanup_ok is True)
    return record


async def run_task(task: dict, profile: Path, output: Path, *, mock: bool, variant: str = "B0", cards_path: Path | None = None, legacy_image: bool = False,
                   prompt_suffix: str | None = None, retry: dict | None = None,
                   retrieval=None, mock_llm=None, on_agent=None) -> dict:
    """retrieval: passed to build_agent (needed when the profile enables retrieve_code).
    mock_llm: with mock=True, replaces the default mock model. on_agent(agent) runs once the agent is built."""
    if mock_llm is not None and not mock:
        raise ValueError("mock_llm requires mock=True")
    if task["id"] in HOLDOUT:
        raise SystemExit("hold-out task refused: " + task["id"])
    output.mkdir(parents=True, exist_ok=True)
    workspace = output / "workspace"
    workspace.mkdir()
    spec, image, clean, failure = prerun_check(task, mock=mock, legacy_image=legacy_image)
    if failure:
        return _isolation_stop(task, variant, failure, mock=mock, profile=profile, output=output)
    config = load_config(profile)
    backend = clean_backend(image, config["policy"]["command"].get("docker_reap_on_timeout", False))
    # CNY ledger is the only budget that stops a run. Do not set max_cost_usd here.
    if mock:
        config["llm"]["api_key"] = config["llm"]["api_key"] or "mock"
    prompt = build_prompt(task, prompt_suffix)
    fingerprints = {
        "issue_sha256": hashlib.sha256(issue_text(load_json(Path(task["public_task"]))).encode("utf-8")).hexdigest(),
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
    }
    memory = None
    registry = None
    if cards_path is not None:
        from src.policy.registry import PolicyPluginRegistry
        from src.policy.reposkill import load_adapter, register_memory_cards, source_digest

        agent_root = ROOT.parent / "coding-agent"
        digest = source_digest(agent_root)
        load_adapter(agent_root, digest)
        from reposkill_agent.memory.card_plugin import MemoryCardPlugin
        from reposkill_agent.memory.cards import load_memory_cards
        cards_path = dated_cards(cards_path)
        loaded = load_memory_cards(cards_path)
        dates = handoff_dates()
        if variant == "B0":
            cards, dropped = leave_one_out(loaded, task["id"])
        else:
            created_at = target_created_at(task["id"], dates)
            cards, dropped = temporal_filter(
                loaded, task_id=task["id"], repo=task["repo"], created_at=created_at, dates=dates,
            )
        public = load_json(Path(task["public_task"]))
        target = ROOT / task["original"]
        registry = PolicyPluginRegistry()
        register_memory_cards(
            registry, source_root=agent_root, approved_sha256=digest,
            variant=variant, cards=cards, repo=task["repo"], issue=issue_text(public),
            target_root=target,
        )
        config["runtime"]["context"]["plugin"] = "memory-cards"
        preview = MemoryCardPlugin(
            variant=variant, cards=cards, repo=task["repo"], issue=issue_text(public),
            target_root=target,
        )
        memory = memory_summary(variant, cards_path, dropped, await preview.decide({}))
        memory["cards_before"] = len(loaded)
        memory["cards_after"] = len(cards)
    agent = build_agent(config, workspace, execution_backend=backend, plugin_registry=registry, retrieval=retrieval)
    if mock:
        agent.llm = mock_llm if mock_llm is not None else _MockLLM()
    if on_agent is not None:
        on_agent(agent)
    requested_model = model_identity(agent.llm)
    ledger = make_ledger(output / "cny-ledger.json")
    before = ledger.cost_committed
    calls_before = ledger.calls_started
    agent.retry.cny_ledger = ledger
    expected = compatibility(
        workspace=workspace,
        code_id="sprint-b0",
        config={"profile": str(profile)},
        backend=backend.capabilities,
    )
    run_error = None
    result = None
    try:
        with RunSession.create(output / "session", task_id=task["id"], expected=expected, timeout_seconds=config["runtime"]["total_timeout_seconds"]) as session:
            result = await session.run(agent, prompt)
            session.store.export_jsonl(output / "events.jsonl")
    except Exception as exc:
        run_error = type(exc).__name__
    finally:
        close = getattr(agent.llm, "aclose", None)
        if close is not None:
            await close()
    delta = backend.candidate_delta
    snapshot_error = getattr(backend, "snapshot_error", None)
    if delta is not None:
        (output / "candidate-delta.json").write_text(json.dumps(delta, indent=2), encoding="utf-8")
    f2p = [node for node in spec.expected_nodeids if node not in spec.baseline_passed]
    infra_reason = run_infra_reason(run_error, backend, delta, snapshot_error)
    infra = infra_reason is not None
    if infra:
        evaluation = {"status": "infra_error", "solved": False}
    else:
        evaluation = await ImageGrader().evaluate(
            image=image,
            delta=delta,
            test_overlay=spec.test_overlay,
            f2p=f2p,
            p2p=list(spec.baseline_passed),
            django=task.get("family") == "django",
            sympy=False,
            timeout=float(task["grader_timeout_seconds"]),
        )
        infra_reason = grader_infra_reason(evaluation)
        infra = infra_reason is not None
    # Drop nothing the grader needs, but do not copy this file into the workspace.
    (output / "grader-result.json").write_text(
        json.dumps(evaluation, indent=2, default=str), encoding="utf-8"
    )
    events = []
    events_path = output / "events.jsonl"
    if events_path.exists():
        events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    result_plain = plain(result) if result else {}
    attempts = result_plain.get("llm_attempts") or []
    stats = summarize_events(events, result_plain, run_error, output / "session" / "artifacts")
    tool_errors = harness_tool_errors(events, tool_schemas())
    changed = delta_changes(delta) or list(evaluation.get("changed_paths") or [])
    lost = False if infra else delta_lost(stats, delta)
    if lost:
        print(f"DELTA_LOST {task['id']}: edit tools ran but the collected delta is empty. snapshot_error={snapshot_error}", flush=True)
    model_calls = len(attempts) if attempts else max(0, ledger.calls_started - calls_before)
    model = dict(requested_model)
    final_model = model_identity(agent.llm)
    if final_model != requested_model:
        model["changed_to"] = final_model
    missing = missing_count(evaluation)
    timed_out = not infra and not missing and timed_out_with_reason(evaluation)
    verdict = None
    regrade_retries = []
    if (missing or timed_out) and not infra:
        regrade = await empty_regrade(task, image, spec, f2p)
        (output / "grader-regrade-empty.json").write_text(json.dumps(regrade, indent=2, default=str), encoding="utf-8")
        verdict = regrade_verdict(regrade, expected_empty(calibration_row(task["id"])))
        regrade_retries = regrade.get("docker_retries") or []
    candidate_error = None
    if missing and not infra:
        candidate_error = classify_missing(evaluation, delta_changes(delta), verdict)
    elif timed_out:
        candidate_error = classify_timeout(evaluation, verdict)
    timeout_failure = candidate_error == "execution_timeout"
    summary = {
        "task_id": task["id"],
        "variant": variant,
        "mock": mock,
        "profile": profile.stem,
        "model": model,
        **fingerprints,
        **({"retry": dict(retry)} if retry is not None else {}),
        "solved": False if lost or infra or missing or timed_out else bool(evaluation.get("solved")),
        "status": "infra_error" if infra else ("delta_lost" if lost else ("fail" if timeout_failure else evaluation.get("status"))),
        "grader_status": evaluation.get("status"),
        "grader_ids": {
            "f2p": len(f2p),
            "p2p": len(spec.baseline_passed),
            "missing": missing,
            "reason": evaluation.get("reason"),
        },
        "grader_candidate_error": candidate_error,
        "missing_regrade": verdict if missing else None,
        "timeout_regrade": verdict if timed_out else None,
        "changed_files": changed,
        "first_edit_step": stats["first_edit_step"],
        "model_calls": model_calls,
        "tool_calls": stats["tool_calls"],
        "harness_tool_errors": len(tool_errors),
        "harness_tool_error_details": tool_errors[:20],
        "peak_context": _peak_context(result_plain, events),
        "cost_cny": str(ledger.cost_committed - before),
        "stop_reason": stats["stop_reason"],
        "run_error": run_error,
        "infra_reason": infra_reason,
        "docker_retries": [dict(stage="run", **row) for row in backend.docker_retries]
                          + [dict(stage="grader", **row) for row in evaluation.get("docker_retries") or []]
                          + [dict(stage="regrade", **row) for row in regrade_retries],
        "snapshot_error": snapshot_error,
        "export_skipped_files": getattr(backend, "export_skipped_files", None),
        "answer_isolation_pass": True if clean else None,
        "clean_image_digest": image if clean else None,
        "memory": memory,
        "git": {
            "coding-harness": git_state(ROOT),
            "coding-agent": git_state(ROOT.parent / "coding-agent"),
        },
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def model_identity(llm) -> dict:
    """Requested model name and base_url host. The key is never read here."""
    base_url = getattr(llm, "base_url", None) or ""
    return {"name": getattr(llm, "model", None), "base_url_host": urlparse(base_url).hostname if base_url else None}


def _isolation_stop(task: dict, variant: str, reason: str, *, mock: bool, profile: Path, output: Path) -> dict:
    summary = {
        "task_id": task["id"],
        "variant": variant,
        "mock": mock,
        "profile": profile.stem,
        "solved": False,
        "status": "answer_isolation_failed",
        "model_calls": 0,
        "cost_cny": "0.000",
        "answer_isolation_pass": False,
        "stop_reason": reason,
    }
    print("ANSWER_ISOLATION_FAILED " + task["id"] + " " + reason, flush=True)
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def _peak_context(result_plain: dict, events: list[dict]):
    stats = list(result_plain.get("context_events") or [])
    if not stats:
        stats = [((event.get("payload") or {}).get("stats") or {}) for event in events if event.get("event_type") == "context.prepared"]
    if not stats:
        return None
    peak = max(stats, key=lambda item: item.get("admission_input_tokens") or 0)
    return {
        "admission_input_tokens": peak.get("admission_input_tokens"),
        "step": peak.get("step"),
    }


def _score_cases(evaluation: dict, spec: RepositorySpec) -> dict:
    f2p = [node for node in spec.expected_nodeids if node not in spec.baseline_passed]
    by_id = {case.get("nodeid"): case for case in evaluation.get("cases") or []}
    return {
        "f2p_passed": sum(1 for node in f2p if (by_id.get(node) or {}).get("outcome") == "passed"),
        "f2p_total": len(f2p),
        "p2p_passed": int(evaluation.get("regression_passed") or 0),
        "p2p_total": len(spec.baseline_passed),
    }


def _memory_task(source_id: str, manifest: dict) -> dict | None:
    for task in manifest["tasks"]:
        if task["id"] == source_id:
            return task
    roots = [
        ROOT / "docs/upgrade/evidence/A5-harness-preflight-62-history-baseline-retry/tasks",
        ROOT / "docs/upgrade/evidence/A5-harness-preflight-57-remaining16-baseline/tasks",
    ]
    receipt = next((root / source_id / "receipt.json" for root in roots if (root / source_id / "receipt.json").exists()), None)
    if receipt is None:
        return None
    public = {}
    for path in (ROOT / "docs/upgrade/evidence").glob("A5-memory-pool-*/**/public-input.json"):
        data = load_json(path)
        if data.get("source_id") == source_id:
            public = data
            break
    repo = load_json(receipt).get("repo", "")
    family = repo.split("/")[0]
    base = receipt.parent
    return {
        "id": source_id,
        "repo": repo,
        "family": family,
        "receipt": str(receipt.relative_to(ROOT)).replace("\\", "/"),
        "original": str((base / "staging/original").relative_to(ROOT)).replace("\\", "/"),
        "baseline": str((base / "staging/baseline").relative_to(ROOT)).replace("\\", "/"),
        "public_task": public.get("public_task_path", ""),
        "transport_manifest": public.get("typed_tree_manifest_ref", ""),
    }


async def verify_memory(manifest: dict, destination: Path) -> list[dict]:
    """Score memory sources with ImageGrader. Uncalibrated baselines are not scored."""
    from scripts.sprint2_calibrate import candidates, spec_ids
    from scripts.sprint2_verify_memory import as_delta

    snapshot = ROOT / "docs/upgrade/evidence/A5-real240-77-harness-preflight/memory-snapshot.jsonl"
    calibrated = {}
    results_path = ROOT / "runs/sprint2-calibrate/results.jsonl"
    if results_path.is_file():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                calibrated[row["id"]] = row
    trees = candidates()
    rows = []
    for line in snapshot.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        source_id = record["source_id"]
        task_dir = trees.get(source_id)
        calibration = calibrated.get(source_id)
        if task_dir is None or calibration is None or not calibration.get("accepted"):
            reason = "gold_fails_calibration" if calibration and not calibration.get("accepted") else "no_calibrated_baseline"
            rows.append({"source_id": source_id, "status": "grader_unavailable", "reason": reason, "f2p_passed": 0, "f2p_total": 0, "p2p_passed": 0, "p2p_total": 0, "calibrated": False})
            print(json.dumps(rows[-1]), flush=True)
            continue
        delta_path = Path(record["source_dir"]) / "candidate-delta.json"
        receipt, family, f2p, p2p, overlay = spec_ids(task_dir)
        p2p = [node for node in p2p if node not in set(calibration.get("p2p_excluded") or [])]
        if not delta_path.is_file():
            rows.append({"source_id": source_id, "status": "grader_unavailable", "reason": "candidate delta missing", "f2p_passed": 0, "f2p_total": len(f2p), "p2p_passed": 0, "p2p_total": len(p2p), "calibrated": True})
            print(json.dumps(rows[-1]), flush=True)
            continue
        result = await ImageGrader().evaluate(
            image=receipt["baseline_image_digest"],
            delta=as_delta(json.loads(delta_path.read_text(encoding="utf-8"))),
            test_overlay=overlay, f2p=f2p, p2p=p2p, django=family == "django", sympy=False, timeout=600,
        )
        if result.get("reason") and not result.get("f2p_total"):
            label = "grader_unavailable"
        elif result.get("solved"):
            label = "pass"
        elif result.get("f2p_passed"):
            label = "partial"
        else:
            label = "fail"
        rows.append({
            "source_id": source_id, "status": label,
            "f2p_passed": result.get("f2p_passed") or 0, "f2p_total": result.get("f2p_total") or 0,
            "p2p_passed": result.get("p2p_passed") or 0, "p2p_total": result.get("p2p_total") or 0,
            "reason": result.get("reason"), "calibrated": True,
        })
        print(json.dumps(rows[-1]), flush=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", default="configs/sprint/dev-tasks.yaml")
    parser.add_argument("--task", action="append", default=[])
    parser.add_argument("--profile", default="configs/sprint/b0.yaml")
    parser.add_argument("--variant", default="B0", choices=["B0", "B1", "B2", "B3"])
    parser.add_argument("--cards", default=None)
    parser.add_argument("--output", default="runs/sprint")
    parser.add_argument("--mock", action="store_true", help="zero-model finish, still grades in Docker")
    parser.add_argument("--legacy-image", action="store_true", help="mock-only: use the old baseline image")
    parser.add_argument("--verify-memory", action="store_true")
    args = parser.parse_args()
    if args.variant != "B0" and not args.cards:
        raise SystemExit("B1/B2/B3 require --cards")
    manifest = load_yaml(ROOT / args.tasks)
    if args.verify_memory:
        asyncio.run(verify_memory(manifest, ROOT / "docs/sprint/memory-source-verification.json"))
        return
    selected = select_tasks(manifest, args.task)
    out_root = Path(args.output)
    if not out_root.is_absolute():
        out_root = ROOT / out_root

    async def _go():
        summaries = []
        for task in selected:
            cards = Path(args.cards) if args.cards else None
            if cards is not None and not cards.is_absolute():
                cards = ROOT / cards
            summaries.append(await run_task(
                task, ROOT / args.profile, out_root / task["id"],
                mock=args.mock, variant=args.variant, cards_path=cards,
                legacy_image=args.legacy_image,
            ))
        print(json.dumps(summaries, indent=2))

    asyncio.run(_go())


if __name__ == "__main__":
    main()
