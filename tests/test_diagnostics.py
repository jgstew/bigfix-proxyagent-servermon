"""Structural checks on diagnostics.bat.

The script itself only runs on Windows (and exists precisely for when Python is
missing), so it cannot be executed here. These tests guard the file-format
properties that make cmd.exe misbehave silently when they are wrong.
"""

import json
import os
import shutil
import subprocess
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


def test_offers_certifi_install_for_all_users(raw):
    # Missing certifi is offered for install with the found interpreter. -s
    # keeps pip from treating a per-user copy as "already satisfied", the
    # prompt goes to the console so a redirected run still shows it, and it
    # defaults to No on timeout so an unattended run cannot hang.
    text = raw.decode("ascii")
    assert "%TARGET% -s -m pip install" in text
    assert "certifi" in text
    assert "/d N" in text and ">con" in text
    assert "/install-certifi" in text


def test_offers_interpreter_switch_before_certifi(raw):
    # A per-user or missing interpreter is offered a switch to py -3 or an
    # all-users python.exe; that must come before the certifi offer so certifi
    # lands in the interpreter the service will actually use.
    text = raw.decode("ascii")
    assert "py -3" in text
    assert "run --no-project --python" in text  # uv
    assert text.index("call :offer_switch") < text.index("call :offer_certifi")


def _ps_snippet(raw: bytes, marker: str) -> str:
    """The PowerShell -Command text of the %PSRUN% line containing marker."""
    for line in raw.decode("ascii").splitlines():
        if line.startswith('%PSRUN% "') and marker in line:
            assert line.endswith('"')
            return line[len('%PSRUN% "') : -1]
    raise AssertionError(f"no %PSRUN% line containing {marker!r}")


@pytest.mark.skipif(not shutil.which("pwsh"), reason="needs PowerShell (pwsh)")
@pytest.mark.parametrize(
    "new_exec",
    [
        r'py -3 "C:\Program Files (x86)\BigFix\Plugins\sm\plugin\servermon.py"',
        r'"C:\Program Files\Python312\python.exe" "C:\p\servermon.py" --x',
        r'"C:\Program Files\uv\uv.exe" run --no-project --python ">=3.11" "C:\p\s.py"',
    ],
)
def test_settings_rewrite_changes_only_executable_path(raw, tmp_path, new_exec):
    # Runs the script's own rewrite snippet: only ExecutablePath changes, the
    # rest of the file is byte-identical, and a backup of the original exists.
    original = (BAT.parent / "settings.json").read_text(encoding="utf-8")
    settings = tmp_path / "settings.json"
    settings.write_text(original, encoding="utf-8")
    out = tmp_path / "out.txt"
    env = dict(
        os.environ,
        SETTINGS=str(settings),
        NEWEXEC=new_exec,
        DIAG_OUT=str(out),
    )
    snippet = _ps_snippet(raw, "ConvertTo-Json")
    subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command", snippet],
        env=env,
        check=True,
        timeout=60,
    )
    updated = settings.read_text(encoding="utf-8")
    assert json.loads(updated)["ExecutablePath"] == new_exec
    old_doc, new_doc = json.loads(original), json.loads(updated)
    del old_doc["ExecutablePath"], new_doc["ExecutablePath"]
    assert old_doc == new_doc
    old_line = next(ln for ln in original.splitlines() if "ExecutablePath" in ln)
    assert updated.replace(
        next(ln for ln in updated.splitlines() if "ExecutablePath" in ln), old_line
    ) == original
    backup = Path(out.read_text(encoding="utf-8").strip())
    assert backup.name.startswith("settings.json.") and backup.suffix == ".bak"
    assert backup.read_text(encoding="utf-8") == original
