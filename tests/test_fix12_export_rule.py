"""Opt-in export rule: files of the original source tree are never skipped; new build artifacts still are."""
import io
import json
from contextlib import redirect_stdout

import pytest

from src.config.schema import CommandConfig
from src.eval.image_grader import ORIGINALS_AMONG, grader_payload
from src.execution.baseline_image_backend import export_script, is_export_skipped


def run_script(code: str, root, work, extra=None) -> dict:
    code = code.replace('pathlib.Path("/opt/repo")', f"pathlib.Path({str(root)!r})")
    code = code.replace('pathlib.Path("/workspace")', f"pathlib.Path({str(work)!r})")
    code = code.replace('pathlib.Path("/opt/repo-extra-files.json")', f"pathlib.Path({str(extra)!r})")
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        exec(code, {})
    return json.loads(buffer.getvalue())


@pytest.fixture
def trees(tmp_path):
    root, work = tmp_path / "repo", tmp_path / "work"
    for base in (root, work):
        (base / "src" / "build").mkdir(parents=True)
        (base / "build" / "lib").mkdir(parents=True)
    (root / "src" / "build" / "_util.py").write_text("old", encoding="utf-8")
    (work / "src" / "build" / "_util.py").write_text("new", encoding="utf-8")
    (root / "src" / ".mypy_cache").mkdir()
    (work / "src" / ".mypy_cache").mkdir()
    (root / "src" / ".mypy_cache" / "original.py").write_text("old", encoding="utf-8")
    (work / "src" / ".mypy_cache" / "original.py").write_text("new", encoding="utf-8")
    (root / "src" / "build" / "gone.py").write_text("x", encoding="utf-8")
    (root / "build" / "lib" / "copy.py").write_text("generated", encoding="utf-8")
    (work / "build" / "lib" / "copy.py").write_text("regenerated", encoding="utf-8")
    (work / "build" / "lib" / "fresh.py").write_text("artifact", encoding="utf-8")
    (work / "src" / "build" / "__pycache__").mkdir()
    (work / "src" / "build" / "__pycache__" / "_util.cpython-312.pyc").write_bytes(b"pyc")
    extra = tmp_path / "extra.json"
    extra.write_text(json.dumps(["build/lib/copy.py"]), encoding="utf-8")
    return root, work, extra


def test_host_rule_is_unchanged_by_default():
    assert is_export_skipped("src/build/_util.py") is True
    assert is_export_skipped("pkg/mod.py") is False


def test_host_rule_keeps_listed_original_files_only():
    originals = {"src/build/_util.py"}
    assert is_export_skipped("src/build/_util.py", originals) is False
    assert is_export_skipped("src/.mypy_cache/original.py") is True
    assert is_export_skipped("src/.mypy_cache/original.py", {"src/.mypy_cache/original.py"}) is False
    assert is_export_skipped("build/lib/fresh.py", originals) is True
    assert is_export_skipped("src/build/__pycache__/_util.cpython-312.pyc", originals) is True


def test_default_export_still_drops_build_paths(trees):
    root, work, extra = trees
    delta = run_script(export_script("/opt/repo", keep_original_files=False), root, work, extra)
    assert delta["changed"] == {} and delta["deleted"] == []


def test_opt_in_export_keeps_original_files_and_drops_new_artifacts(trees):
    root, work, extra = trees
    delta = run_script(export_script("/opt/repo", keep_original_files=True), root, work, extra)
    assert set(delta["changed"]) == {"src/build/_util.py", "src/.mypy_cache/original.py"}
    assert delta["deleted"] == ["src/build/gone.py"]


def test_opt_in_export_refuses_an_image_without_the_extra_file_list(trees, tmp_path):
    root, work, _ = trees
    with pytest.raises(FileNotFoundError):
        run_script(export_script("/opt/repo", keep_original_files=True), root, work, tmp_path / "missing.json")


def test_container_original_check_matches_the_export(trees):
    root, _, extra = trees
    code = ORIGINALS_AMONG.replace('pathlib.Path("/opt/repo")', f"pathlib.Path({str(root)!r})")
    code = code.replace('pathlib.Path("/opt/repo-extra-files.json")', f"pathlib.Path({str(extra)!r})")
    import sys
    stdin, sys.stdin = sys.stdin, io.StringIO(json.dumps(["src/build/_util.py", "build/lib/copy.py", "build/lib/fresh.py"]))
    try:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            exec(code, {})
    finally:
        sys.stdin = stdin
    assert json.loads(buffer.getvalue()) == ["src/build/_util.py"]


def test_grader_payload_filters_with_the_same_rule():
    delta = {"changed": {"src/build/_util.py": "bmV3", "build/lib/fresh.py": "YQ=="}, "deleted": ["src/build/gone.py"]}
    assert grader_payload(delta, {}) == {"deleted": [], "changed": {}, "overlay": {}}
    kept = grader_payload(delta, {}, original_files={"src/build/_util.py", "src/build/gone.py"})
    assert kept == {"deleted": ["src/build/gone.py"], "changed": {"src/build/_util.py": "bmV3"}, "overlay": {}}


def test_config_option_defaults_off_and_stays_out_of_the_dump():
    assert CommandConfig().export_keep_original_files is False
    assert "export_keep_original_files" not in CommandConfig().model_dump()
    assert CommandConfig(export_keep_original_files=True).model_dump()["export_keep_original_files"] is True


def test_runner_requires_the_profile_to_match_the_calibration():
    from scripts.run_swe_task import export_rule

    assert export_rule({"policy": {"command": {}}}, {}) is False
    on = {"policy": {"command": {"export_keep_original_files": True}}}
    assert export_rule(on, {"export_keep_original_files": True}) is True
    with pytest.raises(ValueError):
        export_rule(on, {})
    with pytest.raises(ValueError):
        export_rule({"policy": {"command": {}}}, {"export_keep_original_files": True})
