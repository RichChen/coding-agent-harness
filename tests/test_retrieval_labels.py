from src.retrieval.chunking import chunk_source
from src.retrieval.labels import build_label_view, changed_old_lines, parse_diff

BASE = """import os


def alpha():
    a = 1
    return a


class K:
    x = 1

    def beta(self):
        return 2
"""

DIFF = """diff --git a/pkg/mod.py b/pkg/mod.py
--- a/pkg/mod.py
+++ b/pkg/mod.py
@@ -4,3 +4,3 @@ import os
 def alpha():
-    a = 1
+    a = 2
     return a
@@ -12,2 +12,3 @@ class K:
     def beta(self):
+        pass
         return 2
diff --git a/pkg/new.py b/pkg/new.py
new file mode 100644
--- /dev/null
+++ b/pkg/new.py
@@ -0,0 +1 @@
+X = 1
diff --git a/docs/conf.txt b/docs/conf.txt
--- a/docs/conf.txt
+++ b/docs/conf.txt
@@ -1 +1 @@
-a
+b
diff --git a/pkg/tests/test_mod.py b/pkg/tests/test_mod.py
--- a/pkg/tests/test_mod.py
+++ b/pkg/tests/test_mod.py
@@ -1 +1 @@
-a
+b
"""


class _Rec:
    def __init__(self, src):
        self.spans, self.texts, self.parse_ok = chunk_source(src)


def test_parse_diff_files_and_hunks():
    files = parse_diff(DIFF)
    assert [(f.old_path, f.new_path) for f in files] == [
        ("pkg/mod.py", "pkg/mod.py"),
        (None, "pkg/new.py"),
        ("docs/conf.txt", "docs/conf.txt"),
        ("pkg/tests/test_mod.py", "pkg/tests/test_mod.py"),
    ]
    h0, h1 = files[0].hunks
    assert (h0.old_start, h0.old_len, h0.new_start, h0.new_len) == (4, 3, 4, 3)
    assert changed_old_lines(h0) == [5]
    assert changed_old_lines(h1) == [12]  # insertion after old line 12


def test_parse_diff_does_not_confuse_content_with_headers():
    diff = (
        "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n"
        "--- not a header\n+++ also not\n ctx\n"
    )
    (f,) = parse_diff(diff)
    assert f.old_path == "x.py" and len(f.hunks) == 1
    assert changed_old_lines(f.hunks[0]) == [1]


def test_changed_lines_runs_vs_per_hunk():
    diff = (
        "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1,6 +1,6 @@\n"
        " a\n-b\n+B\n c\n d\n+e2\n e\n f\n"
    )
    (f,) = parse_diff(diff)
    h = f.hunks[0]
    assert changed_old_lines(h) == [2, 4]
    assert changed_old_lines(h, per_hunk=True) == [2]


def test_insert_at_top_maps_to_line_zero():
    diff = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -0,0 +1,1 @@\n+import y\n"
    (f,) = parse_diff(diff)
    assert changed_old_lines(f.hunks[0]) == [0]


def test_no_newline_marker_ignored():
    diff = (
        "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n"
        "\\ No newline at end of file\n+b\n\\ No newline at end of file\n"
    )
    (f,) = parse_diff(diff)
    assert f.hunks[0].lines == ["-a", "+b"]


def test_build_label_view_counts_and_chunks():
    base_files = {"pkg/mod.py": "sha-mod", "docs/conf.txt": "sha-txt", "pkg/tests/test_mod.py": "sha-t"}
    recs = {"sha-mod": _Rec(BASE)}
    lv = build_label_view("i-1", "o/r", DIFF, base_files, recs.__getitem__)
    assert lv.gold_files == ("pkg/mod.py",)
    assert lv.excluded_counts() == {"new_file": 1, "non_py": 1, "test": 1}
    assert lv.gold_chunks == ("pkg/mod.py::K.beta@12", "pkg/mod.py::alpha@4")
    assert lv.module_chunks == 0
    assert not lv.unretrievable


def test_unretrievable_when_only_new_and_tests():
    diff = DIFF.split("diff --git a/pkg/new.py")[1]
    diff = "diff --git a/pkg/new.py" + diff
    lv = build_label_view("i-2", "o/r", diff, {"docs/conf.txt": "s"}, lambda s: None)
    assert lv.unretrievable and lv.gold_chunks == ()


def test_missing_at_base_counted():
    diff = "diff --git a/pkg/gone.py b/pkg/gone.py\n--- a/pkg/gone.py\n+++ b/pkg/gone.py\n@@ -1 +1 @@\n-a\n+b\n"
    lv = build_label_view("i-3", "o/r", diff, {}, lambda s: None)
    assert lv.excluded_counts() == {"missing_at_base": 1} and lv.unretrievable


def test_module_level_change_maps_to_module_chunk():
    diff = "diff --git a/pkg/mod.py b/pkg/mod.py\n--- a/pkg/mod.py\n+++ b/pkg/mod.py\n@@ -1 +1 @@\n-import os\n+import sys\n"
    lv = build_label_view("i-4", "o/r", diff, {"pkg/mod.py": "s"}, {"s": _Rec(BASE)}.__getitem__)
    assert lv.gold_chunks == ("pkg/mod.py::<module>@1",) and lv.module_chunks == 1
