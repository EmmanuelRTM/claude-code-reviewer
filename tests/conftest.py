"""Shared fixtures: throwaway git repositories and an in-memory GitHub."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable

import pytest

from fakes import FakeGitHub

REPO_ROOT = Path(__file__).resolve().parent.parent
KIT_ROOT = REPO_ROOT / "kit"
LIB_ROOT = KIT_ROOT / ".claude" / "reviewkit" / "lib"

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_TERMINAL_PROMPT": "0",
}


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never see the developer's git config or CI switches."""
    for name, value in _GIT_ENV.items():
        monkeypatch.setenv(name, value)
    for name in (
        "GITHUB_ACTIONS",
        "CI",
        "REVIEWKIT_DISABLE",
        "REVIEWKIT_HOOKS_IN_CI",
        "VERIFY_GATE_MODE",
        "CLAUDE_PROJECT_DIR",
        "GH_TOKEN",
        "GITHUB_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)


def run_git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, check=True)
    return proc.stdout


class GitRepo:
    """A small helper around a temporary repository."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def git(self, *args: str) -> str:
        return run_git(self.path, *args)

    def write(self, relative: str, content: str = "") -> Path:
        target = self.path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def commit(self, message: str = "commit", *, add_all: bool = True) -> str:
        if add_all:
            self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD").strip()


@pytest.fixture
def make_repo(tmp_path: Path) -> Callable[..., GitRepo]:
    counter = {"n": 0}

    def factory(name: str = "", *, initial_commit: bool = True) -> GitRepo:
        counter["n"] += 1
        path = tmp_path / (name or f"repo{counter['n']}")
        path.mkdir(parents=True)
        run_git(path, "init", "-q", "-b", "main")
        repo = GitRepo(path)
        repo.write(".gitignore", ".claude/state/\n")
        if initial_commit:
            repo.write("README.md", "# test\n")
            repo.commit("initial")
        return repo

    return factory


@pytest.fixture
def repo(make_repo: Callable[..., GitRepo]) -> GitRepo:
    return make_repo()


@pytest.fixture
def github() -> FakeGitHub:
    return FakeGitHub()
