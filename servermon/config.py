"""Load and validate the servermon TOML configuration file."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomllib
from bigfix_proxyagent.config import (ConfigError, add_aot_entry,
                                      clear_aot_option, remove_aot_entry,
                                      resolve_refresh_interval,
                                      resolve_timeout_seconds, set_aot_option)

from .device import device_id, device_name, device_name_with_port

__all__ = ["ConfigError", "Config", "UrlEntry", "load_config", "parse_config"]

DEFAULT_USER_AGENT = "bigfix-proxyagent-servermon"

_URL_ENTRY_KEYS = {
    "url",
    "match",
    "no_match",
    "verify_tls",
    "timeout_seconds",
    "refresh_interval_minutes",
    "measure_network_hops",
}


@dataclass(frozen=True)
class UrlEntry:
    """One monitored URL from a ``[[urls]]`` table."""

    url: str
    match: str | None = None  # case-insensitive regex that must match
    no_match: str | None = None  # case-insensitive regex that must NOT match
    verify_tls: bool = True
    # Per-request timeout (seconds); None -> the [settings] default, else 45.
    # Bounded to 2-900 when applied (see Config.timeout_for).
    timeout_seconds: float | None = None
    # Minutes between checks of this URL; None -> use the [settings] default,
    # else 30. Bounded to 1-10080 when applied (see Config.refresh_interval_for).
    # The plugin only runs when the Proxy Agent invokes it, so this effectively
    # rounds up to a multiple of that heartbeat.
    refresh_interval_minutes: int | None = None
    # Opt-in network hop count measurement (TTL binary search over plain TCP
    # connects). Rides along with 1 in every HOPS_EVERY_N_CHECKS (6) regular
    # checks of this URL - there is deliberately no separate interval setting.
    measure_network_hops: bool = False


@dataclass(frozen=True)
class Config:
    urls: tuple[UrlEntry, ...]
    # Plugin-wide [settings] defaults; None -> the SDK default (timeout 45s,
    # refresh 30 min). A per-URL value overrides each.
    timeout_seconds: float | None = None
    user_agent: str = DEFAULT_USER_AGENT
    refresh_interval_minutes: int | None = None
    # State storage backend: "json" (default; human-readable, ideal for
    # development/testing) or "sqlite" (better with many devices). Only advisory
    # - if a SQLite state file already exists the SDK uses it regardless, and
    # selecting "sqlite" migrates an existing JSON state file once (never back).
    state_backend: str = "json"

    def timeout_for(self, entry: UrlEntry) -> float:
        """Effective per-request timeout (seconds) for one URL: the per-URL
        value, else the [settings] default, else 45 - bounded to [2, 900], with.

        an out-of-range low value falling back to the 45-second default.
        """
        return resolve_timeout_seconds(
            entry.timeout_seconds, self.timeout_seconds
        )

    def refresh_interval_for(self, entry: UrlEntry) -> int:
        """Effective check cadence (minutes) for one URL: the per-URL value,
        else the [settings] default, else 30 - bounded to [1, 10080], with an.

        out-of-range low value falling back to the 30-minute default.
        """
        return resolve_refresh_interval(
            entry.refresh_interval_minutes, self.refresh_interval_minutes
        )

    def display_name(self, entry: UrlEntry) -> str:
        """Console "computer name" for an entry: the scheme-less device name,
        with the effective default port inserted when another entry shares.

        that base name (so the http/https forms of one host stay
        distinguishable). BigFix tolerates duplicate names; this just keeps
        them legible.
        """
        base = device_name(entry.url)
        collision = sum(1 for other in self.urls if device_name(other.url) == base) > 1
        return device_name_with_port(entry.url) if collision else base


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

    # Plugin-wide default timeout. A positive number is required; the effective
    # value is bounded per URL by Config.timeout_for (out-of-range positives are
    # normalized). Absent -> None, so the SDK default (45s) applies.
    timeout = settings.get("timeout_seconds")
    if timeout is not None and not _is_positive_number(timeout):
        raise ConfigError(
            f"{source}: settings.timeout_seconds must be a positive number"
        )

    user_agent = settings.get("user_agent", DEFAULT_USER_AGENT)
    if not isinstance(user_agent, str) or not user_agent:
        raise ConfigError(f"{source}: settings.user_agent must be a non-empty string")

    # Plugin-wide default cadence. Any integer is accepted; the effective value
    # is bounded per URL by Config.refresh_interval_for (out-of-range values are
    # normalized, not rejected).
    settings_interval = settings.get("refresh_interval_minutes")
    if settings_interval is not None and (
        not isinstance(settings_interval, int) or isinstance(settings_interval, bool)
    ):
        raise ConfigError(
            f"{source}: settings.refresh_interval_minutes must be an integer"
        )

    # State backend selection (see Config.state_backend). Advisory only, so an
    # unknown value is a config mistake worth rejecting rather than normalizing.
    state_backend = settings.get("state_backend", "json")
    if state_backend not in ("json", "sqlite"):
        raise ConfigError(
            f'{source}: settings.state_backend must be "json" or "sqlite"'
        )

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

    # Device identity is the normalized full URL, so only entries that resolve
    # to the same device id (identical apart from scheme case or a trailing
    # slash) would silently overwrite each other's reports. Entries that differ
    # only by scheme are now distinct devices; any resulting display-name clash
    # is disambiguated by Config.display_name rather than rejected here.
    seen: dict[str, tuple[int, str]] = {}
    for index, entry in enumerate(entries, start=1):
        did = device_id(entry.url)
        if did in seen:
            other_index, other_url = seen[did]
            if entry.url == other_url:
                raise ConfigError(
                    f"{source}: [[urls]] entries {other_index} and {index} are "
                    f"exact duplicates of {entry.url!r}; remove one"
                )
            raise ConfigError(
                f"{source}: [[urls]] entry {other_index} ({other_url!r}) and "
                f"entry {index} ({entry.url!r}) are the same device "
                "(identical apart from scheme case or a trailing slash); "
                "remove one or make the URLs distinct"
            )
        seen[did] = (index, entry.url)

    return Config(
        urls=tuple(entries),
        timeout_seconds=None if timeout is None else float(timeout),
        user_agent=user_agent,
        refresh_interval_minutes=settings_interval,
        state_backend=state_backend,
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

    # Any integer is accepted; the effective value is bounded per URL by
    # Config.refresh_interval_for (out-of-range values are normalized, not
    # rejected). Only a non-integer (or bool) is a config error.
    interval = item.get("refresh_interval_minutes")
    if interval is not None and (
        not isinstance(interval, int) or isinstance(interval, bool)
    ):
        raise ConfigError(
            f"{where}: 'refresh_interval_minutes' must be an integer"
        )

    return UrlEntry(
        url=url,
        match=match,
        no_match=no_match,
        verify_tls=verify_tls,
        timeout_seconds=None if timeout is None else float(timeout),
        refresh_interval_minutes=interval,
        measure_network_hops=measure_hops,
    )


# In-place ``[[urls]]`` editing (used by the action commands) is the SDK's
# generic array-of-tables editor, keyed by the ``url`` identity field and
# guarded by servermon's own schema via :func:`_validator`. The SDK preserves
# comments with the vendored tomlkit (else a regex fallback) and refuses to
# write a file that would not load back.


def _validator(path: Path):
    """Reject an edit that would not load back as a valid servermon config.

    Runs servermon's :func:`parse_config` over the SDK-reparsed result before
    the atomic write, so a bad value or a duplicate/malformed device is
    rejected and the file is left unchanged.
    """
    return lambda parsed: parse_config(parsed, source=str(path))


def set_url_option(path: Path | str, url: str, key: str, value: object) -> None:
    """Set ``key = value`` on the ``[[urls]]`` entry for ``url`` (in-place edit,
    comments preserved).

    Used by the "set <field> <value>" action commands (including "set refresh
    interval", via :func:`set_url_refresh_interval`). ``value`` is a Python
    str/int/float/bool. Raises ConfigError if the entry cannot be found or the
    edit would not parse (e.g. a bad regex or a non-positive number), leaving
    the file unchanged.
    """
    path = Path(path)
    set_aot_option(path, "urls", "url", url, key, value, validate=_validator(path))


def set_url_refresh_interval(path: Path | str, url: str, minutes: int) -> None:
    """Set ``refresh_interval_minutes`` for one ``[[urls]]`` entry (see
    :func:`set_url_option`).

    Used by the "set refresh interval" action.
    """
    set_url_option(path, url, "refresh_interval_minutes", minutes)


def clear_url_option(path: Path | str, url: str, key: str) -> None:
    """Remove ``key`` from the ``[[urls]]`` entry for ``url``, reverting it to
    its default (in-place edit, comments preserved).

    Used by "set <field>" with no value. A no-op if the key is already absent;
    raises ConfigError if the entry cannot be found or the result would not
    parse.
    """
    path = Path(path)
    clear_aot_option(path, "urls", "url", url, key, validate=_validator(path))


def remove_url_entry(path: Path | str, url: str) -> None:
    """Remove one ``[[urls]]`` entry from the TOML file (in-place edit).

    Used by the "delete device" action command. If the last entry is removed,
    ``urls = []`` is left behind so the file still loads.
    """
    path = Path(path)
    remove_aot_entry(path, "urls", "url", url, validate=_validator(path))


def add_url_entry(path: Path | str, url: str) -> None:
    """Append a new ``[[urls]]`` entry for ``url`` to the TOML file (in-place
    edit).

    Used by the "push link" action. The new text is re-parsed before it is
    committed, so a duplicate device id or a URL that is not http(s) is rejected
    (``ConfigError``) and the file is left unchanged.
    """
    path = Path(path)
    add_aot_entry(path, "urls", {"url": url}, validate=_validator(path))


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
