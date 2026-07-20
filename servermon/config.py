"""Load and validate the servermon TOML configuration file."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomllib

from ._vendor import load_tomlkit
from .device import device_name
from .util import write_text_atomic

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_USER_AGENT = "bigfix-proxyagent-servermon"

# Matches the DeviceReportRefreshIntervalMinutes in the settings.json this
# plugin ships with; used only if that file cannot be read.
DEFAULT_HEARTBEAT_MINUTES = 60
PLUGIN_SETTINGS_JSON = Path(__file__).resolve().parent.parent / "settings.json"

_URL_ENTRY_KEYS = {
    "url",
    "match",
    "no_match",
    "verify_tls",
    "timeout_seconds",
    "check_interval_minutes",
    "measure_network_hops",
}


class ConfigError(ValueError):
    """Raised when the configuration file is missing, unreadable, or invalid."""


@dataclass(frozen=True)
class UrlEntry:
    """One monitored URL from a ``[[urls]]`` table."""

    url: str
    match: str | None = None  # case-insensitive regex that must match
    no_match: str | None = None  # case-insensitive regex that must NOT match
    verify_tls: bool = True
    timeout_seconds: float | None = None  # None -> use the global setting
    # Minimum minutes between checks of this URL; None -> check on every
    # Proxy Agent refresh. Effectively rounds up to a multiple of the
    # heartbeat (DeviceReportRefreshIntervalMinutes in settings.json), since
    # the plugin only runs when the Proxy Agent invokes it.
    check_interval_minutes: int | None = None
    # Opt-in network hop count measurement (TTL binary search over plain TCP
    # connects). Rides along with 1 in every HOPS_EVERY_N_CHECKS (6) regular
    # checks of this URL - there is deliberately no separate interval setting.
    measure_network_hops: bool = False


@dataclass(frozen=True)
class Config:
    urls: tuple[UrlEntry, ...]
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    user_agent: str = DEFAULT_USER_AGENT

    def timeout_for(self, entry: UrlEntry) -> float:
        if entry.timeout_seconds is not None:
            return entry.timeout_seconds
        return self.timeout_seconds


def heartbeat_minutes(settings_path: Path | None = None) -> int:
    """The Proxy Agent heartbeat (DeviceReportRefreshIntervalMinutes) from
    the plugin's settings.json - the default check interval for URLs without.

    their own check_interval_minutes.
    """
    path = settings_path if settings_path is not None else PLUGIN_SETTINGS_JSON
    try:
        with path.open("r", encoding="utf-8") as f:
            settings = json.load(f)
        value = settings.get("DeviceReportRefreshIntervalMinutes")
        if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
            return value
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, AttributeError):
        pass
    return DEFAULT_HEARTBEAT_MINUTES


def load_config(path: Path | str) -> Config:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        with path.open("rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"invalid TOML in {path}: {error}") from error
    return parse_config(raw, source=str(path))


def parse_config(raw: dict[str, Any], source: str = "<config>") -> Config:
    settings = raw.get("settings", {})
    if not isinstance(settings, dict):
        raise ConfigError(f"{source}: [settings] must be a table")

    timeout = settings.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    if not _is_positive_number(timeout):
        raise ConfigError(
            f"{source}: settings.timeout_seconds must be a positive number"
        )

    user_agent = settings.get("user_agent", DEFAULT_USER_AGENT)
    if not isinstance(user_agent, str) or not user_agent:
        raise ConfigError(f"{source}: settings.user_agent must be a non-empty string")

    raw_urls = raw.get("urls")
    if not isinstance(raw_urls, list):
        # An explicitly-empty list is allowed (e.g. after "delete device"
        # removed the last entry); a missing key is still a config mistake.
        raise ConfigError(
            f"{source}: at least one [[urls]] entry is required (or urls = [])"
        )

    entries = [
        _parse_url_entry(item, f"{source}: [[urls]] entry {i}")
        for i, item in enumerate(raw_urls, start=1)
    ]

    # Device identity is the scheme-less name, so entries that differ only by
    # scheme or a trailing slash would silently overwrite each other's reports.
    seen: dict[str, tuple[int, str]] = {}
    for index, entry in enumerate(entries, start=1):
        name = device_name(entry.url)
        if name in seen:
            other_index, other_url = seen[name]
            if entry.url == other_url:
                raise ConfigError(
                    f"{source}: [[urls]] entries {other_index} and {index} are "
                    f"exact duplicates of {entry.url!r}; remove one"
                )
            raise ConfigError(
                f"{source}: [[urls]] entry {other_index} ({other_url!r}) and "
                f"entry {index} ({entry.url!r}) are both device {name!r}; "
                "remove one or make the URLs distinct"
            )
        seen[name] = (index, entry.url)

    return Config(
        urls=tuple(entries),
        timeout_seconds=float(timeout),
        user_agent=user_agent,
    )


def _parse_url_entry(item: Any, where: str) -> UrlEntry:
    if not isinstance(item, dict):
        raise ConfigError(f"{where}: must be a table")

    unknown = set(item) - _URL_ENTRY_KEYS
    if unknown:
        raise ConfigError(f"{where}: unknown key(s): {', '.join(sorted(unknown))}")

    url = item.get("url")
    if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
        raise ConfigError(
            f"{where}: 'url' must be a string starting with http:// or https://"
        )

    match = _parse_regex_option(item, "match", where)
    no_match = _parse_regex_option(item, "no_match", where)

    verify_tls = item.get("verify_tls", True)
    if not isinstance(verify_tls, bool):
        raise ConfigError(f"{where}: 'verify_tls' must be true or false")

    measure_hops = item.get("measure_network_hops", False)
    if not isinstance(measure_hops, bool):
        raise ConfigError(f"{where}: 'measure_network_hops' must be true or false")

    timeout = item.get("timeout_seconds")
    if timeout is not None and not _is_positive_number(timeout):
        raise ConfigError(f"{where}: 'timeout_seconds' must be a positive number")

    interval = item.get("check_interval_minutes")
    if interval is not None and (
        not isinstance(interval, int) or isinstance(interval, bool) or interval < 1
    ):
        raise ConfigError(
            f"{where}: 'check_interval_minutes' must be a positive integer"
        )

    return UrlEntry(
        url=url,
        match=match,
        no_match=no_match,
        verify_tls=verify_tls,
        timeout_seconds=None if timeout is None else float(timeout),
        check_interval_minutes=interval,
        measure_network_hops=measure_hops,
    )


# Line-level patterns for in-place TOML edits (comments/formatting preserved).
_URL_LINE_RE = re.compile(r"^\s*url\s*=\s*([\"'])(?P<url>.*)\1\s*(#.*)?$")
_INTERVAL_LINE_RE = re.compile(r"^\s*check_interval_minutes\s*=")
_TABLE_HEADER_RE = re.compile(r"^\s*\[")
_URLS_HEADER_RE = re.compile(r"^\s*\[\[urls\]\]")


def set_url_check_interval(path: Path | str, url: str, minutes: int) -> None:
    """Set ``check_interval_minutes`` for one ``[[urls]]`` entry by editing
    the TOML file in place, preserving comments and formatting.

    Used by the "set refresh interval" action command. Prefers the vendored
    tomlkit; falls back to regex line editing when it is unavailable. Raises
    ConfigError if the entry cannot be found or the edit would not parse.
    """
    path = Path(path)
    tomlkit = load_tomlkit()
    if tomlkit is not None:
        _edit_with_tomlkit(
            path, url, tomlkit, lambda table: table.__setitem__(
                "check_interval_minutes", minutes
            )
        )
    else:
        _set_url_check_interval_regex(path, url, minutes)


def remove_url_entry(path: Path | str, url: str) -> None:
    """Remove one ``[[urls]]`` entry from the TOML file (in-place edit).

    Used by the "delete device" action command. Prefers the vendored
    tomlkit; falls back to regex line editing. If the last entry is removed,
    ``urls = []`` is left behind so the file still loads.
    """
    path = Path(path)
    tomlkit = load_tomlkit()
    if tomlkit is not None:
        _remove_with_tomlkit(path, url, tomlkit)
    else:
        _remove_url_entry_regex(path, url)


def _edit_with_tomlkit(path: Path, url: str, tomlkit, mutate) -> None:
    """Load the config with tomlkit, apply ``mutate`` to the matching url's
    table, and write it back (comments/formatting preserved).
    """
    doc = _load_tomlkit_doc(path, tomlkit)
    for table in doc.get("urls", []):
        if table.get("url") == url:
            mutate(table)
            _write_validated_config_text(path, tomlkit.dumps(doc))
            return
    raise ConfigError(f"{path}: no [[urls]] entry with url = {url!r} found")


def _remove_with_tomlkit(path: Path, url: str, tomlkit) -> None:
    doc = _load_tomlkit_doc(path, tomlkit)
    urls = doc.get("urls")
    if urls is not None:
        for i, table in enumerate(urls):
            if table.get("url") == url:
                del urls[i]
                # tomlkit drops the key entirely once the array is empty;
                # leave "urls = []" so the config still parses (parse_config
                # requires the key to be present).
                if len(doc.get("urls", [])) == 0:
                    doc["urls"] = []
                _write_validated_config_text(path, tomlkit.dumps(doc))
                return
    raise ConfigError(f"{path}: no [[urls]] entry with url = {url!r} found")


def _load_tomlkit_doc(path: Path, tomlkit):
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ConfigError(f"cannot read {path}: {error}") from error
    try:
        return tomlkit.parse(text)
    except Exception as error:
        raise ConfigError(f"invalid TOML in {path}: {error}") from error


def _set_url_check_interval_regex(path: Path, url: str, minutes: int) -> None:
    """Tomlkit-free fallback for :func:`set_url_check_interval`."""
    lines = _read_config_lines(path)
    url_index = _find_url_line(lines, url, path)

    # This entry's table ends at the next table header (or EOF).
    end = next(
        (
            i
            for i in range(url_index + 1, len(lines))
            if _TABLE_HEADER_RE.match(lines[i])
        ),
        len(lines),
    )

    new_line = f"check_interval_minutes = {minutes}"
    for i in range(url_index + 1, end):
        if _INTERVAL_LINE_RE.match(lines[i]):
            lines[i] = new_line
            break
    else:
        lines.insert(url_index + 1, new_line)

    _write_validated_config(path, lines)


def _remove_url_entry_regex(path: Path, url: str) -> None:
    """Tomlkit-free fallback for :func:`remove_url_entry`."""
    lines = _read_config_lines(path)
    url_index = _find_url_line(lines, url, path)

    start = next(
        (i for i in range(url_index, -1, -1) if _URLS_HEADER_RE.match(lines[i])),
        None,
    )
    if start is None:
        raise ConfigError(f"{path}: no [[urls]] header found above url = {url!r}")
    end = next(
        (
            i
            for i in range(url_index + 1, len(lines))
            if _TABLE_HEADER_RE.match(lines[i])
        ),
        len(lines),
    )

    del lines[start:end]
    if not any(_URLS_HEADER_RE.match(line) for line in lines):
        # Top-level keys must appear before any table header.
        lines.insert(0, "urls = []")

    _write_validated_config(path, lines)


def _read_config_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ConfigError(f"cannot read {path}: {error}") from error


def _find_url_line(lines: list[str], url: str, path: Path) -> int:
    for i, line in enumerate(lines):
        found = _URL_LINE_RE.match(line)
        if found and found.group("url") == url:
            return i
    raise ConfigError(f"{path}: no [[urls]] entry with url = {url!r} found")


def _write_validated_config(path: Path, lines: list[str]) -> None:
    _write_validated_config_text(path, "\n".join(lines) + "\n")


def _write_validated_config_text(path: Path, new_text: str) -> None:
    # Refuse to write a config that would not load back (stdlib tomllib is
    # the source of truth for what the plugin will actually parse).
    try:
        parse_config(tomllib.loads(new_text), source=str(path))
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"edit would corrupt {path}: {error}") from error
    write_text_atomic(path, new_text)


def _parse_regex_option(item: dict[str, Any], key: str, where: str) -> str | None:
    value = item.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where}: {key!r} must be a non-empty string")
    try:
        re.compile(value)
    except re.error as error:
        raise ConfigError(f"{where}: {key!r} is not a valid regex: {error}") from error
    return value


def _is_positive_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
