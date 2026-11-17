import io
import json
from contextlib import redirect_stdout

from src.execution.baseline_image_backend import EXPORT_DELTA, is_export_skipped


def test_source_files_are_not_skipped():
    assert is_export_skipped("sklearn/linear_model/ridge.py") is False
    assert is_export_skipped("src/app.c") is False


def test_compiled_artifacts_and_build_dirs_are_skipped():
    assert is_export_skipped("sklearn/__check_build/_check_build.cpython-39-x86_64-linux-gnu.so")
    assert is_export_skipped("mod.pyd")
    assert is_export_skipped("mod.o")
    assert is_export_skipped("mod.dylib")
    assert is_export_skipped("build/temp/ext.so")
    assert is_export_skipped("pkg/__pycache__/a.pyc")
    assert is_export_skipped("pkg/.mypy_cache/3.13/a.data.json")
    assert is_export_skipped("pkg/.ruff_cache/0.8.0/content")


def test_export_script_keeps_source_and_omits_linked_extensions(tmp_path):
    root = tmp_path / "repo"
    work = tmp_path / "work"
    (root / "pkg").mkdir(parents=True)
    (work / "pkg").mkdir(parents=True)
    (root / "pkg" / "ridge.py").write_text("old", encoding="utf-8")
    (work / "pkg" / "ridge.py").write_text("new", encoding="utf-8")
    (root / "pkg" / "ext.so").write_bytes(b"native")
    (root / "build").mkdir()
    (root / "build" / "out.o").write_bytes(b"o")
    (root / "pkg" / ".mypy_cache").mkdir()
    (root / "pkg" / ".mypy_cache" / "state.json").write_text("old", encoding="utf-8")
    (work / "pkg" / ".mypy_cache").mkdir()
    (work / "pkg" / ".mypy_cache" / "state.json").write_text("new", encoding="utf-8")
    (root / "pkg" / ".ruff_cache").mkdir()
    (root / "pkg" / ".ruff_cache" / "content").write_text("old", encoding="utf-8")
    code = EXPORT_DELTA.replace('pathlib.Path("/opt/repo")', f"pathlib.Path({str(root)!r})")
    code = code.replace('pathlib.Path("/workspace")', f"pathlib.Path({str(work)!r})")
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        exec(code, {})
    delta = json.loads(buffer.getvalue())
    assert delta["changed"] and "pkg/ridge.py" in delta["changed"]
    assert "pkg/ext.so" not in delta["changed"]
    assert "pkg/ext.so" not in delta["deleted"]
    assert "build/out.o" not in delta["deleted"]
    assert "pkg/.mypy_cache/state.json" not in delta["changed"]
    assert "pkg/.mypy_cache/state.json" not in delta["deleted"]
    assert "pkg/.ruff_cache/content" not in delta["deleted"]
    assert delta["skipped_files"] >= 2
