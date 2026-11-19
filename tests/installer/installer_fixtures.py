"""Fixtures for the installer tests, loaded with `pytest_plugins = ("installer_fixtures",)`.

This is not a `conftest.py` on purpose: other test directories import names
from the top-level `conftest` module, and a second file of that name would
shadow it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from installer_kit import KitSource, build_kit


@pytest.fixture
def kit(tmp_path: Path) -> KitSource:
    """A miniature kit (payload and block templates) to install instead of the real one."""
    return build_kit(tmp_path / "kit-source")
