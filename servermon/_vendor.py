"""Load vendored third-party packages shipped as wheels in ``vendor/``.

The plugin is otherwise standard-library only. tomlkit is vendored (as a
pure-Python universal wheel) purely to write servermon.toml while preserving
comments and formatting; the plugin still works without it, falling back to
the regex line editing in config.py.
"""

from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor"


def load_tomlkit():
    """Return the tomlkit module, or None if it cannot be loaded.

    Uses tomlkit if it is already importable (e.g. pip-installed); otherwise
    adds the vendored wheel to sys.path and imports from there (a wheel is a
    zip, and tomlkit is pure Python, so zipimport handles it). Any failure
    returns None so callers can fall back rather than crash.
    """
    try:
        import tomlkit

        return tomlkit
    except ImportError:
        pass

    try:
        wheels = glob.glob(str(VENDOR_DIR / "tomlkit-*.whl"))
        if not wheels:
            return None
        wheel = sorted(wheels)[-1]
        if wheel not in sys.path:
            sys.path.insert(0, wheel)
        import tomlkit

        return tomlkit
    except Exception:
        # A corrupt/incompatible wheel must not take the plugin down.
        return None


# Basename of the vendored wheel, for logging/diagnostics.
def vendored_wheel_name() -> str | None:
    wheels = glob.glob(str(VENDOR_DIR / "tomlkit-*.whl"))
    return os.path.basename(sorted(wheels)[-1]) if wheels else None
