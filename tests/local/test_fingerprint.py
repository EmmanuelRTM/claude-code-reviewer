"""Working-tree fingerprint: identifies exact content, independent of commits."""

from __future__ import annotations

import pytest

from reviewkit.fingerprint import FingerprintError, tree_fingerprint


def test_stable_across_calls(repo):
    repo.write("app.py", "print(1)\n")
    assert tree_fingerprint(repo.path) == tree_fingerprint(repo.path)


def test_commit_of_the_same_content_keeps_the_fingerprint(repo):
    repo.write("app.py", "print(1)\n")
    before = tree_fingerprint(repo.path)
    repo.commit("add app")
    assert tree_fingerprint(repo.path) == before
    repo.git("commit", "-q", "--amend", "-m", "reworded")
    assert tree_fingerprint(repo.path) == before


def test_partial_commits_do_not_change_it(repo):
    repo.write("a.py", "a\n")
    repo.write("b.py", "b\n")
    before = tree_fingerprint(repo.path)
    repo.git("add", "a.py")
    repo.git("commit", "-q", "-m", "only a")
    assert tree_fingerprint(repo.path) == before


@pytest.mark.parametrize("change", ["edit", "add_untracked", "delete", "mode"])
def test_any_content_change_changes_it(repo, change):
    repo.write("app.py", "print(1)\n")
    repo.commit("add app")
    before = tree_fingerprint(repo.path)
    if change == "edit":
        repo.write("app.py", "print(2)\n")
    elif change == "add_untracked":
        repo.write("new.py", "x\n")
    elif change == "delete":
        (repo.path / "app.py").unlink()
    else:
        (repo.path / "app.py").chmod(0o755)
    assert tree_fingerprint(repo.path) != before


def test_ignored_files_and_the_state_directory_do_not_count(repo):
    repo.write(".gitignore", "*.log\n")  # note: the state dir is NOT ignored here on purpose
    repo.commit("ignore logs")
    before = tree_fingerprint(repo.path)
    repo.write("debug.log", "noise\n")
    repo.write(".claude/state/verified.json", "{}\n")
    assert tree_fingerprint(repo.path) == before


def test_does_not_touch_the_real_index_or_work_tree(repo):
    repo.write("staged.py", "s\n")
    repo.git("add", "staged.py")
    repo.write("unstaged.py", "u\n")
    status_before = repo.git("status", "--porcelain")
    tree_fingerprint(repo.path)
    assert repo.git("status", "--porcelain") == status_before
    leftovers = [p.name for p in (repo.path / ".git").iterdir() if p.name.startswith("reviewkit-index")]
    assert leftovers == []


def test_works_before_the_first_commit(make_repo):
    fresh = make_repo(initial_commit=False)
    fresh.write("a.txt", "a\n")
    first = tree_fingerprint(fresh.path)
    fresh.write("a.txt", "b\n")
    assert tree_fingerprint(fresh.path) != first


def test_works_in_a_linked_worktree(repo, tmp_path):
    linked = tmp_path / "linked"
    repo.git("worktree", "add", "-q", "--detach", str(linked))
    base = tree_fingerprint(linked)
    (linked / "README.md").write_text("changed\n")
    assert tree_fingerprint(linked) != base
    # The main checkout is unaffected by the linked worktree's edit.
    assert tree_fingerprint(repo.path) == base


def test_outside_a_repository_raises(tmp_path):
    with pytest.raises(FingerprintError):
        tree_fingerprint(tmp_path)


def test_state_directory_that_exists_and_is_ignored_does_not_break_it(repo):
    # Regression: an exclude pathspec naming an ignored directory made `git add` fail.
    repo.write(".claude/state/verified.json", "{}\n")
    assert repo.git("check-ignore", ".claude/state/verified.json").strip()
    before = tree_fingerprint(repo.path)
    repo.write(".claude/state/gate.log", "x\n")
    assert tree_fingerprint(repo.path) == before
