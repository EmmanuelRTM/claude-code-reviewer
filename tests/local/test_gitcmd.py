"""Recognising `git commit` inside a Bash command, and reading its trailer."""

from __future__ import annotations

import pytest

from reviewkit.hooks.gitcmd import is_git_commit, read_trailer

HEREDOC_COMMIT = """git commit -m "$(cat <<'EOF'
Fix the parser

Verified-By: verify, code-review
EOF
)\""""

COMMITS = [
    "git commit",
    'git commit -m "fix: thing"',
    "git commit -am 'wip'",
    "git commit --amend --no-edit",
    "git -C sub commit -m x",
    "git -c user.name=a -c user.email=b@c commit -m x",
    "git --no-pager commit -m x",
    "git --git-dir=.git --work-tree=. commit -m x",
    "FOO=1 BAR=2 git commit -m x",
    "git add -A && git commit -m 'msg'",
    "cd pkg; git commit -m x",
    "npm test || git commit -m x",
    "(git commit -m x)",
    "/usr/bin/git commit -m x",
    "env GIT_AUTHOR_NAME=x git commit -m y",
    "command git commit -m y",
    "git add . &&\n  git commit -m 'multi line'",
    HEREDOC_COMMIT,
    "echo start; `git commit -m x`",
    "echo $(git commit -m x)",
]

NOT_COMMITS = [
    "git commit-tree abc123",
    "git commit-graph write",
    "git status",
    "git log --grep=commit",
    'git log --grep="git commit"',
    'echo "git commit -m x"',
    "echo 'run git commit later'",
    "grep -r 'git commit' .",
    'gh pr create --title "git commit helper"',
    "git config alias.ci commit",
    "git -C commit status",
    "git stash; echo commit",
    "cat <<EOF\ngit commit -m x\nEOF",
    "git show HEAD # git commit -m x",
    "git push origin main",
    "gitcommit -m x",
    "legit commit -m x",
    "",
]


@pytest.mark.parametrize("command", COMMITS)
def test_commit_commands_are_recognised(command):
    assert is_git_commit(command) is True


@pytest.mark.parametrize("command", NOT_COMMITS)
def test_near_twins_are_not_commits(command):
    assert is_git_commit(command) is False


def test_trailer_in_a_message_flag(tmp_path):
    info = read_trailer('git commit -m "fix" -m "Verified-By: verify, code-review"', tmp_path)
    assert info.message_visible and info.present and not info.opted_out
    assert info.value == "verify, code-review"


def test_trailer_in_a_heredoc(tmp_path):
    info = read_trailer(HEREDOC_COMMIT, tmp_path)
    assert info.present and info.value == "verify, code-review"


def test_opt_out_requires_a_reason(tmp_path):
    good = read_trailer("git commit -m 'docs' -m 'Verified-By: none (docs only, nothing to run)'", tmp_path)
    assert good.present and good.opted_out and good.reason == "docs only, nothing to run"
    for bad in ("Verified-By: none", "Verified-By: none ()", "Verified-By: none ( )", "Verified-By: none (x)"):
        info = read_trailer(f"git commit -m 'y' -m '{bad}'", tmp_path)
        assert info.present and not info.opted_out, bad


def test_opt_out_is_case_insensitive_on_the_key(tmp_path):
    info = read_trailer("git commit -m 'x\n\nverified-by: None (generated lockfile bump)'", tmp_path)
    assert info.opted_out and info.reason == "generated lockfile bump"


def test_no_trailer(tmp_path):
    info = read_trailer("git commit -m 'just a message'", tmp_path)
    assert info.message_visible and not info.present


def test_message_from_a_file(tmp_path):
    (tmp_path / "msg.txt").write_text("subject\n\nVerified-By: verify\n")
    info = read_trailer("git commit -F msg.txt", tmp_path)
    assert info.message_visible and info.present and info.value == "verify"
    missing = read_trailer("git commit --file nope.txt", tmp_path)
    assert not missing.message_visible and not missing.present


def test_message_not_visible_when_git_will_reuse_or_prompt(tmp_path):
    for command in ("git commit", "git commit --amend --no-edit", "git commit -C HEAD"):
        info = read_trailer(command, tmp_path)
        assert not info.message_visible and not info.present, command


def test_trailer_text_outside_the_commit_statement_does_not_count(tmp_path):
    info = read_trailer("echo 'Verified-By: none (sneaky)' && git commit -m 'real message'", tmp_path)
    assert not info.present and not info.opted_out
