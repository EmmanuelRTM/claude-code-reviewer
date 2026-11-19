"""Unified diff parsing: which lines can carry an inline comment, and diff statistics."""

from __future__ import annotations

from reviewkit.review.diffmap import diff_stats, parse_unified_diff, patch_id

MODIFY = """diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -10,5 +10,6 @@ def handler(event):
     context line 10
     context line 11
-    removed line
+    added line 12
+    added line 13
     context line 14
     context line 15
@@ -40,3 +41,4 @@ def other():
     context line 41
+    added line 42
     context line 43
     context line 44
"""

ADD = """diff --git a/new file.txt b/new file.txt
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/new file.txt
@@ -0,0 +1,2 @@
+first
+second
\\ No newline at end of file
"""

DELETE = """diff --git a/old.py b/old.py
deleted file mode 100644
index 4444444..0000000
--- a/old.py
+++ /dev/null
@@ -1,2 +0,0 @@
-gone 1
-gone 2
"""

RENAME_ONLY = """diff --git a/lib/a.py b/lib/b.py
similarity index 100%
rename from lib/a.py
rename to lib/b.py
"""

RENAME_EDIT = """diff --git a/lib/c.py b/pkg/d.py
similarity index 80%
rename from lib/c.py
rename to pkg/d.py
index 5555555..6666666 100644
--- a/lib/c.py
+++ b/pkg/d.py
@@ -1,3 +1,3 @@
 keep
-old
+new
 keep
"""

BINARY = """diff --git a/img/logo.png b/img/logo.png
index 7777777..8888888 100644
Binary files a/img/logo.png and b/img/logo.png differ
"""

MODE_ONLY = """diff --git a/run.sh b/run.sh
old mode 100644
new mode 100755
"""


def test_modified_file_lines():
    files = parse_unified_diff(MODIFY)
    entry = files["src/app.py"]
    assert entry.status == "modified" and entry.old_path == "src/app.py"
    assert entry.added_lines == frozenset({12, 13, 42})
    assert entry.right_lines == frozenset({10, 11, 12, 13, 14, 15, 41, 42, 43, 44})
    assert (entry.additions, entry.deletions) == (3, 1)
    assert entry.commentable(13) and entry.commentable(10)
    assert not entry.commentable(16) and not entry.commentable(40)


def test_added_file_with_a_space_in_its_name_and_no_trailing_newline():
    entry = parse_unified_diff(ADD)["new file.txt"]
    assert entry.status == "added"
    assert entry.added_lines == entry.right_lines == frozenset({1, 2})


def test_deleted_file_has_no_commentable_lines():
    entry = parse_unified_diff(DELETE)["old.py"]
    assert entry.status == "deleted"
    assert entry.right_lines == frozenset() and entry.deletions == 2
    assert not entry.commentable(1)


def test_renames_with_and_without_edits():
    pure = parse_unified_diff(RENAME_ONLY)["lib/b.py"]
    assert pure.status == "renamed" and pure.old_path == "lib/a.py" and pure.right_lines == frozenset()
    edited = parse_unified_diff(RENAME_EDIT)["pkg/d.py"]
    assert edited.status == "renamed" and edited.old_path == "lib/c.py"
    assert edited.added_lines == frozenset({2}) and edited.right_lines == frozenset({1, 2, 3})


def test_binary_and_mode_only_changes():
    binary = parse_unified_diff(BINARY)["img/logo.png"]
    assert binary.binary and binary.right_lines == frozenset()
    assert parse_unified_diff(MODE_ONLY)["run.sh"].status == "modified"


def test_several_files_in_one_diff_and_stats():
    text = MODIFY + ADD + DELETE + RENAME_EDIT + BINARY
    files = parse_unified_diff(text)
    assert list(files) == ["src/app.py", "new file.txt", "old.py", "pkg/d.py", "img/logo.png"]
    stats = diff_stats(files, text)
    assert stats == {"files": 5, "additions": 6, "deletions": 4, "diff_bytes": len(text.encode())}


def test_content_lines_that_look_like_headers_are_not_headers():
    text = """diff --git a/notes.md b/notes.md
index 1..2 100644
--- a/notes.md
+++ b/notes.md
@@ -1,2 +1,4 @@
 intro
+--- a/fake.py
++++ b/fake.py
+diff --git a/x b/x
 outro
"""
    files = parse_unified_diff(text)
    assert list(files) == ["notes.md"]
    assert files["notes.md"].added_lines == frozenset({2, 3, 4})


def test_range_commentable_requires_one_contiguous_hunk():
    entry = parse_unified_diff(MODIFY)["src/app.py"]
    assert entry.range_commentable(11, 14)
    assert not entry.range_commentable(15, 41)
    assert not entry.range_commentable(14, 11)


def test_quoted_paths_are_unquoted():
    text = 'diff --git "a/we\\"ird.txt" "b/we\\"ird.txt"\nnew file mode 100644\n--- /dev/null\n+++ "b/we\\"ird.txt"\n@@ -0,0 +1 @@\n+x\n'
    assert list(parse_unified_diff(text)) == ['we"ird.txt']


def test_empty_diff():
    assert parse_unified_diff("") == {}


def test_patch_id_survives_a_moved_base_and_changes_with_content(repo):
    body = [f"line {i}" for i in range(1, 21)]
    repo.write("a.py", "\n".join(body) + "\n")
    base = repo.commit("base")
    changed = list(body)
    changed[14] = "line 15 CHANGED"
    repo.write("a.py", "\n".join(changed) + "\n")
    head = repo.commit("change")
    first = patch_id(repo.path, base, head)
    assert len(first) == 40

    # The base moves: another file appears and lines are inserted far above the edit.
    # The pull request still adds and removes the same lines, so the id is unchanged.
    repo.git("checkout", "-q", base)
    repo.write("other.py", "unrelated\n")
    repo.write("a.py", "\n".join(["new top 1", "new top 2", *body]) + "\n")
    base2 = repo.commit("base moved")
    repo.write("a.py", "\n".join(["new top 1", "new top 2", *changed]) + "\n")
    head2 = repo.commit("same change, rebased")
    assert patch_id(repo.path, base2, head2) == first

    changed[14] = "line 15 CHANGED DIFFERENTLY"
    repo.write("a.py", "\n".join(["new top 1", "new top 2", *changed]) + "\n")
    head3 = repo.commit("different change")
    assert patch_id(repo.path, base2, head3) != first


def test_patch_id_of_an_empty_diff_is_empty(repo):
    head = repo.git("rev-parse", "HEAD").strip()
    assert patch_id(repo.path, head, head) == ""


def test_spans_and_truncation_of_oversized_files():
    from reviewkit.review.diffmap import truncate_diff

    big = "diff --git a/big.txt b/big.txt\nnew file mode 100644\n--- /dev/null\n+++ b/big.txt\n@@ -0,0 +1,50 @@\n"
    big += "".join(f"+line {i}\n" for i in range(50))
    text = MODIFY + big + DELETE
    files = parse_unified_diff(text)
    lines = text.split("\n")
    start, end = files["big.txt"].span
    assert lines[start] == "diff --git a/big.txt b/big.txt" and lines[end].startswith("diff --git a/old.py")
    shorter, truncated = truncate_diff(text, files, max_file_lines=20)
    assert truncated == ["big.txt"]
    assert "+line 49" not in shorter and "diff body omitted: 50 added and 0 removed lines" in shorter
    # Neighbouring files are untouched and the result still parses file by file.
    again = parse_unified_diff(shorter)
    assert list(again) == ["src/app.py", "big.txt", "old.py"]
    assert again["src/app.py"].added_lines == files["src/app.py"].added_lines
    assert truncate_diff(text, files, max_file_lines=500) == (text, [])
