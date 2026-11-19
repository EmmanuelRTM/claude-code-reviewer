"""Command line of the installer.

    python3 install.py --target DIR [--set KEY=VALUE ...] [--config FILE]
                       [--dry-run] [--force] [--no-detect] [--uninstall]

Exit codes: 0 on success; 2 for a usage or validation error, including a path
in the target that could not be handled; 1 for an unexpected failure.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Optional, Sequence

from installer.configgen import deep_merge, parse_assignments
from installer.errors import InstallError
from installer.operations import install, uninstall
from installer.report import Report, format_report

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_INVALID = 2

_EPILOG = """\
examples:
  python3 install.py --target ../my-repo --dry-run
  python3 install.py --target ../my-repo --set commands.test="npm test" --set owners=@org/leads,@alice
  python3 install.py --target ../my-repo --set gate.mode=enforce
  python3 install.py --target ../my-repo --uninstall

The install is safe to repeat. Files you own (REVIEW.md, .claude/reviewkit.json, the verify skill, ...)
are created once and never overwritten; kit files you edited are skipped unless --force is given.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="install.py",
        description="Install the verification-first review kit into a git repository, or upgrade or remove it.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--target",
        required=True,
        metavar="DIR",
        help="the repository to install into: the top level of a git work tree",
    )
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="set a value in .claude/reviewkit.json by dotted key, e.g. gate.mode=enforce or "
        "owners=@org/leads,@alice (repeatable; lists are comma-separated)",
    )
    parser.add_argument(
        "--config",
        metavar="FILE",
        help="a JSON file shaped like .claude/reviewkit.json to start from; --set goes on top",
    )
    parser.add_argument("--dry-run", action="store_true", help="print what would be done and write nothing")
    parser.add_argument(
        "--force", action="store_true", help="overwrite kit-owned files even if they were modified locally"
    )
    parser.add_argument("--no-detect", action="store_true", help="do not detect test, lint and build commands")
    parser.add_argument(
        "--uninstall", action="store_true", help="remove what the installer added; files you own are left in place"
    )
    return parser


def _load_config_file(path: Optional[str]) -> dict[str, Any]:
    """Read the `--config` file: a JSON object in the shape of the configuration."""
    if path is None:
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise InstallError(f"--config {path}: cannot be read ({exc.strerror or exc})") from exc
    except ValueError as exc:  # invalid JSON, or not UTF-8
        raise InstallError(f"--config {path}: not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise InstallError(f"--config {path}: expected a JSON object")
    return data


def _run(arguments: argparse.Namespace, payload_root: Optional[Path], blocks_root: Optional[Path]) -> Report:
    target = Path(arguments.target)
    if arguments.uninstall:
        if arguments.set or arguments.config:
            raise InstallError("--uninstall removes the kit; it cannot be combined with --set or --config")
        return uninstall(target, payload_root=payload_root, dry_run=arguments.dry_run, force=arguments.force)
    overrides = deep_merge(_load_config_file(arguments.config), parse_assignments(arguments.set))
    return install(
        target,
        payload_root=payload_root,
        blocks_root=blocks_root,
        overrides=overrides,
        dry_run=arguments.dry_run,
        force=arguments.force,
        detect=not arguments.no_detect,
    )


def _emit(text: str) -> None:
    """Print the report without letting the terminal or a pipe undo a finished run.

    Characters the console cannot encode are shown as escapes. A reader that
    has gone away (`install.py ... | grep -q created`) is not an error: the
    work is done by the time anything is printed.
    """
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        sys.stdout.write(text.encode(encoding, errors="backslashreplace").decode(encoding))
        sys.stdout.flush()
    except BrokenPipeError:
        # Point stdout at the null device so the interpreter does not complain again while exiting.
        with contextlib.suppress(OSError, ValueError):
            sink = os.open(os.devnull, os.O_WRONLY)
            os.dup2(sink, sys.stdout.fileno())
            os.close(sink)


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    payload_root: Optional[Path] = None,
    blocks_root: Optional[Path] = None,
) -> int:
    """Run the installer and return its exit code.

    `payload_root` and `blocks_root` default to this kit's `kit/` and `blocks/`;
    they exist so tests can install a small fixture kit.
    """
    parser = build_parser()
    try:
        arguments = parser.parse_args(argv)
    except SystemExit as exc:  # argparse exits with 0 after --help and 2 after a usage error
        return exc.code if isinstance(exc.code, int) else EXIT_INVALID
    try:
        report = _run(arguments, payload_root, blocks_root)
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INVALID
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("The run stopped part-way. It is safe to run the installer again once this is fixed.", file=sys.stderr)
        return EXIT_FAILURE
    except Exception as exc:
        traceback.print_exc()
        print(f"unexpected failure: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    _emit(format_report(report))
    return EXIT_OK if report.ok else EXIT_INVALID


if __name__ == "__main__":
    raise SystemExit(main())
