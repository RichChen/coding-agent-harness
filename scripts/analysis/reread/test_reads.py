"""Parser checks for reads.py. Run: python -m pytest scripts/analysis/reread/test_reads.py -q"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reads import Unit, call_io, run_command_io  # noqa: E402


def units(command: str) -> list[Unit]:
    return run_command_io(command).units


def test_sed_range_with_cd():
    assert units("cd /workspace && sed -n 225,258p sklearn/metrics/pairwise.py") == [
        Unit("lines", "sklearn/metrics/pairwise.py", 225, 258, source="sed")]


def test_sed_two_files_and_grep_in_one_command():
    got = units("cd /workspace && sed -n 536,575p sklearn/cluster/k_means_.py; "
                "grep -n \"def _centers_dense\" -A 40 sklearn/cluster/k_means_.py")
    assert got == [Unit("lines", "sklearn/cluster/k_means_.py", 536, 575, source="sed"),
                   Unit("search", "sklearn/cluster/k_means_.py", pattern="def _centers_dense", source="grep")]


def test_grep_recursive_dir_and_head_filter():
    assert units('grep -rn "note_object\\|duplicate %s description" sphinx/ | head -30') == [
        Unit("search", "sphinx", pattern="note_object|duplicate %s description", source="grep")]


def test_extended_grep_keeps_escaped_bar():
    assert units('grep -nE "a\\|b" x.py') == [Unit("search", "x.py", pattern="a\\|b", source="grep")]


def test_grep_glob_path_resolved_against_cwd():
    assert units("cd /tmp/repro && grep -c fill _build/_images/*.svg") == [
        Unit("search", "/tmp/repro/_build/_images/*.svg", pattern="fill", source="grep")]


def test_ran_flag():
    assert run_command_io("cd /workspace && python -m pytest -q tests/x.py").ran
    assert run_command_io("cd /tmp/r && sphinx-build -b html . _build").ran
    assert not run_command_io("grep -n x a.py; ls").ran


def test_grep_redirect_is_not_a_path():
    assert units('grep -n "x" a.py 2>/dev/null') == [Unit("search", "a.py", pattern="x", source="grep")]


def test_grep_after_cd_subdir():
    assert units("cd /workspace/sphinx && grep -n foo ext/viewcode.py") == [
        Unit("search", "sphinx/ext/viewcode.py", pattern="foo", source="grep")]


def test_cat_head_tail():
    assert units("cat sphinx/ext/viewcode.py") == [Unit("lines", "sphinx/ext/viewcode.py", 1, None, source="cat")]
    assert units("head -n 50 a.py") == [Unit("lines", "a.py", 1, 50, source="head")]
    assert units("cat -n a.py | sed -n '10,20p'") == [Unit("lines", "a.py", 10, 20, source="sed")]
    assert units("nl -ba a.py | sed -n 10,+5p") == [Unit("lines", "a.py", 10, 15, source="sed")]
    assert units("tail -20 a.py") == [Unit("tail", "a.py", n=20, source="tail")]


def test_cat_grep_becomes_search():
    assert units("cat a.py | grep -n foo") == [Unit("search", "a.py", pattern="foo", source="grep")]


def test_not_reads():
    assert units("python -m pytest tests/test_x.py -q 2>&1 | tail -20") == []
    assert units("cd /workspace && git log --oneline -5") == []
    assert units("ls sklearn/cluster/") == []


def test_heredoc_write():
    io = run_command_io("cd /workspace && cat > /tmp/repro.py <<'EOF'\nimport x\nprint(1)\nEOF\npython /tmp/repro.py")
    assert io.units == [] and io.writes == {"/tmp/repro.py"}


def test_python_heredoc_write_path():
    io = run_command_io("python - <<'EOF'\np = 'sympy/polys/densetools.py'\ns = open(p).read()\n"
                        "open(p, 'w').write(s)\nEOF")
    assert io.writes == {"sympy/polys/densetools.py"} and not io.unknown_write


def test_sed_in_place_and_git_checkout():
    assert run_command_io("sed -i 's/a/b/' x/y.py").writes == {"x/y.py"}
    assert run_command_io("git checkout -- x/y.py").writes == {"x/y.py"}
    assert run_command_io("git stash").unknown_write


def test_multiline_python_c_is_one_command():
    io = run_command_io('cd /workspace && python -c "\nimport sys\nprint(1)\n" 2>&1; sed -n 1,5p a.py')
    assert io.units == [Unit("lines", "a.py", 1, 5, source="sed")]


def test_read_file_uses_output_range():
    out = "101|x\n102|y\n[showing lines 101-102 of 300; call read_file with offset=103 to continue]"
    io = call_io("read_file", {"path": "a.py", "offset": 101, "limit": 50}, out, True)
    assert io.units == [Unit("lines", "a.py", 101, 102, source="read_file")] and io.file_lengths == {"a.py": 300}
    io = call_io("read_file", {"path": "/workspace/a.py"}, "1|x\n2|y", True)
    assert io.units == [Unit("lines", "a.py", 1, 2, source="read_file")]
    assert call_io("read_file", {"path": "a.py"}, "", False).units == []


def test_search_code_key():
    assert call_io("search_code", {"pattern": "def f", "path": "sphinx/"}, "", True).units == [
        Unit("search", "sphinx", pattern="def f", source="search_code")]


def test_touches_directories_globs_and_runs():
    from reread import RAN, touches
    assert touches("/tmp/repro", Unit("lines", "/tmp/repro/a.py", 1, 5))
    assert not touches("/tmp/repro2", Unit("lines", "/tmp/repro/a.py", 1, 5))
    glob = Unit("search", "/tmp/repro/_build/_images/*.svg", pattern="fill")
    assert touches("/tmp/repro/_build/_images/x.svg", glob) and touches(RAN, glob)
    assert touches(RAN, Unit("lines", "docs/_build/index.html", 1, None))
    assert not touches(RAN, Unit("lines", "sphinx/ext/viewcode.py", 1, None))


def test_paging_overlap_below_floor():
    from reread import MIN_OVERLAP, coverage
    page = Unit("lines", "a.py", 758, 797)
    assert coverage(page, [Unit("lines", "a.py", 729, 758)], {}) < MIN_OVERLAP
    assert coverage(page, [Unit("lines", "a.py", 740, 779)], {}) >= MIN_OVERLAP
    assert coverage(Unit("lines", "a.py", 91, 100), [Unit("tail", "a.py", n=20)], {"a.py": 100}) == 1.0
