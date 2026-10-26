"""CR-2 runner: slot queue, selection, resume, provider-failure reruns, injection check, mock model."""
import asyncio
import json
from decimal import Decimal
from pathlib import Path

import pytest

from scripts import run_cr2
from src.retrieval.serve import PREFETCH_FOOTER, PREFETCH_HEADER, TOOL_HEADER

IDS = [f"repo__repo-{i:03d}" for i in range(56)]


def test_queue_has_1120_interleaved_slots_and_is_deterministic():
    assert run_cr2.GROUPS == ("n", "p", "t", "o") and run_cr2.REPEATS == (1, 2, 3, 4, 5)
    assert run_cr2.TOTAL_CAP == Decimal("800")
    queue = run_cr2.slot_queue(IDS)
    assert len(queue) == run_cr2.EXPECTED_SLOTS == 1120 and len(set(queue)) == 1120
    assert queue == run_cr2.slot_queue(list(reversed(IDS)))
    assert queue != run_cr2.all_slots(IDS)
    assert {group for _, group, _ in queue[:40]} == {"n", "p", "t", "o"}
    assert {k for _, _, k in queue} == {1, 2, 3, 4, 5}


def test_canary_and_mock_selection():
    queue = run_cr2.slot_queue(IDS)
    canary = run_cr2.canary_slots(queue)
    assert len(canary) == 8 and all([g for _, g, _ in canary].count(group) == 2 for group in run_cr2.GROUPS)
    assert canary == sorted(canary, key=queue.index)
    firsts = {}
    for slot in queue:
        firsts.setdefault(slot[1], []).append(slot)
    assert set(canary) == {slot for group in run_cr2.GROUPS for slot in firsts[group][:2]}
    mock = run_cr2.mock_slots(queue)
    assert len(mock) == 8 and len({task for task, _, _ in mock}) == 8
    assert sorted(g for _, g, _ in mock) == ["n", "n", "o", "o", "p", "p", "t", "t"]


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
    assert run_cr2.resumable({"mock": True, "profile": "b0-v2", "variant": "CR2-O"}, "o", mock=True) is None
    assert "variant" in run_cr2.resumable({"mock": True, "profile": "b0-v2", "variant": "CR2-P"}, "o", mock=True)


def test_check_authorized():
    run_cr2.check_authorized("5,800")
    for bad in (None, "2,200", "5,200", "3,800", "two"):
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

TOOLS = {"n": ["read_file", "list_dir"], "p": ["read_file", "list_dir"], "t": ["read_file", "list_dir", "retrieve_code"],
         "o": ["read_file", "list_dir"]}


def _expected(ids):
    out = {}
    for task in ids:
        base = "prompt for " + task
        out[(task, "n")] = {"suffix": None, "text": base, "sha256": run_cr2._sha(base)}
        out[(task, "t")] = {"suffix": None, "text": base, "sha256": run_cr2._sha(base)}
        for group, listed in (("p", "a.py"), ("o", "gold.py")):
            suffix = PREFETCH_HEADER + "\n\n1. " + listed + "\n\n" + PREFETCH_FOOTER
            out[(task, group)] = {"suffix": suffix, "text": base + "\n\n" + suffix, "sha256": run_cr2._sha(base + "\n\n" + suffix)}
    return out


class FakeLLM:
    async def _ensure_client(self):
        raise AssertionError("the fake run never calls the model")


class FakeAgent:
    def __init__(self, names):
        self.enabled_tools = names
        self.tools = self
        self.llm = FakeLLM()

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
    """behaviour(task, group, attempt) -> dict of overrides: status, events, prompt, tools, crash_log, models
    (text written to model-names.json)."""
    behaviour = behaviour or (lambda task, group, attempt: {})
    attempts = {}

    async def run(task, profile, output, *, mock, variant, prompt_suffix, retrieval, mock_llm, on_agent):
        group = {"CR2-N": "n", "CR2-P": "p", "CR2-T": "t", "CR2-O": "o"}[variant]
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
        if "models" in over:
            (output / run_cr2.MODELS_FILE).write_text(over["models"], encoding="utf-8")
        summary = {"task_id": task["id"], "mock": mock, "profile": Path(profile).stem, "variant": variant,
                   "status": over.get("status", "failed"), "solved": False, "cost_cny": "0", "harness_tool_errors": 0,
                   "prompt_sha256": run_cr2._sha(prompt), "model": {"name": "mock-model", "base_url_host": None}}
        (output / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        return summary

    return run


def _run(tmp_path, slots, run, service=None, ids=None, mock=True):
    ids = ids or sorted({s[0] for s in slots})
    prepared = {task: {"id": task, "repo": "o/r", "base_commit": "abc", "prompt": "prompt for " + task} for task in ids}
    return asyncio.run(run_cr2.run_slots(slots, mock=mock, out_root=tmp_path / "out", prepared=prepared,
                                         expected=_expected(ids), tools=TOOLS, prefetch_sha256="x", label="t",
                                         service=service or FakeService(), run=run))


def test_run_slots_runs_verifies_and_resumes(tmp_path):
    slots = [("a", "n", 1), ("a", "p", 1), ("b", "t", 1), ("c", "o", 3)]
    calls, service = [], FakeService()
    report = _run(tmp_path, slots, make_fake_run(calls=calls), service=service)
    assert report["halt_reason"] is None and report["started"] == 4 and report["injection_verified_runs"] == 4
    assert report["started_at"] <= report["finished_at"] and report["returned_models"] == []
    assert service.warmed == 1 and service.indexes == [("o/r", "abc")]
    by_group = {c[1]: c for c in calls}
    assert by_group["n"][2] is None and by_group["p"][2].startswith(PREFETCH_HEADER) and by_group["t"][2] is None
    assert by_group["o"][2].startswith(PREFETCH_HEADER) and "gold.py" in by_group["o"][2]
    assert [c[3] for c in calls if c[1] == "t"] == [True] and all(not c[3] for c in calls if c[1] != "t")
    assert (tmp_path / "out" / "b" / "t" / "r1" / run_cr2.CALLS_FILE).is_file()
    assert not (tmp_path / "out" / "c" / "o" / "r3" / run_cr2.CALLS_FILE).exists()
    tools = json.loads((tmp_path / "out" / "a" / "n" / "r1" / run_cr2.TOOLS_FILE).read_text(encoding="utf-8"))
    assert tools["tools"] == TOOLS["n"]
    again = _run(tmp_path, slots + [("b", "n", 2)], make_fake_run(calls=calls))
    assert again["started"] == 1 and again["skipped_finished"] == 4 and again["halt_reason"] is None


def test_o_group_with_p_text_or_t_tools_stops(tmp_path):
    def p_text(task, group, attempt):
        return {"prompt": "prompt for a\n\n" + PREFETCH_HEADER + "\n\n1. a.py\n\n" + PREFETCH_FOOTER}

    report = _run(tmp_path, [("a", "o", 1)], make_fake_run(p_text))
    assert "differs from the expected prompt" in report["halt_reason"]
    report = _run(tmp_path / "2", [("a", "o", 1)], make_fake_run(lambda *a: {"tools": TOOLS["t"]}))
    assert "tool schemas differ" in report["halt_reason"]


def test_expected_prompts_cover_four_groups(monkeypatch):
    monkeypatch.setattr(run_cr2, "build_prompt", lambda task, suffix=None: "base" + ("\n\n" + suffix if suffix else ""))
    prefetch = {"a": {"text": "P-TEXT"}}
    oracle = {"a": {"text": "O-TEXT"}}
    expected = run_cr2.expected_prompts({"a": {}}, prefetch, oracle)
    assert {g: expected[("a", g)]["text"] for g in run_cr2.GROUPS} == {
        "n": "base", "p": "base\n\nP-TEXT", "t": "base", "o": "base\n\nO-TEXT"}
    assert expected[("a", "o")]["suffix"] == "O-TEXT" and expected[("a", "t")]["suffix"] is None
    monkeypatch.setattr(run_cr2, "build_prompt", lambda task, suffix=None: "base" + (suffix or ""))
    with pytest.raises(SystemExit):
        run_cr2.expected_prompts({"a": {}}, prefetch, oracle)


def test_load_prefetch_checks_frame_and_schema(tmp_path):
    from src.retrieval import serve

    text = serve.render_prefetch([serve.FileHit("a.py", (serve.ChunkHit("f", 1, 2),))])
    row = {"task": "a", "text": text, "sha256": run_cr2._sha(text), "bytes": len(text.encode("utf-8")), "issue_sha256": "x"}
    manifest = {"schema": run_cr2.ORACLE_SCHEMA, "header": serve.PREFETCH_HEADER, "footer": serve.PREFETCH_FOOTER, "tasks": [row]}
    path = tmp_path / "o.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    rows, _ = run_cr2.load_prefetch(path, {"a"}, schema=run_cr2.ORACLE_SCHEMA)
    assert rows["a"]["text"] == text
    with pytest.raises(SystemExit, match="schema"):
        run_cr2.load_prefetch(path, {"a"})
    bad = "Different header\n\n1. a.py\n\n" + serve.PREFETCH_FOOTER
    manifest["tasks"] = [{**row, "text": bad, "sha256": run_cr2._sha(bad), "bytes": len(bad)}]
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(SystemExit, match="row frame"):
        run_cr2.load_prefetch(path, {"a"}, schema=run_cr2.ORACLE_SCHEMA)


# --- returned model name -------------------------------------------------------------------------

def _sse(*chunks):
    return b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks) + b"data: [DONE]\n\n"


class _Pieces:
    def __init__(self, body):
        self.body = body

    async def __aiter__(self):
        for i in range(0, len(self.body), 7):
            yield self.body[i:i + 7]

    async def aclose(self):
        pass


def _encode(body: bytes, encoding):
    """encoding: None, "gzip", "deflate" (zlib wrapped, as HTTP specifies) or "deflate-raw" (no zlib header)."""
    import gzip
    import zlib

    if encoding is None:
        return body, {}
    if encoding == "gzip":
        return gzip.compress(body), {"content-encoding": "gzip"}
    if encoding == "deflate":
        return zlib.compress(body), {"content-encoding": "deflate"}
    raw = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    return raw.compress(body) + raw.flush(), {"content-encoding": "deflate"}


def _network_response(status, body: bytes, content_type="application/json", encoding=None):
    """A response whose body arrives in small pieces, like a real connection (content= would preload it)."""
    import httpx

    class Pieces(_Pieces, httpx.AsyncByteStream):
        pass

    body, headers = _encode(body, encoding)
    return httpx.Response(status, stream=Pieces(body), headers={"content-type": content_type, **headers})


def _client(handler):
    import httpx

    from src.llm.client import LLMClient

    return LLMClient("https://api.example.com/v1", "k", "alias-model", supports_streaming=True,
                     http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def _model_handler(encoding=None):
    def handler(request):
        body = json.loads(request.content)
        if body.get("stream"):
            chunks = [{"id": "1", "model": "served-v4", "choices": [{"index": 0, "delta": {"content": "he said \"model\": \"x\""}}]},
                      {"id": "1", "model": "served-v4", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                       "usage": {"prompt_tokens": 3, "completion_tokens": 2}}]
            return _network_response(200, _sse(*chunks), "text/event-stream", encoding)
        return _network_response(200, json.dumps({"id": "2", "model": "served-plain", "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}}).encode(), encoding=encoding)
    return handler


def _tap_round(output, encoding, tap):
    async def go():
        llm = _client(_model_handler(encoding))
        if tap:
            run_cr2.model_name_tap(llm, run_cr2.model_recorder(output))
        events = [e async for e in llm.stream_chat([{"role": "user", "content": "hi"}])]
        plain = await llm.chat([{"role": "user", "content": "hi"}])
        await llm.aclose()
        done = [e for e in events if e.event == "llm_stream_done"][0].data["response"]
        return done.text, done.usage, plain.text
    return asyncio.run(go())


@pytest.mark.parametrize("encoding", [None, "gzip", "deflate", "deflate-raw"])
def test_model_name_tap_records_streamed_and_plain_responses(tmp_path, encoding):
    tapped = _tap_round(tmp_path, encoding, tap=True)
    assert tapped == _tap_round(tmp_path / "untapped", encoding, tap=False) == _tap_round(tmp_path / "x", None, tap=False)
    assert run_cr2.returned_models(tmp_path) == ["served-plain", "served-v4"]


@pytest.mark.parametrize("encoding,body", [
    ("br", b'{"model":"hidden"}'),            # not decodable here: passed through, not scanned
    ("gzip", b'not gzip {"model":"bad"}'),    # broken body: passed through, scanning stops quietly
])
def test_model_tap_stream_passes_other_bodies_through(encoding, body):
    seen = []

    async def go():
        stream = run_cr2._ModelTapStream(_Pieces(body), seen.append, encoding)
        return b"".join([chunk async for chunk in stream])

    assert asyncio.run(go()) == body and seen == []


@pytest.mark.parametrize("encoding", [None, "gzip"])
def test_model_name_tap_never_breaks_errors(tmp_path, encoding):
    from src.llm.client import LLMError

    def handler(request):
        return _network_response(402, json.dumps({"error": {"message": "Insufficient Balance"}}).encode(),
                                 encoding=encoding)

    async def go():
        llm = _client(handler)
        llm.supports_streaming = False
        run_cr2.model_name_tap(llm, run_cr2.model_recorder(tmp_path))
        try:
            await llm.chat([{"role": "user", "content": "hi"}])
        finally:
            await llm.aclose()

    with pytest.raises(LLMError) as err:
        asyncio.run(go())
    assert err.value.status_code == 402 and "Insufficient Balance" in str(err.value)
    assert run_cr2.returned_models(tmp_path) == []


FIVE = [("a", "n", 1), ("b", "p", 1), ("c", "t", 1), ("d", "o", 1), ("e", "n", 1)]


def test_real_run_stops_when_first_three_slots_record_no_model_name(tmp_path):
    missing = {"a": "[]", "b": ""}  # c writes nothing; an empty list or an empty file counts as missing

    def behaviour(task, group, attempt):
        return {"models": missing[task]} if task in missing else {}

    report = _run(tmp_path, FIVE, make_fake_run(behaviour), mock=False)
    assert "no returned model name recorded in the first 3 real slots" in report["halt_reason"]
    # slots already running when the check fires finish; no new slot starts
    assert 3 <= report["started"] <= 4 and report["not_started"] == 5 - report["started"]
    assert len(report["returned_model_slots"]["without_model_name"]) == report["started"]


def test_real_run_continues_and_lists_slots_per_model(tmp_path):
    names = {"b": '["deepseek-v4.1-flash"]', "d": '["deepseek-v4.1-flash", "deepseek-v4.2-flash"]',
             "e": '["deepseek-v4.2-flash"]'}

    def behaviour(task, group, attempt):
        return {"models": names[task]} if task in names else {}

    report = _run(tmp_path, FIVE, make_fake_run(behaviour), mock=False)
    assert report["halt_reason"] is None and report["started"] == 5
    assert report["returned_models"] == ["deepseek-v4.1-flash", "deepseek-v4.2-flash"]
    assert report["returned_model_slots"] == {
        "by_model": {"deepseek-v4.1-flash": ["b/p/r1", "d/o/r1"], "deepseek-v4.2-flash": ["d/o/r1", "e/n/r1"]},
        "without_model_name": ["a/n/r1", "c/t/r1"]}


def test_model_name_check_skips_mock_and_short_runs(tmp_path):
    assert _run(tmp_path, FIVE, make_fake_run())["halt_reason"] is None
    report = _run(tmp_path / "2", FIVE[:2], make_fake_run(), mock=False)
    assert report["halt_reason"] is None and report["started"] == 2
    assert run_cr2.model_name_halt([]) is None


def test_returned_models_tolerates_bad_files(tmp_path):
    path = tmp_path / run_cr2.MODELS_FILE
    for text, want in (("", []), ("{", []), ('{"a": 1}', []), ('["x", "", 3]', ["x"]), ('["m"]', ["m"])):
        path.write_text(text, encoding="utf-8")
        assert run_cr2.returned_models(tmp_path) == want


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


def test_tool_recorder_taps_only_real_runs(tmp_path):
    class Agent(FakeAgent):
        def __init__(self):
            super().__init__(TOOLS["n"])
            self.llm = _client(lambda request: None)

    plain, tapped = Agent(), Agent()
    original = plain.llm._ensure_client
    run_cr2.tool_recorder(tmp_path)(plain)
    assert plain.llm._ensure_client == original
    run_cr2.tool_recorder(tmp_path, tap_model=True)(tapped)
    assert tapped.llm._ensure_client != original and (tmp_path / run_cr2.TOOLS_FILE).is_file()


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
