"""Errors the installer reports to the person running it."""

from __future__ import annotations


class InstallError(Exception):
    """A problem with the arguments, the kit or the target that the user can fix.

    The command line turns it into a one-line message and exit code 2. Anything
    else that escapes is an unexpected failure (exit code 1).
    """
