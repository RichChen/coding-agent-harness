"""CR-2: run the N / P / T / O groups on the 56-task A5/A6 pool (docs/retrieval/CR-2-FREEZE.md).

N is b0-v2 unchanged. P appends the frozen prefetch text (docs/retrieval/cr2-prefetch.json) to the first user
message. T uses configs/retrieval/cr2-t.yaml, which adds the read-only retrieve_code tool. O appends the frozen
oracle list (docs/retrieval/cr2-oracle-prefetch.json, written by cr2_oracle_prefetch.py) in the P format; this
runner reads only that frozen file, never the labels. Slots runs/cr2-main/<task>/<group>/r<k> share one queue,
shuffled with seed 20261026 after a sort on (task, group, k). The structure follows run_a6_retry.py; its helpers
and run_a5_main's are imported, not copied or changed.

    run_cr2.py --freeze-prefetch            render the P text for the 56 tasks (no model calls)
    run_cr2.py --preflight                  every pre-run check (no model calls)
    run_cr2.py --mock --mock-slots          mock model, two tasks per group, under runs/cr2-mock-r1
    run_cr2.py --canary --authorized 5,800  CR-2b: the first two slots of each group with the real model
    run_cr2.py --authorized 5,800           CR-2c: every slot
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import re
import statistics
import subprocess
import sys
import zlib
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.handoff_sha import handoff_sha256  # noqa: E402
from scripts.run_a5_main import (  # noqa: E402
    PARALLEL,
    PROFILE,
    _display_path,
    accepted_tasks,
    load_targets,
    materialize_runner_task,
    retire_infra,
    run_checks,
    spent,
)
from scripts.run_a6_retry import first_user_message  # noqa: E402
from scripts.run_swe_task import build_prompt, issue_text, load_json, run_task  # noqa: E402
from src.llm.client import LLMClient, LLMResponse, LLMStreamEvent, StreamCapability, ToolCall  # noqa: E402

SEED = 20261026
GROUPS = ("n", "p", "t", "o")
REPEATS = (1, 2, 3, 4, 5)
TOTAL_CAP = Decimal("800")
EXPECTED_TASKS = 56
EXPECTED_SLOTS = EXPECTED_TASKS * len(GROUPS) * len(REPEATS)
T_PROFILE = ROOT / "configs" / "retrieval" / "cr2-t.yaml"
PROFILES = {"n": PROFILE, "p": PROFILE, "t": T_PROFILE, "o": PROFILE}
VARIANTS = {"n": "CR2-N", "p": "CR2-P", "t": "CR2-T", "o": "CR2-O"}
SUFFIX_GROUPS = ("p", "o")
PREFETCH = ROOT / "docs" / "retrieval" / "cr2-prefetch.json"
PREFETCH_SCHEMA = "cr2.prefetch.v1"
ORACLE = ROOT / "docs" / "retrieval" / "cr2-oracle-prefetch.json"
ORACLE_SCHEMA = "cr2.oracle-prefetch.v1"
RUNS_SCHEMA = "cr2.runs.v1"
CR1_TEST_RANKINGS = ROOT / "runs" / "cr" / "test-frozen-rankings.jsonl"
LOGS = ROOT / "runs" / "cr2-logs"
TOOLS_FILE = "agent-tools.json"
CALLS_FILE = "retrieval-calls.jsonl"
MODELS_FILE = "model-names.json"
# Contract 8.2: if none of the first this-many real slots of a run recorded a model name, stop starting slots.
MODEL_CHECK_SLOTS = 3
# Contract 9.2: snapshot errors that are task results (see agent_snapshot_failure).
AGENT_SNAPSHOT_ERRORS = frozenset({"ValueError: delta_too_large",
                                   "DockerUnavailable: Docker control output quota exceeded"})
DELTA_STATUS = "delta_too_large"
LOCK_FILE = "run_cr2.lock"
# Settlement error kinds that count as provider unavailability (HTTP such as 402, network) and rerun the slot.
PROVIDER_FAILURE_KINDS = frozenset({"http", "network"})


def out_root_for(mock: bool) -> Path:
    return ROOT / "runs" / ("cr2-mock-r1" if mock else "cr2-main")


def runs_path_for(mock: bool) -> Path:
    return ROOT / "docs" / "retrieval" / ("cr2-runs.mock-r1.json" if mock else "cr2-runs.json")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _git_head() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()


# --- pool and frozen prefetch --------------------------------------------------------------------

def pool_tasks() -> list[dict]:
    tasks = sorted(accepted_tasks(load_targets()), key=lambda t: t["id"])
    if len(tasks) != EXPECTED_TASKS:
        raise SystemExit(f"pool has {len(tasks)} tasks, expected {EXPECTED_TASKS}")
    return tasks


def agent_issue(prepared: dict) -> str:
    """The issue text the agent sees; it is also the retrieval query."""
    return issue_text(load_json(Path(prepared["public_task"])))


def _cr1_hybrid_top(ids: set[str], n: int) -> dict[str, list[str]]:
    out = {}
    if not CR1_TEST_RANKINGS.is_file():
        return out
    with open(CR1_TEST_RANKINGS, encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            if row["instance_id"] in ids:
                out[row["instance_id"]] = row["rankings"]["hybrid"]["files"][:n]
    return out


def freeze_prefetch(tasks: list[dict], service, destination: Path = PREFETCH) -> dict:
    """Render the P text of every task with the frozen hybrid on the agent-visible issue. Refuses to overwrite."""
    from src.retrieval import serve

    if destination.exists():
        raise SystemExit(_display_path(destination) + " exists; the prefetch text is frozen once")
    cr1 = _cr1_hybrid_top({t["id"] for t in tasks}, serve.PREFETCH_FILES)
    rows = []
    for task in tasks:
        prepared = materialize_runner_task(task)
        issue = agent_issue(prepared)
        index = service.task_index(task["repo"], task["base_commit"])
        hits, source = index.search(issue, serve.PREFETCH_FILES)
        text = serve.render_prefetch(hits)
        row = {"task": task["id"], "repo": task["repo"], "base_commit": task["base_commit"],
               "issue_sha256": _sha(issue), "query_vector": source, "files": serve.hits_record(hits),
               "text": text, "sha256": _sha(text), "bytes": len(text.encode("utf-8"))}
        if task["id"] in cr1:
            row["cr1_test_top5_match"] = [h.path for h in hits] == cr1[task["id"]]
        rows.append(row)
        print(task["id"], row["bytes"], source, row.get("cr1_test_top5_match"), flush=True)
    sizes = [row["bytes"] for row in rows]
    manifest = {
        "schema": PREFETCH_SCHEMA,
        "code_commit": _git_head(),
        "cr1_freeze_sha256": handoff_sha256(serve.FREEZE_PATH),
        "frozen": serve.FROZEN,
        "files_per_task": serve.PREFETCH_FILES,
        "chunks_per_file": serve.CHUNKS_PER_FILE,
        "max_bytes": serve.PREFETCH_MAX_BYTES,
        "header": serve.PREFETCH_HEADER,
        "footer": serve.PREFETCH_FOOTER,
        "bytes": {"min": min(sizes), "median": statistics.median(sizes), "max": max(sizes)},
        "cr1_test_top5_match": sum(1 for row in rows if row.get("cr1_test_top5_match")),
        "tasks": rows,
    }
    destination.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return manifest


def load_prefetch(path: Path = PREFETCH, task_ids: set[str] | None = None,
                  schema: str = PREFETCH_SCHEMA) -> tuple[dict, str]:
    """task id -> row, and the handoff sha256 of the file. Every row is checked against its sha256.
    The P and O manifests share this layout and the P header and footer."""
    from src.retrieval import serve

    if not path.is_file():
        raise SystemExit("prefetch manifest missing: " + _display_path(path))
    manifest = json.loads(path.read_text(encoding="utf-8"))
    rows = {row["task"]: row for row in manifest.get("tasks") or []}
    framed = serve.PREFETCH_HEADER + "\n\n"
    problems = [name for name, bad in (
        ("schema", manifest.get("schema") != schema),
        ("row frame", any(not row["text"].startswith(framed) or not row["text"].endswith("\n\n" + serve.PREFETCH_FOOTER)
                          for row in rows.values())),
        ("header", manifest.get("header") != serve.PREFETCH_HEADER or manifest.get("footer") != serve.PREFETCH_FOOTER),
        ("task set", task_ids is not None and set(rows) != task_ids),
        ("row sha256", any(_sha(row["text"]) != row["sha256"] for row in rows.values())),
        ("row bytes", any(len(row["text"].encode("utf-8")) != row["bytes"] or row["bytes"] > serve.PREFETCH_MAX_BYTES
                          for row in rows.values())),
    ) if bad]
    if problems:
        raise SystemExit("prefetch manifest " + _display_path(path) + " fails: " + ", ".join(problems))
    return rows, handoff_sha256(path)


def expected_prompts(prepared: dict, prefetch: dict, oracle: dict) -> dict:
    """(task, group) -> the exact first user message, its sha256 and the prompt suffix."""
    expected = {}
    for task_id, task in prepared.items():
        base = build_prompt(task)
        for group in GROUPS:
            expected[(task_id, group)] = {"suffix": None, "text": base, "sha256": _sha(base)}
        for group, rows in (("p", prefetch), ("o", oracle)):
            suffix = rows[task_id]["text"]
            text = build_prompt(task, suffix)
            if text != base + "\n\n" + suffix:
                raise SystemExit(group.upper() + " prompt for " + task_id + " is not the B0 prompt plus the frozen text")
            expected[(task_id, group)] = {"suffix": suffix, "text": text, "sha256": _sha(text)}
    return expected


def expected_tools() -> dict[str, list[str]]:
    """Tool names each group's agent must send. T needs a retrieval binding only to build; it is never called."""
    import tempfile

    from src.harness.factory import build_agent, load_config
    from src.tools.retrieve import TaskRetrieval

    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        for group in GROUPS:
            config = load_config(PROFILES[group])
            config["llm"]["api_key"] = "names-only"
            retrieval = TaskRetrieval(None, Path(tmp) / CALLS_FILE) if group == "t" else None
            agent = build_agent(config, Path(tmp), retrieval=retrieval)
            out[group] = [s["function"]["name"] for s in agent.tools.schemas(agent.enabled_tools)]
    plain = [out[group] for group in GROUPS if group != "t"]
    if "retrieve_code" in out["n"] or any(names != out["n"] for names in plain) or out["t"] != out["n"] + ["retrieve_code"]:
        raise SystemExit("tool lists are not N = P = O = b0-v2 and T = b0-v2 + retrieve_code: " + json.dumps(out))
    return out


_MODEL_FIELD = re.compile(rb'"(model|system_fingerprint)"\s*:\s*"([^"\\]{1,200})"')


class _ModelTapStream(httpx.AsyncByteStream):
    """Pass a response body through unchanged while noting every "model" and "system_fingerprint" value in it;
    record(value, field) is called for each.

    The stream holds the bytes as sent, before httpx removes the Content-Encoding. For gzip or deflate, a copy is
    decompressed with its own zlib object only for scanning; other encodings are passed through unscanned."""

    def __init__(self, stream, record, encoding: str = ""):
        self._stream = stream
        self._record = record
        self._tail = b""
        encoding = encoding.strip().lower()
        self._plain = encoding in ("", "identity")
        # MAX_WBITS | 32 accepts both the zlib and the gzip header.
        self._decoder = zlib.decompressobj(zlib.MAX_WBITS | 32) if encoding in ("gzip", "deflate") else None
        self._raw_deflate_fallback = encoding == "deflate"  # some servers send deflate without the zlib header
        self._undecoded = b""

    def _decode(self, chunk: bytes) -> bytes:
        if self._plain:
            return chunk
        if self._decoder is None:
            return b""
        try:
            out = self._decoder.decompress(chunk)
        except zlib.error:
            if not self._raw_deflate_fallback:
                raise
            self._raw_deflate_fallback = False
            self._decoder = zlib.decompressobj(-zlib.MAX_WBITS)
            out = self._decoder.decompress(self._undecoded + chunk)
        if self._raw_deflate_fallback:
            self._undecoded = b"" if out else self._undecoded + chunk
            self._raw_deflate_fallback = not out
        return out

    def _scan(self, data: bytes) -> None:
        data = self._tail + data
        for match in _MODEL_FIELD.finditer(data):
            self._record(match.group(2).decode("utf-8", "replace"), match.group(1).decode("ascii"))
        self._tail = data[-256:]

    async def __aiter__(self):
        async for chunk in self._stream:
            try:
                self._scan(self._decode(chunk))
            except Exception:  # noqa: BLE001 - observation must never break the response
                self._plain, self._decoder = False, None
            yield chunk
        try:
            if self._decoder is not None:
                self._scan(self._decoder.flush())
        except Exception:  # noqa: BLE001
            pass

    async def aclose(self):
        await self._stream.aclose()


def model_name_tap(llm, record) -> None:
    """Record the model name the provider returns (LLMResponse does not keep it).

    Wraps the client's own _ensure_client so the httpx client is built exactly as before, then adds a
    response hook that only observes the body bytes. Request payloads and parsed responses are unchanged."""
    original = llm._ensure_client

    async def hook(response):
        try:
            if response.request.url.path.endswith("/chat/completions"):
                response.stream = _ModelTapStream(response.stream, record,
                                                  response.headers.get("content-encoding", ""))
        except Exception:  # noqa: BLE001
            pass

    async def ensure_client():
        client = await original()
        hooks = client.event_hooks
        if hook not in hooks["response"]:
            client.event_hooks = {"request": list(hooks["request"]), "response": [*hooks["response"], hook]}
        return client

    llm._ensure_client = ensure_client


def model_recorder(output: Path):
    """record(value, field) for the tap: model-names.json holds the distinct model names and system_fingerprints."""
    seen: dict[str, set[str]] = {"model": set(), "system_fingerprint": set()}

    def record(value: str, field: str = "model") -> None:
        if field in seen and value not in seen[field]:
            seen[field].add(value)
            payload = {"models": sorted(seen["model"]), "system_fingerprints": sorted(seen["system_fingerprint"])}
            (output / MODELS_FILE).write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return record


def tool_recorder(output: Path, *, tap_model: bool = False):
    """on_agent hook for run_task: write the tool schemas the agent will send, before the first model call.
    With tap_model, also record the model names the provider returns into model-names.json."""
    def record(agent) -> None:
        schemas = agent.tools.schemas(agent.enabled_tools)
        payload = {"tools": [s["function"]["name"] for s in schemas],
                   "schemas_sha256": _sha(json.dumps(schemas, ensure_ascii=False))}
        (output / TOOLS_FILE).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        if tap_model:
            model_name_tap(agent.llm, model_recorder(output))
    return record


def _recorded(run_dir: Path, key: str) -> list[str]:
    """Values recorded under key in model-names.json. Before r3 the file was a plain list of model names.
    A missing, empty or unreadable file counts as none."""
    try:
        data = json.loads((run_dir / MODELS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(data, list):
        values = data if key == "models" else []
    elif isinstance(data, dict):
        values = data.get(key)
    else:
        values = None
    return [value for value in values if isinstance(value, str) and value] if isinstance(values, list) else []


def returned_models(run_dir: Path) -> list[str]:
    return _recorded(run_dir, "models")


def returned_fingerprints(run_dir: Path) -> list[str]:
    return _recorded(run_dir, "system_fingerprints")


def model_name_halt(run_dirs) -> str | None:
    """Contract 8.2: the recording is broken if none of the first MODEL_CHECK_SLOTS real slots has a model name."""
    run_dirs = list(run_dirs)
    if len(run_dirs) < MODEL_CHECK_SLOTS or any(returned_models(path) for path in run_dirs[:MODEL_CHECK_SLOTS]):
        return None
    return ("no returned model name recorded in the first " + str(MODEL_CHECK_SLOTS) + " real slots: "
            + ", ".join(_display_path(path) for path in run_dirs[:MODEL_CHECK_SLOTS]))


def _slots_by_value(out_root: Path, slots, read) -> tuple[dict[str, list[str]], list[str]]:
    by_value: dict[str, list[str]] = {}
    missing = []
    for slot in slots:
        values = read(slot_dir(out_root, slot))
        if not values:
            missing.append(slot_label(slot))
        for value in values:
            by_value.setdefault(value, []).append(slot_label(slot))
    return {value: sorted(labels) for value, labels in sorted(by_value.items())}, sorted(missing)


def model_slots(out_root: Path, slots) -> dict:
    """Returned model name -> slot labels, and the slots without one (contract 8.2: list them, do not stop)."""
    by_model, missing = _slots_by_value(out_root, slots, returned_models)
    return {"by_model": by_model, "without_model_name": missing}


def fingerprint_slots(out_root: Path, slots) -> dict:
    """Returned system_fingerprint -> slot labels (contract 9.2: reported only, never a stop condition)."""
    by_value, missing = _slots_by_value(out_root, slots, returned_fingerprints)
    return {"by_fingerprint": by_value, "without_fingerprint": missing}


# --- slots ---------------------------------------------------------------------------------------

def all_slots(task_ids) -> list[tuple[str, str, int]]:
    return sorted((task, group, k) for task in task_ids for group in GROUPS for k in REPEATS)


def slot_queue(task_ids) -> list[tuple[str, str, int]]:
    slots = all_slots(task_ids)
    random.Random(SEED).shuffle(slots)
    return slots


def canary_slots(queue) -> list[tuple[str, str, int]]:
    """The first two slots of each group, in queue order."""
    seen = {group: 0 for group in GROUPS}
    chosen = []
    for slot in queue:
        if seen[slot[1]] < 2:
            seen[slot[1]] += 1
            chosen.append(slot)
    return chosen


def mock_slots(queue) -> list[tuple[str, str, int]]:
    """Two slots of each group in queue order, each on a task not taken yet."""
    seen = {group: 0 for group in GROUPS}
    tasks, chosen = set(), []
    for slot in queue:
        if seen[slot[1]] < 2 and slot[0] not in tasks:
            seen[slot[1]] += 1
            tasks.add(slot[0])
            chosen.append(slot)
    return chosen


def parse_slot(text: str) -> tuple[str, str, int]:
    parts = text.split("/")
    if len(parts) != 3 or parts[1] not in GROUPS or not parts[2].startswith("r") or not parts[2][1:].isdigit():
        raise SystemExit("slot must look like <task>/<n|p|t>/r<k>: " + text)
    return parts[0], parts[1], int(parts[2][1:])


def slot_dir(out_root: Path, slot) -> Path:
    task, group, k = slot
    return out_root / task / group / ("r" + str(k))


def slot_label(slot) -> str:
    return "/".join([slot[0], slot[1], "r" + str(slot[2])])


# --- run records and stop conditions -------------------------------------------------------------

def resumable(summary: dict, group: str, *, mock: bool) -> str | None:
    """Why an existing summary cannot count for this slot, or None."""
    if "mock" not in summary or "profile" not in summary:
        return "summary has no mock/profile field"
    if bool(summary["mock"]) != mock:
        return "summary is a mock run" if summary["mock"] else "summary is a real run"
    if summary["profile"] != PROFILES[group].stem:
        return "profile is " + str(summary["profile"])
    if summary.get("variant") != VARIANTS[group]:
        return "variant is " + str(summary.get("variant"))
    return None


def _events(run_dir: Path) -> list[dict]:
    path = run_dir / "events.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def provider_failure(run_dir: Path) -> str | None:
    """'<kind> <status>' for the first model call of the run settled as an HTTP or network failure, else None.

    Same selection as the A6 402 rerun: any such settlement in the run, wherever it happened."""
    for event in _events(run_dir):
        if event.get("event_type") != "model.settled":
            continue
        record = (event.get("payload") or {}).get("record") or {}
        if record.get("success") is False and record.get("error_kind") in PROVIDER_FAILURE_KINDS:
            return f"{record['error_kind']} {record.get('status_code')}"
    return None


def agent_snapshot_failure(summary: dict) -> bool:
    """Contract 9.2: the candidate snapshot failed because the workspace delta was too large, which the agent's own
    commands cause (9.1 replay: a repository test regenerated Cython sources). A task result, not infra.

    snapshot_error is only set by export_delta() inside close(snapshot=True), so the quota error here is the
    snapshot step's. The infra reason must be that snapshot error, not an earlier failure."""
    error = summary.get("snapshot_error")
    return (summary.get("status") == "infra_error" and error in AGENT_SNAPSHOT_ERRORS
            and summary.get("infra_reason") == "candidate snapshot failed: " + error)


def counted_status(summary: dict) -> str | None:
    return DELTA_STATUS if agent_snapshot_failure(summary) else summary.get("status")


def needs_rerun(run_dir: Path, summary: dict) -> str | None:
    """Infra rule (CR-2 contract section 3): chosen by error type only, never by solved."""
    if counted_status(summary) == "infra_error":
        return "infra_error"
    failure = provider_failure(run_dir)
    return "provider " + failure if failure else None


def slot_summaries(out_root: Path) -> list[tuple[Path, dict]]:
    """Every attempt in the root, including retired r<k>.infra<n> runs."""
    if not out_root.exists():
        return []
    rows = []
    for group in GROUPS:
        for path in sorted(out_root.glob("*/" + group + "/r*/summary.json")):
            rows.append((path, json.loads(path.read_text(encoding="utf-8"))))
    return rows


def _calls(run_dir: Path) -> list[dict]:
    path = run_dir / CALLS_FILE
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def injection_problem(run_dir: Path, summary: dict, group: str, want: dict, tools: dict) -> str | None:
    if summary.get("prompt_sha256") != want["sha256"]:
        return "prompt_sha256 differs from the expected prompt"
    message = first_user_message(run_dir)
    if message is None:
        return "first user message cannot be read from events.jsonl"
    if message != want["text"]:
        return "first user message differs from the expected prompt"
    record = run_dir / TOOLS_FILE
    if not record.is_file():
        return TOOLS_FILE + " missing"
    if json.loads(record.read_text(encoding="utf-8")).get("tools") != tools[group]:
        return "tool schemas differ from the " + group.upper() + " list"
    if group == "t":
        crashed = [row.get("call_id") for row in _calls(run_dir) if row.get("traceback")]
        if crashed:
            return "retrieve_code crashed on " + ", ".join(map(str, crashed))
    elif (run_dir / CALLS_FILE).exists():
        return CALLS_FILE + " exists in a run without retrieve_code"
    return None


def halt_reason(out_root: Path, expected: dict, tools: dict, verified: set | None = None,
                cap: Decimal = TOTAL_CAP) -> str | None:
    """Stop conditions checked before every new run. In-flight runs still finish."""
    verified = set() if verified is None else verified
    rows = slot_summaries(out_root)
    reason = run_checks([{**summary, "status": counted_status(summary)} for _, summary in rows])
    if reason:
        return reason
    for path, summary in rows:
        run_dir = path.parent
        if "." in run_dir.name or run_dir in verified or needs_rerun(run_dir, summary):
            continue
        task, group = run_dir.parent.parent.name, run_dir.parent.name
        label = task + "/" + group + "/" + run_dir.name
        want = expected.get((task, group))
        if want is None:
            return "run outside the CR-2 pool: " + label
        problem = injection_problem(run_dir, summary, group, want, tools)
        if problem:
            return "injection check failed: " + label + ": " + problem
        verified.add(run_dir)
    identities = set()
    for _, summary in rows:
        model = summary.get("model") or {}
        if model.get("changed_to"):
            return "model changed during a run: " + str(summary.get("task_id"))
        if model:
            identities.add((model.get("name"), model.get("base_url_host")))
    if len(identities) > 1:
        return "model name or base_url host differs between runs: " + json.dumps(sorted(identities, key=str))
    cost = spent(out_root)
    if cost >= cap:
        return "spend " + str(cost) + " CNY reached the cap " + str(cap)
    return None


# --- mock model ----------------------------------------------------------------------------------

_LISTED = re.compile(r"^\s*1\. (\S+)$", re.M)
_DIR_FILE = re.compile(r"^\[F\] (\S+\.py)$", re.M)


class CR2MockLLM(LLMClient):
    """Zero-cost scripted model. It reacts to what it is given, so each group takes its own path:
    T calls retrieve_code, P and O read the first listed file, N lists the root and reads a listed file."""

    def __init__(self):
        self.model = "mock-model"
        self.supports_streaming = False
        self._owns_http_client = False
        self._http = None
        self._closed = False
        self.calls = 0

    @staticmethod
    def _response(text=None, call=None) -> LLMResponse:
        return LLMResponse(text=text, tool_calls=[call] if call else [], finish_reason="tool_calls" if call else "stop",
                           usage={"prompt_tokens": 0, "completion_tokens": 0, "provenance": "unknown"})

    async def chat(self, messages, tools=None):
        step = self.calls
        self.calls += 1
        names = {t["function"]["name"] for t in tools or []}
        user = next((m.get("content") or "" for m in messages if m.get("role") == "user"), "")
        # the loop appends a "remaining model calls" user message after the latest tool result
        last = next((m.get("content") or "" for m in reversed(messages) if m.get("role") == "tool"), "")
        if step == 0:
            if "retrieve_code" in names:
                issue = user.split("Public issue:\n", 1)[-1]
                return self._response(call=ToolCall(id="mock-0", name="retrieve_code", arguments={"query": issue[:500], "k": 5}))
            from src.retrieval.serve import PREFETCH_HEADER
            if PREFETCH_HEADER in user:
                found = _LISTED.search(user.split(PREFETCH_HEADER, 1)[1])
                return self._response(call=ToolCall(id="mock-0", name="read_file", arguments={"path": found.group(1), "limit": 20}))
            return self._response(call=ToolCall(id="mock-0", name="list_dir", arguments={"path": "."}))
        if step == 1 and isinstance(last, str):
            found = _LISTED.search(last) or _DIR_FILE.search(last)
            if found:
                return self._response(call=ToolCall(id="mock-1", name="read_file", arguments={"path": found.group(1), "limit": 20}))
        return self._response(text="mock finish without edits")

    async def stream_chat(self, messages, tools=None):
        response = await self.chat(messages, tools)
        yield LLMStreamEvent("llm_stream_done", {
            "finish_reason": response.finish_reason, "usage": response.usage, "has_tool_calls": response.has_tool_calls,
            "capability": StreamCapability.NON_STREAMING.value, "response": response,
        })

    async def aclose(self):
        self._closed = True


# --- running -------------------------------------------------------------------------------------

def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        # os.kill(pid, 0) would terminate the process on Windows; ask for its exit code instead.
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def acquire_lock(out_root: Path) -> Path:
    """Contract 9.2: one runner per output root. The lock file holds the owner's pid and start time."""
    out_root.mkdir(parents=True, exist_ok=True)
    path = out_root / LOCK_FILE
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            owner = json.loads(path.read_text(encoding="utf-8"))
            pid = int(owner["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            raise SystemExit("unreadable lock " + _display_path(path) + "; check for another runner, then delete it")
        if _pid_alive(pid):
            raise SystemExit("another run_cr2.py (pid " + str(pid) + ", started " + str(owner.get("started_at"))
                             + ") holds " + _display_path(path) + "; not starting")
        raise SystemExit("stale lock " + _display_path(path) + " (pid " + str(pid) + " is not running, started "
                         + str(owner.get("started_at")) + "); delete it by hand after checking, then rerun")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"pid": os.getpid(), "started_at": _now()}) + "\n")
    return path


async def run_slots(slots, *, mock: bool, out_root: Path, prepared: dict, expected: dict, tools: dict,
                    prefetch_sha256: str, label: str, service=None, run=run_task, oracle_sha256: str | None = None) -> dict:
    lock = acquire_lock(out_root)
    try:
        return await _run_slots(slots, mock=mock, out_root=out_root, prepared=prepared, expected=expected, tools=tools,
                                prefetch_sha256=prefetch_sha256, label=label, service=service, run=run,
                                oracle_sha256=oracle_sha256)
    finally:
        lock.unlink(missing_ok=True)


def delta_slots(out_root: Path, slots) -> dict[str, list[str]]:
    """Group -> slots whose final run is a delta_too_large task result (contract 9.2)."""
    out: dict[str, list[str]] = {group: [] for group in GROUPS}
    for slot in slots:
        path = slot_dir(out_root, slot) / "summary.json"
        if path.is_file() and agent_snapshot_failure(json.loads(path.read_text(encoding="utf-8"))):
            out[slot[1]].append(slot_label(slot))
    return {group: sorted(labels) for group, labels in out.items()}


async def _run_slots(slots, *, mock: bool, out_root: Path, prepared: dict, expected: dict, tools: dict,
                     prefetch_sha256: str, label: str, service, run, oracle_sha256: str | None) -> dict:
    from src.tools.retrieve import TaskRetrieval

    started_at = _now()
    verified = set()
    pending, refused, kept_infra = [], [], []
    for slot in slots:
        task, group, _ = slot
        output = slot_dir(out_root, slot)
        summary_path = output / "summary.json"
        if summary_path.is_file():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            problem = resumable(summary, group, mock=mock)
            if problem:
                refused.append(_display_path(summary_path) + ": " + problem)
                continue
            if not needs_rerun(output, summary):
                continue
            if not retire_infra(output):
                kept_infra.append(_display_path(output))
                continue
        elif output.exists():
            refused.append(_display_path(output) + ": directory has no summary.json (an interrupted run)")
            continue
        pending.append((slot, output))
    if refused:
        raise SystemExit("existing summaries do not belong to these slots; not overwriting:\n" + "\n".join(refused))
    if any(slot[1] == "t" for slot, _ in pending):
        if service is None:
            raise SystemExit("T slots need the retrieval service")
        await asyncio.to_thread(service.warm)
    state = {"halt": halt_reason(out_root, expected, tools, verified)}
    semaphore = asyncio.Semaphore(PARALLEL)
    finished_real: list[Path] = []

    def check_models(output: Path) -> None:
        if mock or len(finished_real) >= MODEL_CHECK_SLOTS:
            return
        finished_real.append(output)
        if len(finished_real) == MODEL_CHECK_SLOTS:
            state["halt"] = state["halt"] or model_name_halt(finished_real)

    async def one(slot, output):
        task, group, _ = slot
        want = expected[(task, group)]
        async with semaphore:
            while True:
                if state["halt"]:
                    return None
                retrieval = None
                try:
                    output.mkdir(parents=True, exist_ok=True)
                    if group == "t":
                        index = await asyncio.to_thread(service.task_index, prepared[task]["repo"], prepared[task]["base_commit"])
                        retrieval = TaskRetrieval(index, output / CALLS_FILE)
                    summary = await run(prepared[task], PROFILES[group], output, mock=mock, variant=VARIANTS[group],
                                        prompt_suffix=want["suffix"], retrieval=retrieval,
                                        mock_llm=CR2MockLLM() if mock else None,
                                        on_agent=tool_recorder(output, tap_model=not mock))
                except (Exception, SystemExit) as exc:
                    state["halt"] = state["halt"] or ("run crashed: " + slot_label(slot) + " "
                                                      + type(exc).__name__ + ": " + str(exc)[:300])
                    return None
                finally:
                    retrieval = None
                state["halt"] = state["halt"] or halt_reason(out_root, expected, tools, verified)
                why = needs_rerun(output, summary)
                if not why:
                    check_models(output)
                    return summary
                print("rerun " + slot_label(slot) + ": " + why, flush=True)
                if state["halt"] or not retire_infra(output):
                    kept_infra.append(_display_path(output))
                    check_models(output)
                    return summary

    results = await asyncio.gather(*(one(*item) for item in pending))
    retired = [path for group in GROUPS for path in out_root.glob("*/" + group + "/r*.infra*")]
    started = [(slot, row) for (slot, _), row in zip(pending, results) if row is not None]
    models = sorted({name for slot, _ in started for name in returned_models(slot_dir(out_root, slot))})
    report = {
        "label": label,
        "mock": mock,
        "started_at": started_at,
        "finished_at": _now(),
        "parallel": PARALLEL,
        "seed": SEED,
        "repeats": len(REPEATS),
        "total_cap_cny": str(TOTAL_CAP),
        "prefetch_sha256": prefetch_sha256,
        "oracle_sha256": oracle_sha256,
        "returned_models": models,
        "returned_model_slots": None if mock else model_slots(out_root, [slot for slot, _ in started]),
        "returned_fingerprint_slots": None if mock else fingerprint_slots(out_root, [slot for slot, _ in started]),
        "delta_too_large_by_group": delta_slots(out_root, slots),
        "slots": [slot_label(slot) for slot in slots],
        "halt_reason": state["halt"],
        "started": len(started),
        "not_started": len(results) - len(started),
        "skipped_finished": len(slots) - len(pending),
        "started_by_group": {group: sum(1 for slot, _ in started if slot[1] == group) for group in GROUPS},
        "injection_verified_runs": len(verified),
        "infra_kept": sorted(set(kept_infra)),
        "infra_retired": sorted(_display_path(path) for path in retired),
        "spent_cny": str(spent(out_root)),
    }
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / (label + "-report.json")).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def write_runs(*, task_ids, repo_of: dict, mock: bool, prefetch_sha256: str, out_root: Path | None = None,
               destination: Path | None = None, oracle_sha256: str | None = None) -> Path:
    """cr2-runs.json: one row per finished slot, the input of analyze_cr2.py."""
    out_root = out_root or out_root_for(mock)
    runs = []
    for slot in all_slots(task_ids):
        task, group, k = slot
        run_dir = slot_dir(out_root, slot)
        path = run_dir / "summary.json"
        if not path.is_file():
            continue
        summary = json.loads(path.read_text(encoding="utf-8"))
        problem = resumable(summary, group, mock=mock)
        if problem:
            raise SystemExit("refusing to hand off " + _display_path(path) + ": " + problem)
        no_candidate = agent_snapshot_failure(summary)
        runs.append({
            "task_id": task, "repo": repo_of[task], "group": group, "k": k,
            "run_dir": _display_path(run_dir), "mock": summary["mock"], "profile": summary["profile"],
            "status": counted_status(summary), "solved": False if no_candidate else summary.get("solved"),
            "no_candidate": no_candidate, "snapshot_error": summary.get("snapshot_error"),
            "model_calls": summary.get("model_calls"),
            "cost_cny": summary.get("cost_cny"), "stop_reason": summary.get("stop_reason"),
            "prompt_sha256": summary.get("prompt_sha256"), "rerun_reason": needs_rerun(run_dir, summary),
            "returned_models": returned_models(run_dir),
            "system_fingerprints": returned_fingerprints(run_dir),
        })
    payload = {"schema": RUNS_SCHEMA, "scope": "mock" if mock else "cr2", "seed": SEED, "repeats": len(REPEATS),
               "groups": list(GROUPS), "prefetch_sha256": prefetch_sha256, "oracle_sha256": oracle_sha256,
               "slots_expected": len(all_slots(task_ids)), "runs": runs}
    if mock:
        payload["note"] = "Zero-model mock runs. This is not CR-2 data."
    destination = destination or runs_path_for(mock)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    print("runs", _display_path(destination), len(runs), handoff_sha256(destination), flush=True)
    return destination


# --- preflight -----------------------------------------------------------------------------------

def preflight(tasks: list[dict], prefetch: dict, oracle: dict, destination: Path | None = None) -> dict:
    """Checks that need no model and no container: pool, issue and base_commit against Verified, prompts, tools."""
    from src.retrieval import dataset

    destination = destination or LOGS / "preflight.json"
    verified = {q.instance_id: q for q in dataset.load_query_views()}
    rows = []
    prepared = {}
    for task in tasks:
        row = {"task_id": task["id"]}
        try:
            prepared[task["id"]] = materialize_runner_task(task)
            q = verified[task["id"]]
            row["base_commit_matches_verified"] = q.base_commit == task["base_commit"]
            row["issue_matches_verified"] = agent_issue(prepared[task["id"]]) == q.problem_statement
            issue_sha = _sha(agent_issue(prepared[task["id"]]))
            row["prefetch_issue_sha256_matches"] = prefetch[task["id"]]["issue_sha256"] == issue_sha
            row["oracle_issue_sha256_matches"] = oracle[task["id"]]["issue_sha256"] == issue_sha
            ok = row["base_commit_matches_verified"] and row["prefetch_issue_sha256_matches"] and row["oracle_issue_sha256_matches"]
            row["failure"] = None if ok else "mismatch"
        except (Exception, SystemExit) as exc:
            row["failure"] = type(exc).__name__ + ": " + str(exc)[:300]
        rows.append(row)
    prompts = {"checked": 0, "failure": None}
    try:
        expected = expected_prompts(prepared, prefetch, oracle)
        prompts["checked"] = len(expected)
    except (Exception, SystemExit) as exc:
        prompts["failure"] = type(exc).__name__ + ": " + str(exc)[:300]
    try:
        tools = expected_tools()
        tools_failure = None
    except (Exception, SystemExit) as exc:
        tools, tools_failure = None, type(exc).__name__ + ": " + str(exc)[:300]
    queue = slot_queue([task["id"] for task in tasks])
    report = {
        "passed": sum(1 for row in rows if row["failure"] is None),
        "total": len(rows),
        "issue_differs_from_verified": sorted(row["task_id"] for row in rows if row.get("issue_matches_verified") is False),
        "prompts": prompts,
        "tools": tools,
        "tools_failure": tools_failure,
        "slots": len(queue),
        "canary": [slot_label(slot) for slot in canary_slots(queue)],
        "mock": [slot_label(slot) for slot in mock_slots(queue)],
        "model_calls": 0,
        "tasks": rows,
    }
    report["pass"] = (report["passed"] == report["total"] == EXPECTED_TASKS and prompts["failure"] is None
                      and prompts["checked"] == EXPECTED_TASKS * len(GROUPS) and tools_failure is None
                      and len(queue) == EXPECTED_SLOTS)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


# --- cli -----------------------------------------------------------------------------------------

def check_authorized(value: str | None) -> None:
    """Real runs need the repeat count and the cap the project author confirmed, given on the command line."""
    if value is None:
        raise SystemExit("real runs need --authorized R,CAP as confirmed by the project author (CR-2 contract section 3)")
    try:
        repeats, cap = value.split(",")
        ok = int(repeats) == len(REPEATS) and Decimal(cap) == TOTAL_CAP
    except ValueError:
        ok = False
    if not ok:
        raise SystemExit(f"--authorized {value} does not match R={len(REPEATS)}, cap={TOTAL_CAP} in this runner")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--freeze-prefetch", action="store_true", help="render docs/retrieval/cr2-prefetch.json")
    parser.add_argument("--preflight", action="store_true", help="pre-run checks, no model calls")
    parser.add_argument("--mock", action="store_true", help="mock model, output under runs/cr2-mock-r1")
    parser.add_argument("--mock-slots", action="store_true", help="with --mock: two slots per group, all on different tasks")
    parser.add_argument("--canary", action="store_true", help="the first two slots of each group in the queue")
    parser.add_argument("--slot", action="append", default=[], help="<task>/<n|p|t|o>/r<k>; repeatable")
    parser.add_argument("--runs-file", action="store_true", help="rewrite cr2-runs.json from existing summaries")
    parser.add_argument("--authorized", help="R,CAP confirmed by the project author; required without --mock")
    args = parser.parse_args()
    if args.mock_slots and not args.mock:
        raise SystemExit("--mock-slots is only allowed with --mock")
    if sum(bool(item) for item in (args.mock_slots, args.canary, args.slot)) > 1:
        raise SystemExit("choose one of --mock-slots, --canary, --slot")
    tasks = pool_tasks()
    task_ids = [task["id"] for task in tasks]
    repo_of = {task["id"]: task["repo"] for task in tasks}
    if args.freeze_prefetch:
        from src.retrieval.serve import RetrievalService

        manifest = freeze_prefetch(tasks, RetrievalService.from_freeze())
        print("prefetch", manifest["bytes"], "cr1 top5 match", manifest["cr1_test_top5_match"], "of", len(manifest["tasks"]))
        return
    prefetch, prefetch_sha = load_prefetch(task_ids=set(task_ids))
    oracle, oracle_sha = load_prefetch(ORACLE, task_ids=set(task_ids), schema=ORACLE_SCHEMA)
    if args.runs_file:
        write_runs(task_ids=task_ids, repo_of=repo_of, mock=args.mock, prefetch_sha256=prefetch_sha, oracle_sha256=oracle_sha)
        return
    if args.preflight:
        report = preflight(tasks, prefetch, oracle)
        print("preflight", report["passed"], "of", report["total"], "tasks; prompts", json.dumps(report["prompts"]),
              "slots", report["slots"], "pass", report["pass"], flush=True)
        if not report["pass"]:
            raise SystemExit("preflight failed")
        return
    if not args.mock:
        check_authorized(args.authorized)
    queue = slot_queue(task_ids)
    if args.mock_slots:
        slots, label = mock_slots(queue), "mock"
    elif args.canary:
        slots, label = canary_slots(queue), "canary"
    elif args.slot:
        known = set(queue)
        slots = [parse_slot(text) for text in args.slot]
        unknown = [slot for slot in slots if slot not in known]
        if unknown:
            raise SystemExit("not a CR-2 slot: " + ", ".join(slot_label(slot) for slot in unknown))
        label = "slots"
    else:
        slots, label = queue, "main"
    if args.mock:
        label = "mock" if label == "mock" else "mock-" + label
    prepared = {task["id"]: materialize_runner_task(task) for task in tasks}
    expected = expected_prompts(prepared, prefetch, oracle)
    tools = expected_tools()
    service = None
    if any(slot[1] == "t" for slot in slots):
        from src.retrieval.serve import RetrievalService

        service = RetrievalService.from_freeze()
    out_root = out_root_for(args.mock)
    report = asyncio.run(run_slots(slots, mock=args.mock, out_root=out_root, prepared=prepared, expected=expected,
                                   tools=tools, prefetch_sha256=prefetch_sha, label=label, service=service,
                                   oracle_sha256=oracle_sha))
    print(json.dumps({key: value for key, value in report.items() if key != "slots"}, indent=2), flush=True)
    if args.mock:
        write_runs(task_ids=task_ids, repo_of=repo_of, mock=True, prefetch_sha256=prefetch_sha, out_root=out_root,
                   oracle_sha256=oracle_sha)
    elif label == "main" and not report["halt_reason"]:
        write_runs(task_ids=task_ids, repo_of=repo_of, mock=False, prefetch_sha256=prefetch_sha, out_root=out_root,
                   oracle_sha256=oracle_sha)
    if report["halt_reason"]:
        raise SystemExit("stopped: " + report["halt_reason"])


if __name__ == "__main__":
    main()
