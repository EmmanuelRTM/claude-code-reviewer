"""Read-only detection of a repository's check commands and default branch."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from installer import gitquery
from installer.detect import detect_commands, detect_settings
from installer.errors import InstallError
from installer.gitquery import origin_default_branch, work_tree_root

PYTEST = "python3 -m pytest -q"


def package_json(root: Path, scripts: object) -> None:
    (root / "package.json").write_text(json.dumps({"name": "demo", "scripts": scripts}), encoding="utf-8")


def touch(root: Path, relative: str, content: str = "") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_nothing_is_detected_in_an_empty_directory(tmp_path: Path) -> None:
    assert detect_commands(tmp_path) == {}


# --- Node ------------------------------------------------------------------------


def test_package_scripts_map_to_run_commands(tmp_path: Path) -> None:
    package_json(tmp_path, {"test": "jest", "lint": "eslint .", "build": "tsc", "dev": "vite", "deploy": "x"})
    assert detect_commands(tmp_path) == {
        "test": "npm run test",
        "lint": "npm run lint",
        "build": "npm run build",
        "start": "npm run dev",
    }


@pytest.mark.parametrize(
    ("lockfile", "manager"),
    [
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("bun.lockb", "bun"),
        ("bun.lock", "bun"),
        ("package-lock.json", "npm"),
    ],
)
def test_the_lockfile_picks_the_package_manager(tmp_path: Path, lockfile: str, manager: str) -> None:
    package_json(tmp_path, {"test": "jest", "start": "node server.js"})
    touch(tmp_path, lockfile)
    assert detect_commands(tmp_path) == {"test": f"{manager} run test", "start": f"{manager} run start"}


def test_lockfile_precedence(tmp_path: Path) -> None:
    package_json(tmp_path, {"build": "tsc"})
    touch(tmp_path, "bun.lock")
    touch(tmp_path, "yarn.lock")
    assert detect_commands(tmp_path) == {"build": "yarn run build"}
    touch(tmp_path, "pnpm-lock.yaml")
    assert detect_commands(tmp_path) == {"build": "pnpm run build"}


def test_dev_is_preferred_over_start(tmp_path: Path) -> None:
    package_json(tmp_path, {"start": "node dist/server.js", "dev": "vite"})
    assert detect_commands(tmp_path)["start"] == "npm run dev"


def test_the_npm_placeholder_test_script_is_ignored(tmp_path: Path) -> None:
    package_json(tmp_path, {"test": 'echo "Error: no test specified" && exit 1', "lint": "eslint ."})
    assert detect_commands(tmp_path) == {"lint": "npm run lint"}


@pytest.mark.parametrize(
    "scripts", [None, [], "jest", {"test": ""}, {"test": "  "}, {"test": 5}, {"test:unit": "jest"}]
)
def test_unusable_scripts_are_ignored(tmp_path: Path, scripts: object) -> None:
    package_json(tmp_path, scripts)
    assert detect_commands(tmp_path) == {}


@pytest.mark.parametrize("content", ["", "{broken", "[]", '"text"'])
def test_an_unreadable_package_json_is_ignored(tmp_path: Path, content: str) -> None:
    touch(tmp_path, "package.json", content)
    assert detect_commands(tmp_path) == {}


def test_a_directory_named_like_a_marker_file_is_ignored(tmp_path: Path) -> None:
    for name in ("package.json", "pyproject.toml", "go.mod", "Cargo.toml", "Makefile"):
        (tmp_path / name).mkdir()
    assert detect_commands(tmp_path) == {}


# --- Python -----------------------------------------------------------------------


@pytest.mark.parametrize("marker", ["pyproject.toml", "pytest.ini", "setup.cfg"])
def test_python_project_files_suggest_pytest(tmp_path: Path, marker: str) -> None:
    touch(tmp_path, marker)
    assert detect_commands(tmp_path) == {"test": PYTEST}


@pytest.mark.parametrize("test_file", ["tests/test_app.py", "tests/unit/deep/test_models.py"])
def test_a_tests_directory_with_test_modules_suggests_pytest(tmp_path: Path, test_file: str) -> None:
    touch(tmp_path, test_file)
    assert detect_commands(tmp_path) == {"test": PYTEST}


def test_a_tests_directory_without_test_modules_suggests_nothing(tmp_path: Path) -> None:
    touch(tmp_path, "tests/helpers.py")
    touch(tmp_path, "tests/app.test.js")
    touch(tmp_path, "tests/test_notes.txt")
    assert detect_commands(tmp_path) == {}


@pytest.mark.parametrize("section", ["[tool.ruff]", "[tool.ruff.lint]", "  [tool.ruff.format]", "[tool.ruff]  # style"])
def test_a_ruff_section_in_pyproject_suggests_ruff(tmp_path: Path, section: str) -> None:
    touch(tmp_path, "pyproject.toml", f'[project]\nname = "demo"\n\n{section}\nline-length = 100\n')
    assert detect_commands(tmp_path) == {"test": PYTEST, "lint": "ruff check ."}


@pytest.mark.parametrize("content", ["[tool.ruffian]\n", "# [tool.ruff]\n", 'description = "uses [tool.ruff]"\n', ""])
def test_pyproject_without_a_ruff_section_suggests_no_lint(tmp_path: Path, content: str) -> None:
    touch(tmp_path, "pyproject.toml", content)
    assert detect_commands(tmp_path) == {"test": PYTEST}


@pytest.mark.parametrize("ruff_file", ["ruff.toml", ".ruff.toml"])
def test_a_ruff_config_file_suggests_ruff(tmp_path: Path, ruff_file: str) -> None:
    touch(tmp_path, "setup.cfg")
    touch(tmp_path, ruff_file)
    assert detect_commands(tmp_path) == {"test": PYTEST, "lint": "ruff check ."}


def test_a_ruff_config_alone_gives_a_lint_command_but_no_test_command(tmp_path: Path) -> None:
    # The file says which linter is in use. It says nothing about tests, and a
    # test command that runs zero tests would fail the Stop hook.
    touch(tmp_path, "ruff.toml")
    assert detect_commands(tmp_path) == {"lint": "ruff check ."}


# --- Go, Rust, Make -----------------------------------------------------------------


def test_go_module(tmp_path: Path) -> None:
    touch(tmp_path, "go.mod", "module example.com/demo\n")
    assert detect_commands(tmp_path) == {"test": "go test ./...", "lint": "go vet ./...", "build": "go build ./..."}


def test_cargo_package(tmp_path: Path) -> None:
    touch(tmp_path, "Cargo.toml", "[package]\n")
    assert detect_commands(tmp_path) == {
        "test": "cargo test",
        "lint": "cargo clippy -- -D warnings",
        "build": "cargo build",
    }


def test_makefile_targets(tmp_path: Path) -> None:
    touch(tmp_path, "Makefile", ".PHONY: test lint build\n\ntest:\n\tgo test\nlint: deps\n\tx\nbuild ::\n\ty\n")
    assert detect_commands(tmp_path) == {"test": "make test", "lint": "make lint", "build": "make build"}


def test_makefile_lines_that_are_not_those_targets(tmp_path: Path) -> None:
    content = (
        ".PHONY: test lint build\n"
        "test := value\n"
        "lint:=value\n"
        "build ::= value\n"
        "tests:\n\techo\n"
        "test-unit:\n\techo\n"
        "# test:\n"
        "all:\n\ttest: nested\n"
    )
    touch(tmp_path, "Makefile", content)
    assert detect_commands(tmp_path) == {}


@pytest.mark.parametrize("name", ["GNUmakefile", "makefile", "Makefile"])
def test_makefile_names_that_make_reads(tmp_path: Path, name: str) -> None:
    touch(tmp_path, name, "test:\n\techo\n")
    assert detect_commands(tmp_path) == {"test": "make test"}


# --- several ecosystems ---------------------------------------------------------------


def test_earlier_ecosystems_win_and_later_ones_fill_the_gaps(tmp_path: Path) -> None:
    package_json(tmp_path, {"lint": "eslint ."})
    touch(tmp_path, "pyproject.toml", "[tool.ruff]\n")
    touch(tmp_path, "go.mod")
    touch(tmp_path, "Makefile", "test:\n\tx\nbuild:\n\ty\n")
    assert detect_commands(tmp_path) == {
        "lint": "npm run lint",  # Node comes first
        "test": PYTEST,  # then Python
        "build": "go build ./...",  # then Go; the Makefile had nothing left to fill
    }


def test_makefile_only_fills_empty_slots(tmp_path: Path) -> None:
    touch(tmp_path, "Cargo.toml")
    touch(tmp_path, "Makefile", "test:\n\tx\nlint:\n\ty\n")
    assert detect_commands(tmp_path)["test"] == "cargo test"
    package_json(tmp_path, {"test": "jest"})
    assert detect_commands(tmp_path)["test"] == "npm run test"


# --- git queries ------------------------------------------------------------------------


def set_origin_head(repo, branch: str) -> None:
    repo.git("update-ref", f"refs/remotes/origin/{branch}", "HEAD")
    repo.git("symbolic-ref", "refs/remotes/origin/HEAD", f"refs/remotes/origin/{branch}")


def test_work_tree_root(repo) -> None:
    assert work_tree_root(repo.path) == repo.path.resolve()
    (repo.path / "sub" / "dir").mkdir(parents=True)
    assert work_tree_root(repo.path / "sub" / "dir") == repo.path.resolve()


def test_work_tree_root_outside_a_repository(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(InstallError, match="not inside a git work tree"):
        work_tree_root(plain)


def test_work_tree_root_of_a_bare_repository(tmp_path: Path) -> None:
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    with pytest.raises(InstallError):
        work_tree_root(bare)


def test_inherited_git_variables_do_not_redirect_the_query(repo, make_repo, monkeypatch: pytest.MonkeyPatch) -> None:
    other = make_repo("other")
    monkeypatch.setenv("GIT_DIR", str(other.path / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other.path))
    assert work_tree_root(repo.path) == repo.path.resolve()


def test_a_missing_git_is_reported(repo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(InstallError, match="git"):
        work_tree_root(repo.path)
    assert origin_default_branch(repo.path) is None  # detection is best effort


def test_a_hanging_git_is_reported(repo, monkeypatch: pytest.MonkeyPatch) -> None:
    def hang(command, **options):
        assert options["timeout"] > 0
        raise subprocess.TimeoutExpired(command, options["timeout"])

    monkeypatch.setattr(gitquery.subprocess, "run", hang)
    with pytest.raises(InstallError, match="did not answer"):
        work_tree_root(repo.path)


def test_origin_default_branch(repo) -> None:
    assert origin_default_branch(repo.path) is None
    set_origin_head(repo, "trunk")
    assert origin_default_branch(repo.path) == "trunk"
    set_origin_head(repo, "release/2.x")
    assert origin_default_branch(repo.path) == "release/2.x"


def test_a_branch_name_with_shell_syntax_is_not_trusted(repo) -> None:
    # Legal for git, but the name ends up in a configuration that scripts read.
    set_origin_head(repo, "$(touch${IFS}owned)")
    assert origin_default_branch(repo.path) is None


def test_detect_settings_combines_commands_and_branch(repo) -> None:
    repo.write("go.mod", "module demo\n")
    assert detect_settings(repo.path) == {
        "commands": {"test": "go test ./...", "lint": "go vet ./...", "build": "go build ./..."}
    }
    set_origin_head(repo, "trunk")
    assert detect_settings(repo.path)["default_branch"] == "trunk"


def test_detect_settings_with_nothing_to_report(repo) -> None:
    assert detect_settings(repo.path) == {}
