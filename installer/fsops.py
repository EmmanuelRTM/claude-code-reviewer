"""Filesystem access for the installer: validated paths, no symlinks, atomic writes.

Every path the installer touches is a target-relative POSIX path. It is checked
here before use, so nothing is written outside the target directory or through
a symbolic link, whoever supplied the path (the payload, or a manifest read
back from the target repository).
"""

from __future__ import annotations

import contextlib
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from installer.errors import InstallError

_BYTECODE_CACHE = "__pycache__"
_BYTECODE_SUFFIXES = (".pyc", ".pyo")


class UnsafePathError(InstallError):
    """A path that must not be touched: it leaves the target or crosses a symbolic link."""


@dataclass(frozen=True)
class Existing:
    """What is at a destination now. A `problem` means: do not read or write it."""

    problem: Optional[str] = None
    content: Optional[bytes] = None

    @property
    def exists(self) -> bool:
        return self.content is not None


def relative_parts(relative: str) -> tuple[str, ...]:
    """Split a target-relative POSIX path into its parts, or raise `UnsafePathError`.

    Rejects anything that could resolve outside the target (absolute paths,
    `..`, backslashes, drive letters) and anything inside git's own metadata.
    """
    if not relative or relative.startswith("/") or any(char in relative for char in "\0\\:"):
        raise UnsafePathError(f"unsafe path: {relative!r}")
    parts = tuple(relative.split("/"))
    for part in parts:
        if part in ("", ".", ".."):
            raise UnsafePathError(f"unsafe path: {relative!r}")
        if part.lower() == ".git":
            raise UnsafePathError(f"path is inside git metadata: {relative!r}")
    return parts


def _problem(root: Path, parts: tuple[str, ...]) -> Optional[str]:
    """Why the path must not be touched, checking each existing component without following links."""
    current = Path(root)
    last = len(parts) - 1
    for index, part in enumerate(parts):
        current = current / part
        shown = "/".join(parts[: index + 1])
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            return None  # nothing here, so nothing below it either
        except OSError as exc:
            return f"cannot inspect {shown} ({exc.strerror or exc})"
        if stat.S_ISLNK(info.st_mode):
            subject = "" if index == last else f"parent directory {shown} "
            return f"{subject}is a symbolic link (refusing to write through it)"
        if index < last and not stat.S_ISDIR(info.st_mode):
            return f"{shown} is not a directory"
        if index == last and not stat.S_ISREG(info.st_mode):
            return "exists but is not a regular file"
    return None


def inspect(root: Path, relative: str) -> Existing:
    """Read the destination's current state without following any symbolic link."""
    parts = relative_parts(relative)
    problem = _problem(root, parts)
    if problem:
        return Existing(problem=problem)
    try:
        return Existing(content=Path(root).joinpath(*parts).read_bytes())
    except FileNotFoundError:
        return Existing()
    except OSError as exc:
        return Existing(problem=f"cannot be read ({exc.strerror or exc})")


def _umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask


def _mode(existing: Optional[int], executable: Optional[bool]) -> int:
    """Permission bits for a write: keep what is there, follow the umask for new files."""
    base = existing if existing is not None else 0o666 & ~_umask()
    if executable is None:
        return base
    if executable:
        return base | ((base & 0o444) >> 2)  # executable for whoever may read
    return base & ~0o111


def write_file(root: Path, relative: str, data: bytes, *, executable: Optional[bool] = None) -> None:
    """Write `data` atomically: a temporary file in the same directory, then a rename.

    `executable` sets or clears the executable bit; `None` keeps the mode of the
    file being replaced. Raises `UnsafePathError` rather than write through a link.
    """
    parts = relative_parts(relative)
    problem = _problem(root, parts)
    if problem:
        raise UnsafePathError(f"{relative}: {problem}")
    destination = Path(root).joinpath(*parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing_mode: Optional[int] = stat.S_IMODE(os.lstat(destination).st_mode)
    except FileNotFoundError:
        existing_mode = None
    descriptor, temporary = tempfile.mkstemp(dir=str(destination.parent), prefix=f".{destination.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            with contextlib.suppress(OSError):
                os.fsync(handle.fileno())
        os.chmod(temporary, _mode(existing_mode, executable))
        os.replace(temporary, destination)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def delete_file(root: Path, relative: str) -> None:
    """Remove a regular file. A file that is already gone is not an error."""
    parts = relative_parts(relative)
    problem = _problem(root, parts)
    if problem:
        raise UnsafePathError(f"{relative}: {problem}")
    with contextlib.suppress(FileNotFoundError):
        os.unlink(Path(root).joinpath(*parts))


def _is_real_directory(root: Path, parts: tuple[str, ...]) -> bool:
    current = Path(root)
    for part in parts:
        current = current / part
        try:
            if not stat.S_ISDIR(os.lstat(current).st_mode):
                return False
        except OSError:
            return False
    return True


def _within(parts: tuple[str, ...], allowed_roots: Sequence[str]) -> bool:
    return any(parts[: len(allowed)] == allowed for allowed in (tuple(root.split("/")) for root in allowed_roots))


def _drop_bytecode_cache(directory: Path) -> None:
    """Delete `__pycache__` when it is all that is left and holds only bytecode.

    Running the kit leaves these caches next to its modules; without this an
    uninstall would leave the kit's directories behind.
    """
    try:
        if os.listdir(directory) != [_BYTECODE_CACHE]:
            return
        cache = directory / _BYTECODE_CACHE
        if not stat.S_ISDIR(os.lstat(cache).st_mode):
            return
        entries = [cache / name for name in os.listdir(cache)]
        if not all(
            entry.name.endswith(_BYTECODE_SUFFIXES) and stat.S_ISREG(os.lstat(entry).st_mode) for entry in entries
        ):
            return
        for entry in entries:
            os.unlink(entry)
        os.rmdir(cache)
    except OSError:
        return


def prune_empty_dirs(root: Path, relative_dir: str, allowed_roots: Sequence[str]) -> None:
    """Remove `relative_dir` and its parents while they are empty.

    Only directories at or below one of `allowed_roots` are ever removed, and
    never through a symbolic link. A directory with content ends the walk.
    """
    parts = relative_parts(relative_dir)
    while parts and _within(parts, allowed_roots) and _is_real_directory(root, parts):
        directory = Path(root).joinpath(*parts)
        _drop_bytecode_cache(directory)
        try:
            os.rmdir(directory)
        except OSError:
            return
        parts = parts[:-1]
