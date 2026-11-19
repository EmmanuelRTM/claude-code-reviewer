#!/usr/bin/env python3
"""Install the review kit into a git repository.

    python3 install.py --target DIR [--set KEY=VALUE ...] [--config FILE]
                       [--dry-run] [--force] [--no-detect] [--uninstall]

A thin entry point: the work is done by the `installer` package next to this
file. Standard library only, Python 3.9 or newer.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Deliberate even though 3.9 is the minimum: an older interpreter gets this message, not a traceback.
if sys.version_info < (3, 9):  # noqa: UP036
    sys.exit("install.py needs Python 3.9 or newer")

# Find the package however the script is started (`python3 -I`, another working directory).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from installer.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
