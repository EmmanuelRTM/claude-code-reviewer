"""`@claude review` comment commands.

Same rules as the managed Code Review service, so habits carry over: the
command is one line at the very start of a top-level pull request comment;
other text may follow; a misspelled or later-line keyword is a plain review.
"""

from __future__ import annotations

import re
from typing import Optional

_COMMAND = re.compile(r"@claude[ \t]+review(?:[ \t]+(\S+))?(?:[ \t].*)?\Z", re.I)
_KEYWORDS = {"always": "always", "once": "review", "stop": "stop", "off": "stop", "pause": "stop"}

COMMAND_PREFIX = "@claude review"


def parse_review_command(body: Optional[str]) -> Optional[str]:
    """Return `review`, `always` or `stop`, or None when the comment is not a command."""
    if not body:
        return None
    first_line = body.split("\n", 1)[0].rstrip("\r")
    match = _COMMAND.match(first_line)
    if not match:
        return None
    return _KEYWORDS.get((match.group(1) or "").lower(), "review")
