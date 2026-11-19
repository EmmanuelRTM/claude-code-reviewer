#!/usr/bin/env python3
"""PreToolUse hook on `git commit`: was this exact tree verified?

Thin entry point. The logic lives in `reviewkit.hooks.commit_gate`.
Exit 0 lets the commit proceed, exit 2 blocks it with the reason on stderr.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "reviewkit" / "lib"))

try:
    from reviewkit.hooks.commit_gate import main
except Exception as exc:  # noqa: BLE001 - a broken install must be visible but never block
    print(f"reviewkit: commit gate unavailable ({type(exc).__name__}: {exc})", file=sys.stderr)
    raise SystemExit(1) from None

raise SystemExit(main())
