"""Load the vendored tomlkit wheel via the bigfix-proxyagent SDK's loader.

tomlkit is vendored (a pure-Python universal wheel) purely to write
servermon.toml while preserving comments and formatting; the plugin still
works without it, falling back to the regex line editing in config.py.
"""

from __future__ import annotations

from pathlib import Path

from bigfix_proxyagent.vendor import load_wheel
from bigfix_proxyagent.vendor import vendored_wheel_name as _vendored

VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor"


def load_tomlkit():
    """Return the tomlkit module, or None if it cannot be loaded."""
    return load_wheel("tomlkit", VENDOR_DIR)


def vendored_wheel_name() -> str | None:
    """Basename of the vendored tomlkit wheel, for logging/diagnostics."""
    return _vendored("tomlkit", VENDOR_DIR)
