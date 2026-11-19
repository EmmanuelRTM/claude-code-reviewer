"""Builders and plumbing shared by the gates tests.

Imported as `gates.gates_kit`: `tests/` is on the import path (`pythonpath` in
pyproject.toml), which makes `tests/gates` the namespace package `gates`.

This directory deliberately has no `conftest.py` and no generically named
helper: pytest puts every test directory on `sys.path`, so a second
`conftest.py` or `support.py` here could shadow one that tests elsewhere
import by name. The two fixtures the command tests need are defined in those
test modules, on top of `Actions` below.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence, Tuple

import pytest

from reviewkit import cmd_gates, markers
from reviewkit.github.client import GitHubError

HEAD = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
OLD = "0123456789abcdef0123456789abcdef01234567"
SHORT = HEAD[:7]
EARLY, NOON, LATE = "2026-03-01T09:00:00Z", "2026-03-01T12:00:00Z", "2026-03-01T15:00:00Z"


def user(login: str, kind: str = "User") -> Dict[str, Any]:
    return {"login": login, "type": kind}


def review(
    login: str, state: str = "APPROVED", commit: str = HEAD, at: str = NOON, kind: str = "User"
) -> Dict[str, Any]:
    return {"user": user(login, kind), "state": state, "commit_id": commit, "submitted_at": at}


def comment(login: str, body: str, at: str = NOON, kind: str = "User", **extra: Any) -> Dict[str, Any]:
    """An issue comment; like GitHub, `updated_at` equals `created_at` until someone edits it."""
    data: Dict[str, Any] = {"user": user(login, kind), "body": body, "created_at": at, "updated_at": at}
    data.update(extra)
    return data


def pull(
    number: int = 7,
    head: str = HEAD,
    base: str = "main",
    labels: Sequence[str] = (),
    state: str = "open",
    author: str = "dev",
    **extra: Any,
) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "number": number,
        "state": state,
        "title": "Add the export button",
        "user": user(author),
        "head": {"sha": head, "ref": "feature"},
        "base": {"ref": base},
        "labels": [{"name": name} for name in labels],
    }
    data.update(extra)
    return data


def merged_pull(
    number: int = 1,
    title: str = "Add the export button",
    body: str = "",
    labels: Sequence[str] = (),
    reviews: Sequence[Dict[str, Any]] = (),
    review_comments: Sequence[Dict[str, Any]] = (),
    commits: Sequence[Dict[str, Any]] = (),
    created_at: str = "2026-03-01T00:00:00Z",
) -> Dict[str, Any]:
    """One merged pull request in the shape `metrics.collect_pulls` returns."""
    return {
        "number": number,
        "title": title,
        "body": body,
        "created_at": created_at,
        "merged_at": "2026-03-05T00:00:00Z",
        "labels": list(labels),
        "reviews": list(reviews),
        "review_comments": list(review_comments),
        "commits": list(commits),
    }


def finding(sev: str = "important", cid: Any = None, reactions: Any = None, kind: str = "Bot") -> Dict[str, Any]:
    """A review comment that carries a `finding` marker, as the review bot writes them."""
    payload: Dict[str, Any] = {"id": "f-1", "sev": sev, "round": 1}
    if cid is not None:
        payload["cid"] = cid
    body = f"**Missing timeout** on the new call.\n\n{markers.encode('finding', payload)}"
    return {"user": user("github-actions[bot]", kind), "body": body, "reactions": reactions or {}}


def commit(message: str) -> Dict[str, Any]:
    return {"sha": "c0ffee", "commit": {"message": message}}


def hours_after_creation(n: float) -> str:
    """A timestamp `n` hours after the default `created_at` of `merged_pull`."""
    whole, minutes = divmod(int(n * 60), 60)
    return f"2026-03-01T{whole:02d}:{minutes:02d}:00Z"


def raises(status: int) -> Callable[..., Any]:
    """A fake route that fails with the given HTTP status."""

    def respond(match: Any, params: Any, body: Any) -> Any:
        raise GitHubError(status, "FAKE", match.string, "fake failure")

    return respond


def calls(gh: Any) -> List[Tuple[str, str]]:
    """Every call the fake saw, as `(METHOD, path)` in order."""
    return [(call["method"], call["path"]) for call in gh.calls]


def bodies(gh: Any) -> List[Any]:
    """Every request body the code under test sent, in order."""
    return [call["body"] for call in gh.calls if call["body"] is not None]


def run_cli(argv: Sequence[str]) -> int:
    """Run a gates command through a parser that holds only this module's commands.

    Mirrors `reviewkit.cli.main` without importing the other command modules,
    so these tests exercise the gates commands alone.
    """
    parser = argparse.ArgumentParser(prog="rk")
    cmd_gates.register(parser.add_subparsers(dest="command", metavar="<command>"))
    args = parser.parse_args(list(argv))
    return int(args.handler(args) or 0)


def read_outputs(path: Any) -> Dict[str, str]:
    """Parse the single-line `name=value` entries of a GITHUB_OUTPUT file."""
    result: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        result[name] = value
    return result


def _refuse_real_client() -> Any:
    raise AssertionError("this test tried to build a real GitHub client")


class Actions:
    """What a workflow step sees: repository, event file, output files, cwd.

    Build one per test from the `tmp_path` and `monkeypatch` fixtures.
    """

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._monkeypatch = monkeypatch
        self.project = tmp_path / "project"
        self.project.mkdir()
        self.output_file = tmp_path / "github_output.txt"
        self.summary_file = tmp_path / "step_summary.md"
        self._event_file = tmp_path / "event.json"
        monkeypatch.chdir(self.project)
        monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
        monkeypatch.setenv("GITHUB_OUTPUT", str(self.output_file))
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(self.summary_file))
        monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
        monkeypatch.setenv("GITHUB_RUN_ID", "42")
        for name in ("GITHUB_EVENT_NAME", "GITHUB_EVENT_PATH"):
            monkeypatch.delenv(name, raising=False)
        # A test that forgets `use_github` fails instead of reaching for a real token.
        monkeypatch.setattr(cmd_gates, "client_from_env", _refuse_real_client)

    def use_github(self, fake: Any) -> Any:
        """Hand `fake` to the gates commands in place of a real GitHub client."""
        self._monkeypatch.setattr(cmd_gates, "client_from_env", lambda: fake)
        return fake

    def event(self, name: str, payload: Dict[str, Any]) -> None:
        self._event_file.write_text(json.dumps(payload), encoding="utf-8")
        self._monkeypatch.setenv("GITHUB_EVENT_NAME", name)
        self._monkeypatch.setenv("GITHUB_EVENT_PATH", str(self._event_file))

    def config(self, data: Any) -> None:
        path = self.project / ".claude" / "reviewkit.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")

    def write(self, relative: str, content: str) -> Path:
        path = self.project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def outputs(self) -> Dict[str, str]:
        return read_outputs(self.output_file) if self.output_file.exists() else {}

    def summary(self) -> str:
        return self.summary_file.read_text(encoding="utf-8") if self.summary_file.exists() else ""
