"""CR-2 analysis step rules on hand-built event sequences."""
import json

import pytest

from scripts import analyze_cr2 as an


def started():
    return {"event_type": "model.started", "payload": {}}


def intent(name, **arguments):
    return {"event_type": "tool.intent", "payload": {"name": name, "arguments": arguments}}


def result():
    return {"event_type": "tool.result", "payload": {}}


GOLD = ("pkg/core.py",)


def test_normalize_path():
    assert an.normalize_path("./pkg/core.py") == "pkg/core.py"
    assert an.normalize_path("pkg\\core.py") == "pkg/core.py"
    assert an.normalize_path("/workspace/pkg/./core.py") == "pkg/core.py"
    assert an.normalize_path("pkg/sub/../core.py") == "pkg/core.py"
    assert an.normalize_path(".") == "" and an.normalize_path(None) == ""


def test_first_touch_counts_model_calls_from_one():
    events = [started(), intent("list_dir", path="."), result(),
              started(), intent("read_file", path="pkg/other.py"), result(),
              started(), intent("read_file", path="./pkg/core.py"), result()]
    assert an.first_gold_step(events, GOLD) == 3


def test_first_touch_on_first_call_and_parallel_calls():
    events = [started(), intent("search_code", pattern="core"), intent("read_file", path="pkg/core.py")]
    assert an.first_gold_step(events, GOLD) == 1


def test_only_touch_tools_count():
    events = [started(), intent("search_code", path="pkg/core.py"), started(), intent("run_command", command="cat pkg/core.py"),
              started(), intent("retrieve_code", query="pkg/core.py"), started(), intent("edit_file", path="pkg/core.py")]
    assert an.first_gold_step(events, GOLD) == 4
    for tool in ("write_file", "search_replace", "edit_file", "read_file"):
        assert an.first_gold_step([started(), intent(tool, path="pkg/core.py")], GOLD) == 1


def test_never_touched_is_41_and_retries_count_as_calls():
    assert an.first_gold_step([started(), intent("read_file", path="x.py")], GOLD) == an.NEVER == 41
    retried = [started(), started(), intent("read_file", path="pkg/core.py")]
    assert an.first_gold_step(retried, GOLD) == 2


def test_failed_intent_still_counts():
    events = [started(), intent("read_file", path="pkg/core.py"), {"event_type": "tool.result", "payload": {"success": False}}]
    assert an.first_gold_step(events, GOLD) == 1


def test_retrieve_stats():
    events = [started(), intent("list_dir", path="."), started(), intent("retrieve_code", query="a"),
              started(), intent("retrieve_code", query="b", k=3)]
    calls = [{"call_id": "1", "files": [{"path": "x.py", "chunks": []}]},
             {"call_id": "2", "files": [{"path": "pkg/core.py", "chunks": []}]}]
    stats = an.retrieve_stats(events, calls, GOLD)
    assert stats == {"retrieve_calls": 2, "first_retrieve_step": 2, "gold_in_retrieval": True, "retrieval_log_rows": 2}
    none = an.retrieve_stats([started()], [], GOLD)
    assert none["retrieve_calls"] == 0 and none["first_retrieve_step"] is None and not none["gold_in_retrieval"]


def test_run_metrics_and_early_flag():
    row = {"task_id": "t1", "group": "n", "k": 1, "solved": True, "model_calls": 12, "cost_cny": "0.5", "stop_reason": "completed"}
    events = [started()] * 10 + [intent("read_file", path="pkg/core.py")]
    m = an.run_metrics(row, events, GOLD, first_message="héllo")
    assert m["first_gold_step"] == 10 and m["gold_within_10"] is True and m["first_message_bytes"] == 6
    events = [started()] * 11 + [intent("read_file", path="pkg/core.py")]
    assert an.run_metrics(row, events, GOLD)["gold_within_10"] is False


def test_task_means_and_paired_difference():
    rows = [
        {"task_id": "a", "group": "n", "first_gold_step": 10}, {"task_id": "a", "group": "n", "first_gold_step": 20},
        {"task_id": "a", "group": "p", "first_gold_step": 5}, {"task_id": "a", "group": "p", "first_gold_step": 5},
        {"task_id": "b", "group": "n", "first_gold_step": 41}, {"task_id": "b", "group": "n", "first_gold_step": 41},
        {"task_id": "b", "group": "p", "first_gold_step": 31}, {"task_id": "b", "group": "p", "first_gold_step": 41},
    ]
    means = an.task_means(rows, "first_gold_step")
    assert means == {"a": {"n": 15.0, "p": 5.0}, "b": {"n": 41.0, "p": 36.0}}
    out = an.paired(means, "p", "n")
    assert out["n_tasks"] == 2 and out["diff"] == pytest.approx(-7.5) and out["lo"] <= -7.5 <= out["hi"]
    assert an.paired(means, "t", "n")["n_tasks"] == 0


def test_analyze_end_to_end(tmp_path):
    def run_dir(task, group, k, events, calls=None):
        d = tmp_path / task / group / f"r{k}"
        d.mkdir(parents=True)
        (d / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
        if calls is not None:
            (d / an.CALLS_FILE).write_text("".join(json.dumps(c) + "\n" for c in calls), encoding="utf-8")
        return d.relative_to(tmp_path).as_posix()

    runs = []
    for task in ("a", "b"):
        for k in (1, 2):
            base = {"task_id": task, "k": k, "solved": False, "model_calls": 5, "cost_cny": "0.1", "stop_reason": "completed"}
            runs.append({**base, "group": "n", "run_dir": run_dir(task, "n", k, [started()] * 4 + [intent("read_file", path="pkg/core.py")])})
            runs.append({**base, "group": "p", "run_dir": run_dir(task, "p", k, [started(), intent("read_file", path="pkg/core.py")])})
            runs.append({**base, "group": "t", "run_dir": run_dir(task, "t", k, [started(), intent("retrieve_code", query="q"),
                                                                                  started(), intent("read_file", path="pkg/core.py")],
                                                                     [{"call_id": "1", "files": [{"path": "pkg/core.py"}]}])})
            gold_path = "pkg/core.py" if task == "a" else "other.py"
            runs.append({**base, "group": "o", "solved": True,
                         "run_dir": run_dir(task, "o", k, [started(), intent("read_file", path=gold_path)])})
    gold = {"a": GOLD, "b": ("other.py",)}
    prefetch = {"a": {"files": [{"path": "pkg/core.py"}]}, "b": {"files": [{"path": "pkg/core.py"}]}}
    assert not an.analyze(runs, gold, prefetch, root=tmp_path)["complete"]
    report = an.analyze(runs, gold, prefetch, root=tmp_path, repeats=2)
    assert report["complete"] and report["runs"] == 16
    assert report["group_means"]["first_gold_step"]["o"] == 1
    assert report["paired"]["first_gold_step"]["o_minus_n"]["diff"] == pytest.approx(1 - (4 + 41) / 2)
    assert report["paired"]["solved"]["o_minus_n"]["diff"] == pytest.approx(1.0)
    assert report["paired"]["solved"]["p_minus_n"]["diff"] == pytest.approx(0.0)
    early = report["solve_by_early_touch"]
    assert early["o"]["touched_within_10"] == {"runs": 4, "solved": 4, "rate": 1.0}
    assert early["o"]["not_touched_within_10"] == {"runs": 0, "solved": 0, "rate": None}
    assert early["n"]["touched_within_10"]["runs"] == 2 and early["n"]["not_touched_within_10"]["rate"] == 0.0
    assert report["group_means"]["first_gold_step"] == {"n": (4 + 41) / 2, "p": (1 + 41) / 2, "t": (2 + 41) / 2, "o": 1}
    assert report["paired"]["first_gold_step"]["p_minus_n"]["diff"] == pytest.approx(-1.5)
    assert report["t_only"]["share_with_call"] == 1.0 and report["t_only"]["first_call_step_mean"] == 1
    assert report["t_only"]["share_gold_in_any_result"] == 0.5
    split = report["p_only_first_gold_step"]
    assert split["gold_in_top5"]["tasks_in_pool"] == 1 and split["gold_in_top5"]["p_minus_n"]["diff"] == pytest.approx(-3)
    assert split["gold_not_in_top5"]["p_mean"] == 41
    assert report["stop_reasons"]["n"] == {"completed": 4}
    assert report["delta_too_large_by_group"] == {"n": 0, "p": 0, "t": 0, "o": 0}

    # r3: a delta_too_large run keeps its step metrics, counts as unsolved even if a summary said solved, and is
    # reported per group.
    runs[-1] = {**runs[-1], "status": "delta_too_large", "solved": True, "stop_reason": "candidate_snapshot_failed"}
    report = an.analyze(runs, gold, prefetch, root=tmp_path, repeats=2)
    assert report["complete"] and report["runs"] == 16
    assert report["delta_too_large_by_group"] == {"n": 0, "p": 0, "t": 0, "o": 1}
    assert report["group_means"]["first_gold_step"]["o"] == 1
    assert report["solve_by_early_touch"]["o"]["touched_within_10"] == {"runs": 4, "solved": 3, "rate": 0.75}
    assert report["group_means"]["solved"]["o"] == pytest.approx((1.0 + 0.5) / 2)
