"""The SDK bootstrap in servermon/__init__.py: prefer an installed
bigfix_proxyagent, else fall back to the wheel vendored in vendor/.
"""

import sys

import servermon


def test_prefers_installed_sdk(monkeypatch):
    # bigfix_proxyagent is importable here, so nothing is added to sys.path.
    monkeypatch.setattr(sys, "path", list(sys.path))
    before = list(sys.path)
    servermon._ensure_sdk()
    assert sys.path == before


def test_falls_back_to_vendored_wheel(monkeypatch):
    # Simulate the SDK not being installed: the vendored wheel is prepended.
    monkeypatch.setitem(sys.modules, "bigfix_proxyagent", None)
    monkeypatch.setattr(sys, "path", list(sys.path))
    servermon._ensure_sdk()
    assert any(
        p.endswith(".whl") and "bigfix_proxyagent" in p for p in sys.path
    ), "vendored SDK wheel should have been added to sys.path"
