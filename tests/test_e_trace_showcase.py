from scripts.analysis.e_trace_showcase import render_markdown, select_task, task_rows


def _row(task, group, repeat, **extra):
    return {
        "task": task,
        "group": group,
        "repeat": repeat,
        "status": "fail",
        "solved": False,
        "valid": True,
        "located_request": 3,
        "first_edit_request": 8,
        "edit_calls": 2,
        "tail_repeat": 0.1,
        "requests": 10,
        "cost_cny": 0.1,
        **extra,
    }


def test_selects_complete_task_and_preserves_group_rows():
    rows = [_row("b", group, "r1") for group in ("U-new", "N", "E-new")]
    rows += [_row("a", "U-new", "r1")]
    assert select_task(rows) == "b"
    grouped = task_rows(rows, "b")
    assert [len(grouped[group]) for group in ("U-new", "N", "E-new")] == [1, 1, 1]


def test_render_marks_summary_as_proxy_not_full_trace():
    rows = [_row("task-1", group, repeat) for group in ("U-new", "N", "E-new") for repeat in ("r1", "r2")]
    report = render_markdown("fixture.json", "task-1", task_rows(rows, "task-1"))
    assert report.count("| U-new |") == 2
    assert "不是完整的逐步对话回放" in report
    assert "首次编辑请求" in report
