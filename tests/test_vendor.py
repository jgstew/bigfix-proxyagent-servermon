"""Servermon._vendor loads tomlkit with the standard plugin precedence (the
happy path is covered by test_config.py's test_vendored_tomlkit_is_loadable).
"""

import servermon._vendor
from servermon._vendor import load_tomlkit, vendored_wheel_name


def test_load_tomlkit_uses_precedence_with_plugin_vendor_dir(monkeypatch):
    seen = {}

    def fake(name, vendor_dir):
        seen.update(name=name, vendor_dir=vendor_dir)
        return "TK"

    monkeypatch.setattr(servermon._vendor, "load_wheel_or_bundled", fake)
    assert load_tomlkit() == "TK"
    assert seen["name"] == "tomlkit"
    assert seen["vendor_dir"] == servermon._vendor.VENDOR_DIR


def test_vendored_wheel_name_prefers_loose_wheel(monkeypatch):
    monkeypatch.setattr(
        servermon._vendor, "_vendored", lambda name, vendor_dir: "tomlkit-9.9.9.whl"
    )
    monkeypatch.setattr(servermon._vendor, "_bundled", lambda name: "tomlkit-0.15.1.whl")
    assert vendored_wheel_name() == "tomlkit-9.9.9.whl"


def test_vendored_wheel_name_falls_back_to_bundled(monkeypatch):
    monkeypatch.setattr(servermon._vendor, "_vendored", lambda name, vendor_dir: None)
    monkeypatch.setattr(servermon._vendor, "_bundled", lambda name: "tomlkit-0.15.1.whl")
    assert vendored_wheel_name() == "tomlkit-0.15.1.whl"
