import json

from scripts.describe_a6_stops import render


def _row(task, arm, first, k, stop, status, calls=40):
    return {
        "task_id": task,
        "repo": "demo/demo",
        "arm": arm,
        "first": first,
        "k": k,
        "run_dir": f"runs/a6-retry/{task}/{arm}/{first}/r{k}",
        "mock": False,
        "status": status,
        "solved": status == "pass",
        "model_calls": calls,
        "stop_reason": stop,
    }


def test_counts_by_arm_and_lists_parse_errors(tmp_path):
    rows = [
        _row("t", "r0", "b0r1", 1, "completed", "pass", 12),
        _row("t", "r0", "b0r1", 2, "max_model_calls", "fail"),
        _row("t", "r1", "b0r1", 1, "model_parse_error", "partial", 37),
        _row("t", "r1", "b0r1", 2, "context_overflow", "fail", 20),
    ]
    runs = tmp_path / "runs.json"
    runs.write_text(
        json.dumps({"schema": "a6.retry-runs.v1", "scope": "a6-retry", "runs": rows}),
        encoding="utf-8",
    )
    retired = tmp_path / "retired.json"
    retired.write_text(
        json.dumps([{"slot": "t/r1/b0r1/r1", "retired_to": ["r1.infra1"]}]), encoding="utf-8"
    )
    text = render(runs, retired)
    assert "| model_parse_error | 0 | 1 |" in text
    assert "| context_overflow | 0 | 1 |" in text
    assert "| 合计 | 2 | 2 |" in text
    assert "| t/r1/b0r1/r1 | R1 | 37 | partial |" in text
    assert "| t/r1/b0r1/r2 | R1 | 20 | fail |" in text
    assert "1 个槽位，R0 0 个，R1 1 个" in text


def _write(tmp_path, rows):
    runs = tmp_path / "runs.json"
    runs.write_text(
        json.dumps({"schema": "a6.retry-runs.v1", "scope": "a6-retry", "runs": rows}),
        encoding="utf-8",
    )
    return runs


def test_old_labels_get_no_extra_sections(tmp_path):
    rows = [
        _row("t", "r0", "b0r1", 1, "model_parse_error", "fail", 30),
        _row("t", "r1", "b0r1", 1, "completed", "pass", 12),
    ]
    text = render(_write(tmp_path, rows), None)
    assert text.count("## stop_reason = ") == 2
    assert "人民币上限停机" not in text and "服务商错误停机" not in text


def test_lists_new_failure_labels(tmp_path):
    rows = [
        _row("t", "r0", "b0r1", 1, "cny_hard_cap", "fail", 33),
        _row("t", "r1", "b0r1", 1, "budget_exhausted", "partial", 21),
        _row("t", "r1", "b0r1", 2, "provider_http_503", "fail", 5),
        _row("t", "r1", "b0r1", 3, "cny_call_cap", "fail", 40),
        _row("t", "r0", "b0r1", 2, "provider_network", "fail", 2),
    ]
    text = render(_write(tmp_path, rows), None)
    assert "## stop_reason = model_parse_error 的运行\n\n无" in text
    assert "## stop_reason = budget_exhausted 的运行（人民币上限停机）" in text
    assert "## stop_reason = cny_hard_cap 的运行（人民币上限停机）" in text
    assert "## stop_reason = cny_call_cap 的运行（人民币上限停机）" in text
    assert "## stop_reason = provider_http_503 的运行（服务商错误停机）" in text
    assert "## stop_reason = provider_network 的运行（服务商错误停机）" in text
    assert "| t/r0/b0r1/r1 | R0 | 33 | fail |" in text
    assert "| t/r1/b0r1/r1 | R1 | 21 | partial |" in text
    assert "| cny_hard_cap | 1 | 0 |" in text
