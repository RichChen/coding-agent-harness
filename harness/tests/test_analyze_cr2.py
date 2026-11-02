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


# --- r4 (contract 10.1, 10.2) ----------------------------------------------------------------------

def test_infra_kept_list_sources(tmp_path):
    report = tmp_path / "main-report.json"
    report.write_text(json.dumps({"infra_kept": ["runs/cr2-main/a/n/r1"]}), encoding="utf-8")
    assert an.infra_kept_runs({"infra_kept": ["runs\\cr2-main\\b\\t\\r2"]}, report) == {"runs/cr2-main/b/t/r2"}
    assert an.infra_kept_runs({}, report) == {"runs/cr2-main/a/n/r1"}
    assert an.infra_kept_runs({}) == set()


def test_only_kept_reruns_pass():
    runs = [{"run_dir": "runs/x/a/n/r1", "rerun_reason": "provider network None"},
            {"run_dir": "runs/x/a/n/r2", "rerun_reason": None},
            {"run_dir": "runs/x/a/p/r1", "rerun_reason": "infra_error"}]
    assert an.blocking_reruns(runs, set()) == ["runs/x/a/n/r1", "runs/x/a/p/r1"]
    assert an.blocking_reruns(runs, {"runs/x/a/n/r1"}) == ["runs/x/a/p/r1"]
    assert an.blocking_reruns(runs, {"runs/x/a/n/r1", "runs/x/a/p/r1"}) == []


def test_main_rejects_unkept_rerun_and_accepts_kept(tmp_path, monkeypatch):
    runs_file = tmp_path / "runs.json"
    runs_file.write_text(json.dumps({"runs": [{"run_dir": "runs/x/a/n/r1", "rerun_reason": "provider network None"}]}),
                         encoding="utf-8")
    prefetch = tmp_path / "prefetch.json"
    prefetch.write_text(json.dumps({"tasks": []}), encoding="utf-8")
    argv = ["analyze_cr2.py", "--runs", str(runs_file), "--prefetch", str(prefetch), "--out", str(tmp_path / "out.json")]
    monkeypatch.setattr("sys.argv", argv)
    with pytest.raises(SystemExit, match="runs/x/a/n/r1"):
        an.main()
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"infra_kept": ["runs/x/a/n/r1"]}), encoding="utf-8")
    reached = {}

    def fake_analyze(runs, gold, prefetch_rows, repeats, infra_kept):
        reached["kept"] = infra_kept
        return {"per_run": []}

    monkeypatch.setattr(an, "load_gold", lambda ids: {})
    monkeypatch.setattr(an, "analyze", fake_analyze)
    monkeypatch.setattr("sys.argv", argv + ["--report", str(report)])
    an.main()
    assert reached["kept"] == {"runs/x/a/n/r1"}


def test_attempt_dirs_order(tmp_path):
    slot = tmp_path / "a" / "n"
    for name in ("r1", "r1.infra2", "r1.infra1", "r1.infra10", "r10", "r1.infra1.bak"):
        (slot / name).mkdir(parents=True)
    assert [p.name for p in an.attempt_dirs(slot / "r1")] == ["r1.infra1", "r1.infra2", "r1.infra10", "r1"]
    assert [p.name for p in an.attempt_dirs(slot / "r10")] == ["r10"]


def _write_run(directory, summary, events):
    directory.mkdir(parents=True)
    (directory / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (directory / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")


def test_s4_first_non_infra_attempt(tmp_path):
    touch_at = lambda step: [started()] * step + [intent("read_file", path="pkg/core.py")]
    fail = {"status": "fail", "solved": False, "model_calls": 40, "cost_cny": "0.5", "stop_reason": "max_model_calls"}
    infra = {"status": "infra_error", "solved": False, "snapshot_error": None,
             "infra_reason": "candidate snapshot failed: container_gone_before_snapshot"}
    delta = {"status": "infra_error", "solved": True, "snapshot_error": "ValueError: delta_too_large",
             "infra_reason": "candidate snapshot failed: ValueError: delta_too_large", "model_calls": 7}
    passed = {"status": "pass", "solved": True, "model_calls": 9, "cost_cny": "0.1", "stop_reason": "completed"}
    root = tmp_path
    # a/n/r1: first attempt retired for a network failure but finished normally -> S4 takes it.
    _write_run(root / "a/n/r1.infra1", fail, touch_at(30))
    _write_run(root / "a/n/r1", passed, touch_at(2))
    # a/p/r1: Docker infra first, then a normal attempt -> the second one.
    _write_run(root / "a/p/r1.infra1", infra, touch_at(1))
    _write_run(root / "a/p/r1.infra2", fail, touch_at(41)[:-1])
    _write_run(root / "a/p/r1", passed, touch_at(3))
    # a/t/r1: a retired delta_too_large attempt is a task result (counted_status), unsolved.
    _write_run(root / "a/t/r1.infra1", delta, touch_at(5))
    _write_run(root / "a/t/r1", passed, touch_at(4))
    # a/o/r1: no retired attempt -> the final run itself.
    _write_run(root / "a/o/r1", passed, touch_at(1))
    runs = [{"task_id": "a", "group": g, "k": 1, "run_dir": f"a/{g}/r1", **passed} for g in an.GROUPS]
    per_run = [an.run_metrics(row, an._read_jsonl(root / row["run_dir"] / "events.jsonl"), GOLD, []) for row in runs]
    chosen, info = an.first_non_infra_runs(runs, per_run, {"a": GOLD}, root)
    by_group = {r["group"]: r for r in chosen}
    assert by_group["n"]["run_dir"] == "a/n/r1.infra1" and by_group["n"]["first_gold_step"] == 30
    assert by_group["n"]["solved"] is False
    assert by_group["p"]["run_dir"] == "a/p/r1.infra2" and by_group["p"]["first_gold_step"] == 41
    assert by_group["t"]["run_dir"] == "a/t/r1.infra1" and by_group["t"]["first_gold_step"] == 5
    assert by_group["t"]["solved"] is False and by_group["t"]["delta_too_large"] is True
    assert by_group["o"] is per_run[3]
    assert info == {"slots_from_retired_run": {"n": 1, "p": 1, "t": 1, "o": 0}, "slots_without_non_infra_run": []}


def test_s4_drops_slot_without_non_infra_attempt(tmp_path):
    infra = {"status": "infra_error", "solved": False, "snapshot_error": None, "infra_reason": "container cleanup failed"}
    _write_run(tmp_path / "a/n/r1.infra1", infra, [started()])
    _write_run(tmp_path / "a/n/r1", infra, [started()])
    runs = [{"task_id": "a", "group": "n", "k": 1, "run_dir": "a/n/r1", "status": "infra_error"}]
    per_run = [an.run_metrics(runs[0], [started()], GOLD)]
    chosen, info = an.first_non_infra_runs(runs, per_run, {"a": GOLD}, tmp_path)
    assert chosen == [] and info["slots_without_non_infra_run"] == ["a/n/r1"]


def test_compare_drops_tasks_without_runs_in_a_group():
    rows = [{"task_id": "a", "group": "n", "first_gold_step": 10, "solved": False},
            {"task_id": "a", "group": "p", "first_gold_step": 4, "solved": True},
            {"task_id": "b", "group": "n", "first_gold_step": 20, "solved": False},
            {"task_id": "b", "group": "t", "first_gold_step": 41, "solved": False}]
    out = an.compare(rows)
    assert out["runs"] == 4 and out["tasks"] == 2 and out["runs_by_group"] == {"n": 2, "p": 1, "t": 1, "o": 0}
    p_minus_n = out["first_gold_step"]["paired"]["p_minus_n"]
    assert p_minus_n["n_tasks"] == 1 and p_minus_n["diff"] == pytest.approx(-6)
    assert out["first_gold_step"]["paired"]["t_minus_n"]["n_tasks"] == 1
    assert out["solved"]["paired"]["o_minus_n"]["n_tasks"] == 0
    assert out["solved"]["group_means"]["p"] == 1.0


def test_sensitivity_filters(tmp_path):
    fp_new, fp_old = an.S3_FINGERPRINT, "e72d3c7f663d3e1383b00b3884cb4501"
    runs, per_run = [], []
    for task in ("x", an.S2_TASK):
        for group in an.GROUPS:
            run_dir = f"{task}/{group}/r1"
            _write_run(tmp_path / run_dir, {"status": "fail"}, [started()])
            fps = [fp_new] if group != "t" else [fp_new, fp_old]
            kept = task == "x" and group == "p"
            runs.append({"task_id": task, "group": group, "k": 1, "run_dir": run_dir, "status": "fail"})
            per_run.append({"task_id": task, "group": group, "first_gold_step": 41, "solved": False,
                            "run_dir": run_dir, "infra_kept": kept, "system_fingerprints": fps})
    out = an.sensitivity(runs, per_run, {"x": GOLD, an.S2_TASK: GOLD}, tmp_path)
    s1 = out["s1_without_infra_kept"]
    assert s1["runs"] == 7 and s1["first_gold_step"]["paired"]["p_minus_n"]["n_tasks"] == 1
    s2 = out["s2_without_" + an.S2_TASK]
    assert s2["runs"] == 4 and s2["tasks"] == 1
    s3 = out["s3_only_fingerprint_" + an.S3_FINGERPRINT]
    assert s3["runs_by_group"] == {"n": 2, "p": 2, "t": 0, "o": 2}
    assert s3["first_gold_step"]["paired"]["t_minus_n"]["n_tasks"] == 0
    s4 = out["s4_first_non_infra_attempt"]
    assert s4["runs"] == 8 and s4["slots_from_retired_run"] == {"n": 0, "p": 0, "t": 0, "o": 0}


def test_analyze_marks_infra_kept_and_reports_sensitivity(tmp_path):
    d = tmp_path / "a" / "n" / "r1"
    d.mkdir(parents=True)
    (d / "events.jsonl").write_text(json.dumps(started()) + "\n", encoding="utf-8")
    runs = [{"task_id": "a", "group": "n", "k": 1, "run_dir": "a/n/r1", "solved": False, "status": "fail",
             "rerun_reason": "provider network None", "system_fingerprints": [an.S3_FINGERPRINT]}]
    report = an.analyze(runs, {"a": GOLD}, {}, root=tmp_path, repeats=1, infra_kept={"a/n/r1"})
    assert report["infra_kept"] == ["a/n/r1"] and report["per_run"][0]["infra_kept"] is True
    assert report["sensitivity"]["s1_without_infra_kept"]["runs"] == 0
    assert report["sensitivity"]["s3_only_fingerprint_" + an.S3_FINGERPRINT]["runs"] == 1
