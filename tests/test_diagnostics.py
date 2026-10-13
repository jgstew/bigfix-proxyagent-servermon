"""Structural checks on diagnostics.bat.

The script itself only runs on Windows (and exists precisely for when Python is
missing), so it cannot be executed here. These tests guard the file-format
properties that make cmd.exe misbehave silently when they are wrong.
"""

from pathlib import Path

import pytest

BAT = Path(__file__).resolve().parent.parent / "diagnostics.bat"


@pytest.fixture(scope="module")
def raw() -> bytes:
    assert BAT.is_file(), "diagnostics.bat should ship in the plugin root"
    return BAT.read_bytes()


def test_crlf_line_endings(raw):
    # cmd.exe mis-parses LF-only batch files (labels/goto can land mid-line).
    assert raw.endswith(b"\r\n")
    assert raw.count(b"\n") == raw.count(b"\r\n"), "every line must end CRLF"


def test_ascii_only(raw):
    # cmd.exe reads a .bat in the console OEM code page, not UTF-8.
    raw.decode("ascii")


def test_reads_settings_next_to_itself(raw):
    # The script must diagnose the settings.json beside it, wherever the plugin
    # folder lives, not a hardcoded install path.
    text = raw.decode("ascii")
    assert "%~dp0" in text
    assert "settings.json" in text
    assert "Program Files (x86)" not in text


def test_runs_the_plugin_validate_end_to_end(raw):
    # The final check runs the exact ExecutablePath with --validate.
    assert b"--validate" in raw
