import json

from src.eval.unified import load_run_rows, normalize_run_rows, summarize_receipts


def test_explicit_legacy_export_is_read_only_and_summarized(tmp_path):
    path = tmp_path / "runs.json"
    path.write_text(json.dumps([
        {"task": "org__repo-1", "group": "U", "repeat": "r1", "solved": True,
         "cost_cny": 0.2, "model_calls": 4},
        {"task": "org__repo-1", "group": "U", "repeat": "r2", "status": "fail",
         "cost_cny": 0.4, "model_calls": 8},
        {"task": "org__repo-1", "group": "E", "repeat": "r1", "status": "partial",
         "cost_cny": 0.1, "model_calls": 2},
    ]), encoding="utf-8")
    rows = load_run_rows(path)
    receipts = normalize_run_rows(rows, adapter="swebench", grader_kind="image_grader")
    assert len(receipts) == 3
    assert receipts[0].status == "pass"
    assert receipts[0].raw_receipt["cost_cny"] == 0.2
    assert summarize_receipts(receipts) == [
        {"group": "E", "runs": 1, "pass": 0, "partial": 1, "invalid_or_error": 0,
         "mean_cost_cny": 0.1, "mean_model_calls": 2.0},
        {"group": "U", "runs": 2, "pass": 1, "partial": 0, "invalid_or_error": 0,
         "mean_cost_cny": 0.30000000000000004, "mean_model_calls": 6.0},
    ]


def test_rows_wrapper_is_explicit(tmp_path):
    path = tmp_path / "wrapped.json"
    path.write_text(json.dumps({"rows": [{"task_id": "mini-1", "status": "fail"}]}), encoding="utf-8")
    assert len(load_run_rows(path)) == 1
