"""Load tomlkit with the standard plugin precedence.

tomlkit (a pure-Python universal wheel) is used purely to write servermon.toml
while preserving comments and formatting; the plugin still works without it,
falling back to the regex line editing in config.py.

Precedence (via the SDK's :func:`bigfix_proxyagent.vendor.load_wheel_or_bundled`):
an installed tomlkit, then a loose ``tomlkit-*.whl`` this plugin ships in
``vendor/`` (drop one there to pin a specific version - it wins), then the copy
bundled inside the SDK wheel. So ``vendor/`` normally holds only the SDK wheel.
"""

from __future__ import annotations

from pathlib import Path

from bigfix_proxyagent.vendor import bundled_wheel_name as _bundled
from bigfix_proxyagent.vendor import load_wheel_or_bundled
from bigfix_proxyagent.vendor import vendored_wheel_name as _vendored

VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor"


def load_tomlkit():
    """Return the tomlkit module, or None if it cannot be loaded."""
    return load_wheel_or_bundled("tomlkit", VENDOR_DIR)


def vendored_wheel_name() -> str | None:
    """Basename of the tomlkit wheel in use - a loose one in ``vendor/`` if
    present, else the SDK's bundled copy - for logging/diagnostics.
    """
    return _vendored("tomlkit", VENDOR_DIR) or _bundled("tomlkit")
