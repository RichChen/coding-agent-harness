"""CR-2 runner: slot queue, selection, resume, provider-failure reruns, injection check, mock model."""
import asyncio
import json
from decimal import Decimal
from pathlib import Path

import pytest

from scripts import run_cr2
from src.retrieval.serve import PREFETCH_FOOTER, PREFETCH_HEADER, TOOL_HEADER

IDS = [f"repo__repo-{i:03d}" for i in range(56)]


def test_queue_has_336_interleaved_slots_and_is_deterministic():
    queue = run_cr2.slot_queue(IDS)
    assert len(queue) == run_cr2.EXPECTED_SLOTS == 336 and len(set(queue)) == 336
    assert queue == run_cr2.slot_queue(list(reversed(IDS)))
    assert queue != run_cr2.all_slots(IDS)
    assert {group for _, group, _ in queue[:30]} == {"n", "p", "t"}
    assert {k for _, _, k in queue} == {1, 2}


def test_canary_and_mock_selection():
    queue = run_cr2.slot_queue(IDS)
    canary = run_cr2.canary_slots(queue)
    assert [g for _, g, _ in canary].count("n") == 2 and len(canary) == 6
    assert canary == sorted(canary, key=queue.index)
    mock = run_cr2.mock_slots(queue)
    assert len(mock) == 6 and len({task for task, _, _ in mock}) == 6
    assert sorted(g for _, g, _ in mock) == ["n", "n", "p", "p", "t", "t"]


def test_parse_slot_and_label():
    slot = run_cr2.parse_slot("django__django-1/t/r2")
    assert slot == ("django__django-1", "t", 2) and run_cr2.slot_label(slot) == "django__django-1/t/r2"
    for bad in ("x/q/r1", "x/n/1", "x/n"):
        with pytest.raises(SystemExit):
            run_cr2.parse_slot(bad)


def test_resumable_checks_mock_profile_variant():
    ok = {"mock": True, "profile": "cr2-t", "variant": "CR2-T"}
    assert run_cr2.resumable(ok, "t", mock=True) is None
    assert "real" in run_cr2.resumable({**ok, "mock": False}, "t", mock=True)
    assert "profile" in run_cr2.resumable(ok, "n", mock=True)
    assert "variant" in run_cr2.resumable({**ok, "profile": "b0-v2"}, "n", mock=True)
    assert run_cr2.resumable({"mock": True, "profile": "b0-v2", "variant": "CR2-P"}, "p", mock=True) is None


def test_check_authorized():
    run_cr2.check_authorized("2,200")
    for bad in (None, "3,200", "2,150", "two"):
        with pytest.raises(SystemExit):
            run_cr2.check_authorized(bad)


def _settled(success=True, kind=None, status=None):
    record = {"success": success}
    if kind:
        record.update(error_kind=kind, status_code=status)
    return {"event_type": "model.settled", "payload": {"record": record}}


def _write_events(run_dir: Path, events):
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")


def test_provider_failure_rule(tmp_path):
    cases = {
        "ok": ([_settled()], None),
        "402": ([_settled(), _settled(False, "http", 402)], "http 402"),
        "mid": ([_settled(False, "network"), _settled()], "network None"),
        "timeout": ([_settled(False, "timeout")], None),
        "parse": ([_settled(False, "parse")], None),
    }
    for name, (events, want) in cases.items():
        _write_events(tmp_path / name, events)
        assert run_cr2.provider_failure(tmp_path / name) == want, name
    assert run_cr2.needs_rerun(tmp_path / "ok", {"status": "infra_error"}) == "infra_error"
    assert run_cr2.needs_rerun(tmp_path / "402", {"status": "failed", "solved": False}) == "provider http 402"
    assert run_cr2.needs_rerun(tmp_path / "parse", {"status": "failed", "stop_reason": "model_parse_error"}) is None


# --- a fake run_task drives run_slots end to end -------------------------------------------------

TOOLS = {"n": ["read_file", "list_dir"], "p": ["read_file", "list_dir"], "t": ["read_file", "list_dir", "retrieve_code"]}


def _expected(ids):
    out = {}
    for task in ids:
        base = "prompt for " + task
        suffix = PREFETCH_HEADER + "\n\n1. a.py\n\n" + PREFETCH_FOOTER
        out[(task, "n")] = {"suffix": None, "text": base, "sha256": run_cr2._sha(base)}
        out[(task, "p")] = {"suffix": suffix, "text": base + "\n\n" + suffix, "sha256": run_cr2._sha(base + "\n\n" + suffix)}
        out[(task, "t")] = {"suffix": None, "text": base, "sha256": run_cr2._sha(base)}
    return out


class FakeAgent:
    def __init__(self, names):
        self.enabled_tools = names
        self.tools = self

    def schemas(self, enabled):
        return [{"type": "function", "function": {"name": n}} for n in enabled]


class FakeIndex:
    pass


class FakeService:
    def __init__(self):
        self.warmed = 0
        self.indexes = []

    def warm(self):
        self.warmed += 1

    def task_index(self, repo, base_commit):
        self.indexes.append((repo, base_commit))
        return FakeIndex()


def make_fake_run(behaviour=None, calls=None):
    """behaviour(task, group, attempt) -> dict of overrides: status, events, prompt, tools, crash_log."""
    behaviour = behaviour or (lambda task, group, attempt: {})
    attempts = {}

    async def run(task, profile, output, *, mock, variant, prompt_suffix, retrieval, mock_llm, on_agent):
        group = {"CR2-N": "n", "CR2-P": "p", "CR2-T": "t"}[variant]
        key = (task["id"], group)
        attempts[key] = attempts.get(key, 0) + 1
        over = behaviour(task["id"], group, attempts[key])
        if calls is not None:
            calls.append((task["id"], group, prompt_suffix, retrieval is not None, mock_llm is not None))
        prompt = over.get("prompt") or ((task["prompt"] + "\n\n" + prompt_suffix) if prompt_suffix else task["prompt"])
        on_agent(FakeAgent(over.get("tools") or TOOLS[group]))
        events = [{"event_type": "state.saved", "payload": {"messages": [{"role": "system", "content": "s"},
                                                                         {"role": "user", "content": prompt}]}}]
        events += over.get("events", [_settled()])
        _write_events(output, events)
        if retrieval is not None:
            retrieval.log({"call_id": "c1", "query": "q", "k": 5, **({"traceback": "boom"} if over.get("crash_log") else {})})
        summary = {"task_id": task["id"], "mock": mock, "profile": Path(profile).stem, "variant": variant,
                   "status": over.get("status", "failed"), "solved": False, "cost_cny": "0", "harness_tool_errors": 0,
                   "prompt_sha256": run_cr2._sha(prompt), "model": {"name": "mock-model", "base_url_host": None}}
        (output / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        return summary

    return run


def _run(tmp_path, slots, run, service=None, ids=None):
    ids = ids or sorted({s[0] for s in slots})
    prepared = {task: {"id": task, "repo": "o/r", "base_commit": "abc", "prompt": "prompt for " + task} for task in ids}
    return asyncio.run(run_cr2.run_slots(slots, mock=True, out_root=tmp_path / "out", prepared=prepared,
                                         expected=_expected(ids), tools=TOOLS, prefetch_sha256="x", label="t",
                                         service=service or FakeService(), run=run))


def test_run_slots_runs_verifies_and_resumes(tmp_path):
    slots = [("a", "n", 1), ("a", "p", 1), ("b", "t", 1)]
    calls, service = [], FakeService()
    report = _run(tmp_path, slots, make_fake_run(calls=calls), service=service)
    assert report["halt_reason"] is None and report["started"] == 3 and report["injection_verified_runs"] == 3
    assert service.warmed == 1 and service.indexes == [("o/r", "abc")]
    by_group = {c[1]: c for c in calls}
    assert by_group["n"][2] is None and by_group["p"][2].startswith(PREFETCH_HEADER) and by_group["t"][2] is None
    assert [c[3] for c in calls if c[1] == "t"] == [True] and all(not c[3] for c in calls if c[1] != "t")
    assert (tmp_path / "out" / "b" / "t" / "r1" / run_cr2.CALLS_FILE).is_file()
    tools = json.loads((tmp_path / "out" / "a" / "n" / "r1" / run_cr2.TOOLS_FILE).read_text(encoding="utf-8"))
    assert tools["tools"] == TOOLS["n"]
    again = _run(tmp_path, slots + [("b", "n", 2)], make_fake_run(calls=calls))
    assert again["started"] == 1 and again["skipped_finished"] == 3 and again["halt_reason"] is None


def test_run_slots_reruns_provider_failures_and_keeps_directory(tmp_path):
    def behaviour(task, group, attempt):
        return {"events": [_settled(False, "http", 402)]} if attempt == 1 else {}

    report = _run(tmp_path, [("a", "n", 1)], make_fake_run(behaviour))
    assert report["halt_reason"] is None and report["started"] == 1
    assert report["infra_retired"] == [run_cr2._display_path(tmp_path / "out" / "a" / "n" / "r1.infra1")]
    assert (tmp_path / "out" / "a" / "n" / "r1.infra1" / "summary.json").is_file()
    assert run_cr2.provider_failure(tmp_path / "out" / "a" / "n" / "r1") is None


def test_run_slots_does_not_rerun_task_results(tmp_path):
    def behaviour(task, group, attempt):
        return {"events": [_settled(False, "parse")]}

    report = _run(tmp_path, [("a", "n", 1)], make_fake_run(behaviour))
    assert report["infra_retired"] == [] and report["halt_reason"] is None


@pytest.mark.parametrize("over,needle", [
    ({"prompt": "something else"}, "differs from the expected prompt"),
    ({"tools": ["read_file", "list_dir", "retrieve_code"]}, "tool schemas differ"),
])
def test_injection_failure_stops_the_run(tmp_path, over, needle):
    def behaviour(task, group, attempt):
        return over if task == "a" else {}

    slots = [("a", "n", 1), ("b", "n", 1), ("c", "n", 1), ("d", "n", 1)]
    report = _run(tmp_path, slots, make_fake_run(behaviour))
    assert needle in report["halt_reason"] and report["not_started"] >= 1


def test_t_without_retrieve_code_and_crashed_tool_stop(tmp_path):
    report = _run(tmp_path, [("a", "t", 1)], make_fake_run(lambda *a: {"tools": TOOLS["n"]}))
    assert "tool schemas differ" in report["halt_reason"]
    report = _run(tmp_path / "2", [("a", "t", 1)], make_fake_run(lambda *a: {"crash_log": True}))
    assert "retrieve_code crashed" in report["halt_reason"]


def test_refuses_foreign_summaries(tmp_path):
    run_dir = tmp_path / "out" / "a" / "n" / "r1"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(json.dumps({"mock": False, "profile": "b0-v2", "variant": "CR2-N"}), encoding="utf-8")
    with pytest.raises(SystemExit, match="not overwriting"):
        _run(tmp_path, [("a", "n", 1)], make_fake_run())


def test_spend_cap_halts(tmp_path):
    run = make_fake_run()
    _run(tmp_path, [("a", "n", 1)], run)
    summary_path = tmp_path / "out" / "a" / "n" / "r1" / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["cost_cny"] = "200.0"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    reason = run_cr2.halt_reason(tmp_path / "out", _expected(["a"]), TOOLS, cap=Decimal("200"))
    assert "cap" in reason


# --- mock model ----------------------------------------------------------------------------------

def _chat(llm, messages, names):
    tools = [{"type": "function", "function": {"name": n}} for n in names]
    return asyncio.run(llm.chat(messages, tools))


def test_mock_llm_t_calls_retrieve_code_then_reads_first_hit():
    llm = run_cr2.CR2MockLLM()
    user = {"role": "user", "content": "Fix it.\n\nPublic issue:\nThe header is wrong."}
    first = _chat(llm, [user], TOOLS["t"])
    call = first.tool_calls[0]
    assert call.name == "retrieve_code" and call.arguments == {"query": "The header is wrong.", "k": 5}
    tool_msg = {"role": "tool", "content": TOOL_HEADER + "\n\n1. pkg/http.py\n   - f (lines 1-3)\n2. b.py"}
    reminder = {"role": "user", "content": "remaining model calls: 38/40"}
    second = _chat(llm, [user, tool_msg, reminder], TOOLS["t"])
    assert second.tool_calls[0].name == "read_file" and second.tool_calls[0].arguments["path"] == "pkg/http.py"
    assert not _chat(llm, [user, tool_msg], TOOLS["t"]).tool_calls


def test_mock_llm_p_and_n_paths():
    p_user = {"role": "user", "content": "Public issue:\nx\n\n" + PREFETCH_HEADER + "\n\n1. src/a.py\n   - g (lines 2-5)\n\n" + PREFETCH_FOOTER}
    first = _chat(run_cr2.CR2MockLLM(), [p_user], TOOLS["p"])
    assert first.tool_calls[0].name == "read_file" and first.tool_calls[0].arguments["path"] == "src/a.py"
    llm = run_cr2.CR2MockLLM()
    n_user = {"role": "user", "content": "Public issue:\nx"}
    assert _chat(llm, [n_user], TOOLS["n"]).tool_calls[0].name == "list_dir"
    listing = {"role": "tool", "content": "[D] docs\n[F] README.rst\n[F] setup.py"}
    reminder = {"role": "user", "content": "remaining model calls: 38/40"}
    assert _chat(llm, [n_user, listing, reminder], TOOLS["n"]).tool_calls[0].arguments["path"] == "setup.py"
