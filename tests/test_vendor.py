"""Failure paths of the vendored-wheel loader (the happy path is covered by
test_config.py's test_vendored_tomlkit_is_loadable).
"""

import sys

import pytest

import servermon._vendor
from servermon._vendor import load_tomlkit, vendored_wheel_name


@pytest.fixture
def no_installed_tomlkit(monkeypatch):
    """Make ``import tomlkit`` fail so the wheel path is exercised."""
    # A None entry in sys.modules makes the import raise ImportError.
    monkeypatch.setitem(sys.modules, "tomlkit", None)
    # Snapshot sys.path so bogus wheel entries do not leak into other tests.
    monkeypatch.setattr(sys, "path", list(sys.path))


def test_no_wheel_returns_none(tmp_path, monkeypatch, no_installed_tomlkit):
    monkeypatch.setattr(servermon._vendor, "VENDOR_DIR", tmp_path)
    assert load_tomlkit() is None
    assert vendored_wheel_name() is None


def test_corrupt_wheel_returns_none(tmp_path, monkeypatch, no_installed_tomlkit):
    # A corrupt/incompatible wheel must not take the plugin down; callers
    # fall back to the regex editing path.
    (tmp_path / "tomlkit-0.0.0-py3-none-any.whl").write_bytes(b"not a zip")
    monkeypatch.setattr(servermon._vendor, "VENDOR_DIR", tmp_path)
    assert load_tomlkit() is None
