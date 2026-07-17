"""Load and validate the servermon TOML configuration file."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomllib

from .device import device_name

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_USER_AGENT = "bigfix-proxyagent-servermon"

_URL_ENTRY_KEYS = {"url", "match", "no_match", "verify_tls", "timeout_seconds"}


class ConfigError(ValueError):
    """Raised when the configuration file is missing, unreadable, or invalid."""


@dataclass(frozen=True)
class UrlEntry:
    """One monitored URL from a ``[[urls]]`` table."""

    url: str
    match: str | None = None  # case-sensitive substring that must be present
    no_match: str | None = None  # case-insensitive regex that must NOT match
    verify_tls: bool = True
    timeout_seconds: float | None = None  # None -> use the global setting


@dataclass(frozen=True)
class Config:
    urls: tuple[UrlEntry, ...]
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    user_agent: str = DEFAULT_USER_AGENT

    def timeout_for(self, entry: UrlEntry) -> float:
        if entry.timeout_seconds is not None:
            return entry.timeout_seconds
        return self.timeout_seconds


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
    if not isinstance(raw_urls, list) or not raw_urls:
        raise ConfigError(f"{source}: at least one [[urls]] entry is required")

    entries = [
        _parse_url_entry(item, f"{source}: [[urls]] entry {i}")
        for i, item in enumerate(raw_urls, start=1)
    ]

    # Device identity is the scheme-less name, so entries that differ only by
    # scheme or a trailing slash would silently overwrite each other's reports.
    seen: dict[str, str] = {}
    for entry in entries:
        name = device_name(entry.url)
        if name in seen:
            raise ConfigError(
                f"{source}: {entry.url!r} and {seen[name]!r} are both device "
                f"{name!r}; remove one or make the URLs distinct"
            )
        seen[name] = entry.url

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

    match = item.get("match")
    if match is not None and (not isinstance(match, str) or not match):
        raise ConfigError(f"{where}: 'match' must be a non-empty string")

    no_match = item.get("no_match")
    if no_match is not None:
        if not isinstance(no_match, str) or not no_match:
            raise ConfigError(f"{where}: 'no_match' must be a non-empty string")
        try:
            re.compile(no_match)
        except re.error as error:
            raise ConfigError(
                f"{where}: 'no_match' is not a valid regex: {error}"
            ) from error

    verify_tls = item.get("verify_tls", True)
    if not isinstance(verify_tls, bool):
        raise ConfigError(f"{where}: 'verify_tls' must be true or false")

    timeout = item.get("timeout_seconds")
    if timeout is not None and not _is_positive_number(timeout):
        raise ConfigError(f"{where}: 'timeout_seconds' must be a positive number")

    return UrlEntry(
        url=url,
        match=match,
        no_match=no_match,
        verify_tls=verify_tls,
        timeout_seconds=None if timeout is None else float(timeout),
    )


def _is_positive_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
