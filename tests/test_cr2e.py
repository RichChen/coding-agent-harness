"""CR-2e-a: T2 configuration, compatibility of b0-v2 and cr2-t, the dev-check runner and its analysis."""
import hashlib
import json
from pathlib import Path

import pytest

from scripts import analyze_cr2e, cr2_compat, run_cr2e
from src.harness.factory import build_agent, load_config
from src.tools.retrieve import RetrieveCodeTool, TaskRetrieval

ROOT = Path(__file__).resolve().parents[1]
B0 = ROOT / "configs" / "sprint" / "b0-v2.yaml"
T = ROOT / "configs" / "retrieval" / "cr2-t.yaml"
T2 = ROOT / "configs" / "retrieval" / "cr2e-t2.yaml"
T2_R2 = ROOT / "configs" / "retrieval" / "cr2e-t2-r2.yaml"
CR2_T_SCHEMAS_SHA256 = "77adc36ffa19019d9b922f1b116631f7d183bf5548826b8cfc83bf69595f5b63"
DEFAULT_DESCRIPTION = (
    "Search the original repository at the base revision (before any edits made in this session) "
    "for the files most related to a query. Returns up to k file paths, each with at most two "
    "symbols and their line ranges. Does not return code. Read-only."
)


def _agent(profile, tmp_path, **kwargs):
    config = load_config(profile)
    config["llm"]["api_key"] = "test"
    return build_agent(config, tmp_path, **kwargs)


def _schemas_sha(agent) -> str:
    text = json.dumps(agent.tools.schemas(agent.enabled_tools), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- compatibility (contract 0) --------------------------------------------------------------------

def test_b0v2_compat_snapshot_identical():
    baseline = json.loads(cr2_compat.FIXTURE.read_text(encoding="utf-8"))
    assert cr2_compat.agent_snapshot() == baseline["agent"]


def test_cr2_t_tool_schemas_unchanged(tmp_path):
    agent = _agent(T, tmp_path, retrieval=TaskRetrieval(None, tmp_path / "calls.jsonl"))
    assert _schemas_sha(agent) == CR2_T_SCHEMAS_SHA256
    assert agent.tools.get("retrieve_code").description == DEFAULT_DESCRIPTION


def test_t2_config_differs_from_cr2_t_only_in_system_prompt():
    t, t2 = T.read_text(encoding="utf-8").splitlines(), T2.read_text(encoding="utf-8").splitlines()
    assert len(t) == len(t2)
    diff = [(a, b) for a, b in zip(t, t2) if a != b]
    assert diff == [(diff[0][0], "  system_prompt_path: prompts/swe_cr2e.md")]
    assert diff[0][0].strip().startswith("system_prompt_path:")
    t_cfg, t2_cfg = load_config(T), load_config(T2)
    assert t2_cfg["runtime"]["system_prompt_path"] == t_cfg["runtime"]["system_prompt_path"].replace("swe_b0", "swe_cr2e")
    t2_cfg["runtime"]["system_prompt_path"] = t_cfg["runtime"]["system_prompt_path"]
    assert t2_cfg == t_cfg


def test_t2_prompt_replaces_only_the_search_sentence():
    b0 = (ROOT / "prompts" / "swe_b0.md").read_text(encoding="utf-8")
    t2 = (ROOT / "prompts" / "swe_cr2e.md").read_text(encoding="utf-8")
    old = "Use search_code to find symbols. "
    new = (
        "There are two search tools:\n"
        "- retrieve_code ranks files of the original repository by relevance to a description. Use it when the issue "
        "describes a symptom in words and you do not yet know which module handles it. Its results are candidates: "
        "read the listed code to confirm before editing.\n"
        "- search_code greps the current workspace. Use it when you already have an exact symbol, error message or "
        "pattern.\n"
    )
    assert b0.count(old) == 1
    assert t2 == b0.replace(old, new)


def test_description_parameter_defaults_to_the_old_text():
    assert RetrieveCodeTool().description == DEFAULT_DESCRIPTION
    assert RetrieveCodeTool(description="other").description == "other"
    assert RetrieveCodeTool().description == DEFAULT_DESCRIPTION


def test_set_description_keeps_tool_order_and_changes_only_the_description(tmp_path):
    retrieval = TaskRetrieval(None, tmp_path / "calls.jsonl")
    agent = _agent(T2, tmp_path, retrieval=retrieval)
    before = agent.tools.schemas(agent.enabled_tools)
    run_cr2e.set_description(agent, retrieval, run_cr2e.DESCRIPTION_V2)
    after = agent.tools.schemas(agent.enabled_tools)
    assert [s["function"]["name"] for s in after] == [s["function"]["name"] for s in before]
    assert after[:-1] == before[:-1]
    assert after[-1]["function"]["description"] == run_cr2e.DESCRIPTION_V2
    assert after[-1]["function"]["parameters"] == before[-1]["function"]["parameters"]
    assert agent.tools.get("retrieve_code").retrieval is retrieval


def test_expected_t2_agent_is_b0v2_plus_retrieve_code_with_t2_prompt():
    want = run_cr2e.expected_agent(run_cr2e.ROUNDS[1])
    assert want["tools"] == cr2_compat.agent_snapshot()["tool_names"] + ["retrieve_code"]
    assert want["schemas_sha256"] != CR2_T_SCHEMAS_SHA256
    assert want["system_prompt"] == (ROOT / "prompts" / "swe_cr2e.md").read_text(encoding="utf-8").strip()


def test_no_config_other_than_t_and_t2_enables_retrieve_code():
    for path in (ROOT / "configs").rglob("*.yaml"):
        # configs/workspace/ 启用的是 P2 的工作区检索工具，与 CR-2 的 retrieve_code 同名，不在这个守卫的范围内。
        if (ROOT / "configs" / "workspace") in path.parents:
            continue
        if path not in (T, T2, T2_R2):
            assert "retrieve_code" not in path.read_text(encoding="utf-8"), path


def test_round2_changes_only_the_retrieve_code_line_of_the_prompt():
    r1 = (ROOT / "prompts" / "swe_cr2e.md").read_text(encoding="utf-8").splitlines()
    r2 = (ROOT / "prompts" / "swe_cr2e-r2.md").read_text(encoding="utf-8").splitlines()
    diff = [(a, b) for a, b in zip(r1, r2) if a != b]
    assert len(r1) == len(r2) and len(diff) == 1
    assert diff[0][0].startswith("- retrieve_code ") and diff[0][1].startswith("- retrieve_code ")
    assert run_cr2e.ROUNDS[2]["description"] == run_cr2e.ROUNDS[1]["description"] == run_cr2e.DESCRIPTION_V2


def test_round2_config_differs_from_t2_only_in_system_prompt():
    t2, r2 = T2.read_text(encoding="utf-8").splitlines(), T2_R2.read_text(encoding="utf-8").splitlines()
    diff = [(a, b) for a, b in zip(t2, r2) if a != b]
    assert len(t2) == len(r2) and diff == [("  system_prompt_path: prompts/swe_cr2e.md", "  system_prompt_path: prompts/swe_cr2e-r2.md")]
    want = run_cr2e.expected_agent(run_cr2e.ROUNDS[2])
    assert want["tools"] == run_cr2e.expected_agent(run_cr2e.ROUNDS[1])["tools"]
    assert want["system_prompt"] == (ROOT / "prompts" / "swe_cr2e-r2.md").read_text(encoding="utf-8").strip()


# --- task selection --------------------------------------------------------------------------------

def _n_rows(means: dict) -> list[dict]:
    rows = []
    for task, (a, b) in means.items():
        rows += [{"group": "n", "task_id": task, "first_gold_step": a}, {"group": "n", "task_id": task, "first_gold_step": b},
                 {"group": "p", "task_id": task, "first_gold_step": 99}]
    return rows


def test_select_tasks_takes_hardest_four_with_id_ties_then_seeded_sample():
    means = {f"t{i:02d}": (i, i) for i in range(10)}
    means["t00"] = (50, 50)
    means["t01"] = (9, 9)  # ties with t09 on mean 9
    chosen = run_cr2e.select_tasks(_n_rows(means), sorted(means))
    assert [row["task_id"] for row in chosen["hardest"]] == ["t00", "t01", "t09", "t08"]
    import random

    rest = sorted(t for t in means if t not in {"t00", "t01", "t09", "t08"})
    assert chosen["random"] == random.Random(20261029).sample(rest, 2)
    assert chosen["tasks"] == ["t00", "t01", "t09", "t08"] + chosen["random"]


def test_select_tasks_refuses_partial_coverage():
    with pytest.raises(SystemExit):
        run_cr2e.select_tasks(_n_rows({"a": (1, 2)}), ["a", "b"])


def test_slots_are_twelve_in_repeat_major_order():
    slots = run_cr2e.all_slots(["b", "a", "c", "f", "e", "d"])
    assert len(slots) == 12
    assert slots[:2] == [("a", 1), ("b", 1)] and slots[6] == ("a", 2)


# --- completion guard and authorization ------------------------------------------------------------

def _runs_file(tmp_path, rows, expected=2):
    path = tmp_path / "runs.json"
    path.write_text(json.dumps({"slots_expected": expected, "runs": rows}), encoding="utf-8")
    return path


def test_refuse_finished_only_for_a_complete_runs_file(tmp_path):
    run_cr2e.refuse_finished(tmp_path / "absent.json")
    run_cr2e.refuse_finished(_runs_file(tmp_path, [{"rerun_reason": None}]))
    run_cr2e.refuse_finished(_runs_file(tmp_path, [{"rerun_reason": None}, {"rerun_reason": "provider"}]))
    with pytest.raises(SystemExit, match="not run again"):
        run_cr2e.refuse_finished(_runs_file(tmp_path, [{"rerun_reason": None}, {"rerun_reason": None}]))


def test_real_runs_need_the_exact_authorization():
    run_cr2e.check_authorized("dev,20")
    for value in (None, "dev", "dev,40", "formal,20"):
        with pytest.raises(SystemExit):
            run_cr2e.check_authorized(value)


# --- injection check -------------------------------------------------------------------------------

def _run_dir(tmp_path, *, system="SYS", user="PROMPT", tools=None, calls=None):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    event = {"event_type": "state.saved", "payload": {"messages": messages}}
    (run_dir / "events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
    if tools is not None:
        (run_dir / run_cr2e.TOOLS_FILE).write_text(json.dumps(tools), encoding="utf-8")
    if calls is not None:
        (run_dir / run_cr2e.CALLS_FILE).write_text("".join(json.dumps(c) + "\n" for c in calls), encoding="utf-8")
    return run_dir


WANT = {"tools": ["read_file", "retrieve_code"], "description": "V2", "system_prompt": "SYS",
        "system_prompt_sha256": run_cr2e._sha("SYS"), "schemas_sha256": "abc"}
GOOD_TOOLS = {"tools": ["read_file", "retrieve_code"], "retrieve_code_description": "V2",
              "system_prompt_sha256": run_cr2e._sha("SYS"), "schemas_sha256": "abc"}
SUMMARY = {"prompt_sha256": run_cr2e._sha("PROMPT")}


def test_injection_ok(tmp_path):
    assert run_cr2e.injection_problem(_run_dir(tmp_path, tools=GOOD_TOOLS), SUMMARY, "PROMPT", WANT) is None


@pytest.mark.parametrize("kwargs, change, expect", [
    ({"user": "OTHER"}, {}, "first user message"),
    ({"system": "B0"}, {}, "system message"),
    ({}, {"tools": ["read_file"]}, "tool list"),
    ({}, {"retrieve_code_description": "old"}, "description"),
    ({}, {"schemas_sha256": "xyz"}, "schema sha256"),
])
def test_injection_problems(tmp_path, kwargs, change, expect):
    run_dir = _run_dir(tmp_path, tools={**GOOD_TOOLS, **change}, **kwargs)
    assert expect in run_cr2e.injection_problem(run_dir, SUMMARY, "PROMPT", WANT)


def test_injection_flags_missing_record_and_crashed_retrieval(tmp_path):
    assert "missing" in run_cr2e.injection_problem(_run_dir(tmp_path), SUMMARY, "PROMPT", WANT)
    other = tmp_path / "b"
    other.mkdir()
    run_dir = _run_dir(other, tools=GOOD_TOOLS, calls=[{"call_id": "c1", "traceback": "boom"}])
    assert "crashed" in run_cr2e.injection_problem(run_dir, SUMMARY, "PROMPT", WANT)
    assert "prompt_sha256" in run_cr2e.injection_problem(run_dir, {}, "PROMPT", WANT)


# --- answer isolation and demo ---------------------------------------------------------------------

def test_cr2e_scripts_read_gold_only_through_analyze_cr2():
    from tests.test_retrieval_isolation import LABELS_IMPORT

    for name in ("run_cr2e.py", "analyze_cr2e.py", "cr_demo.py"):
        text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert LABELS_IMPORT.search(text) is None, name
    assert "load_gold" not in (ROOT / "scripts" / "run_cr2e.py").read_text(encoding="utf-8")


def test_demo_argument_summaries():
    from scripts import cr_demo

    assert cr_demo._arg_summary("run_command", {"command": "x" * 200}) == "x" * 80
    assert cr_demo._arg_summary("retrieve_code", {"query": "q", "k": 3}) == "query: q，k=3"
    assert cr_demo._arg_summary("read_file", {"path": "a.py", "offset": 5}) == "a.py (offset=5)"
    assert cr_demo._arg_summary("search_code", {"pattern": "p", "path": "src"}) == "pattern: p in src"


def test_demo_trajectory_and_issue_fence(tmp_path):
    from types import SimpleNamespace

    from scripts import cr_demo

    events = _events([("read_file", {"path": "a.py"}, "r1")], [("run_command", {"command": "ls"}, "c1")])
    events.append({"event_type": "tool.result", "payload": {"call_id": "r1", "success": True}})
    (tmp_path / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    rows = cr_demo.trajectory(tmp_path)
    assert [(r["step"], r["tool"], r["path"], r["success"]) for r in rows] == [(1, "read_file", "a.py", True), (2, "run_command", None, None)]
    q = SimpleNamespace(instance_id="i", repo="r", base_commit="b", problem_statement="see ```py\nx\n```")
    lines = cr_demo.issue_section(q)
    assert lines[6] == "````text" and lines[8] == "````"


# --- analysis definitions --------------------------------------------------------------------------

def _events(*calls):
    """calls: one list of (name, arguments, call_id) per model step."""
    events = []
    for step_calls in calls:
        events.append({"event_type": "model.started"})
        for name, arguments, call_id in step_calls:
            events.append({"event_type": "tool.intent", "tool_call_id": call_id,
                           "payload": {"name": name, "arguments": arguments}})
    return events


def _call(call_id, *paths):
    return {"call_id": call_id, "files": [{"path": p} for p in paths]}


def test_read_after_retrieval_within_two_steps():
    events = _events(
        [("retrieve_code", {"query": "q"}, "c1")],
        [("search_code", {"pattern": "x"}, "s1")],
        [("read_file", {"path": "/workspace/a.py"}, "r1")],
    )
    report = analyze_cr2e.retrieval_report(events, [_call("c1", "a.py", "b.py")], ["b.py"])
    assert report["called"] and report["calls"] == 1 and report["first_call_step"] == 1
    assert report["read_after_retrieval"] and report["call_rows"][0]["read_within_2"] == ["a.py"]
    assert report["gold_in_any_result"] and report["call_rows"][0]["k"] == 5
    assert report["search_code_calls"] == 1


def test_read_three_steps_later_or_same_step_does_not_count():
    events = _events(
        [("retrieve_code", {"query": "q", "k": 3}, "c1"), ("read_file", {"path": "a.py"}, "r0")],
        [("search_code", {"pattern": "x"}, "s1")],
        [("list_dir", {"path": "."}, "l1")],
        [("read_file", {"path": "a.py"}, "r1")],
    )
    report = analyze_cr2e.retrieval_report(events, [_call("c1", "a.py")], ["z.py"])
    assert not report["read_after_retrieval"] and not report["gold_in_any_result"]
    assert report["call_rows"][0]["k"] == 3


def test_repeats_same_query_or_back_to_back_but_not_parallel_distinct():
    events = _events(
        [("retrieve_code", {"query": "a b"}, "c1"), ("retrieve_code", {"query": "other"}, "c2")],
        [("retrieve_code", {"query": "third"}, "c3")],
        [("read_file", {"path": "x.py"}, "r1")],
        [("retrieve_code", {"query": "A  B"}, "c4")],
        [("read_file", {"path": "y.py"}, "r2")],
        [("retrieve_code", {"query": "new"}, "c5")],
    )
    rows = analyze_cr2e.retrieval_report(events, [], [])["call_rows"]
    assert [r["repeated_without_new_info"] for r in rows] == [False, False, True, True, False]


def test_no_retrieval_run():
    report = analyze_cr2e.retrieval_report(_events([("read_file", {"path": "a.py"}, "r1")]), [], ["a.py"])
    assert not report["called"] and report["first_call_step"] is None and not report["read_after_retrieval"]


def test_threshold():
    run = lambda called, read: {"called": called, "read_after_retrieval": read}  # noqa: E731
    ok = [run(True, True)] * 3 + [run(True, False)] * 3 + [run(False, False)] * 6
    assert analyze_cr2e.threshold(ok)["pass"]
    few = [run(True, True)] * 5 + [run(False, False)] * 7
    assert not analyze_cr2e.threshold(few)["pass"] and not analyze_cr2e.threshold(few)["calls_ok"]
    unread = [run(True, True)] * 2 + [run(True, False)] * 5 + [run(False, False)] * 5
    assert not analyze_cr2e.threshold(unread)["pass"] and not analyze_cr2e.threshold(unread)["reads_ok"]
    assert not analyze_cr2e.threshold([run(False, False)] * 12)["pass"]
