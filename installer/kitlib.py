"""Where this kit lives on disk, and access to its `reviewkit` library.

The installer generates `.claude/reviewkit.json` from the library's own
configuration dataclasses, so defaults and validation have a single source.
The library is imported from this kit's payload, never from a target repository.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PAYLOAD_ROOT = REPO_ROOT / "kit"
DEFAULT_BLOCKS_ROOT = REPO_ROOT / "blocks"
LIBRARY_ROOT = DEFAULT_PAYLOAD_ROOT / ".claude" / "reviewkit" / "lib"


def _load_config_module() -> ModuleType:
    """Import `reviewkit.config`, putting this kit's library directory first on the path."""
    library = str(LIBRARY_ROOT)
    if library not in sys.path:
        sys.path.insert(0, library)
    return importlib.import_module("reviewkit.config")


_config = _load_config_module()

CONFIG_RELATIVE_PATH: str = _config.CONFIG_RELATIVE_PATH
Config = _config.Config
ConfigError = _config.ConfigError
config_to_dict = _config.config_to_dict
parse_config = _config.parse_config
