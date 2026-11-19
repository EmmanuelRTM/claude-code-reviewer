#!/usr/bin/env python3
"""Stop hook: the project's checks must pass before Claude may finish a turn.

Thin entry point. The logic lives in `reviewkit.hooks.stop_checks`.
Exit 0 lets the turn end, exit 2 sends Claude back to work with the failure.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "reviewkit" / "lib"))

try:
    from reviewkit.hooks.stop_checks import main
except Exception as exc:  # noqa: BLE001 - a broken install must be visible but never block
    print(f"reviewkit: checks on stop unavailable ({type(exc).__name__}: {exc})", file=sys.stderr)
    raise SystemExit(1) from None

raise SystemExit(main())
