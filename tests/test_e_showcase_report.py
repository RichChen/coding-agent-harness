from scripts.analysis.e_showcase_report import render_markdown, summarize


def _row(group, solved=False, status="fail", cls="edited_elsewhere", edit_calls=1, repeat=0.1):
    return {
        "group": group,
        "solved": solved,
        "status": status,
        "cls": cls,
        "edit_calls": edit_calls,
        "tail_repeat": repeat,
        "requests": 10,
        "cost_cny": 0.1,
        "valid": True,
    }


def test_summarize_preserves_behavior_counts_and_ignores_invalid_behavior_row():
    rows = [
        _row("U-new", cls="found_idle", edit_calls=0, repeat=1.0),
        _row("N", solved=True, status="pass"),
        _row("E-new", cls="found_idle", edit_calls=0),
        {**_row("N", cls="found_idle", edit_calls=0), "valid": False},
    ]
    summary = summarize(rows)
    assert summary["U-new"]["found_idle"] == 1
    assert summary["U-new"]["no_edit"] == 1
    assert summary["N"]["solved"] == 1
    assert summary["N"]["valid_runs"] == 1
    assert summary["E-new"]["edit_rate"] == 0


def test_render_mentions_evidence_boundary():
    rows = [_row(group) for group in ("U-new", "N", "E-new")]
    report = render_markdown("fixture.json", summarize(rows))
    assert "不能据此声称 E 已经稳定提高普遍解出率" in report
    assert "找到后不编辑" in report
