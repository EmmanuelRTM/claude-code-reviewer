#!/usr/bin/env python3
"""Entry point for the review kit CLI.

Run as `python3 -I .claude/reviewkit/rk.py <command>`. Isolated mode keeps the
interpreter from importing anything out of the current directory; the library
path is added explicitly here.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from reviewkit.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
