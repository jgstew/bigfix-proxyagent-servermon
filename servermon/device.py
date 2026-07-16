"""Derive the BigFix device identity for a monitored URL and build device reports."""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING, Any

from . import __version__

if TYPE_CHECKING:
    from .checker import CheckResult
    from .config import UrlEntry

DATA_SOURCE = "servermon"

_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)


def device_name(url: str) -> str:
    """Device name shown in the BigFix console: the URL without its scheme."""
    return _SCHEME_RE.sub("", url.strip()).rstrip("/")


def device_id(url: str) -> str:
    """Stable device id for a monitored URL, used as the report file name.

    Keyed on the scheme-less device name so that switching a URL between
    http:// and https:// keeps the same device identity (and history) in
    BigFix.
    """
    return hashlib.sha256(device_name(url).encode("utf-8")).hexdigest()


def build_report(entry: UrlEntry, result: CheckResult) -> dict[str, Any]:
    """Build the device report written to ``<device id>.report``.

    "device id", "data source", and "computer name" are the keys the Proxy
    Agent requires; the rest become relevance-inspectable device properties
    declared in Inspectors/servermon.inspectors.
    """
    report: dict[str, Any] = {
        "device id": device_id(entry.url),
        "data source": DATA_SOURCE,
        "computer name": device_name(entry.url),
        # Surfaces the web server software (e.g. "nginx/1.25.3") as the
        # device OS in the console; falls back to the plugin name when the
        # server did not identify itself or did not respond.
        "operating system": result.server or DATA_SOURCE,
        "in proxy agent context": True,
        "servermon version": __version__,
        "url": entry.url,
        "http response code": result.status_code,
        "http check result": result.detail,
        "check success": result.success,
        "response time ms": result.response_time_ms,
        "last check time": result.checked_at,
    }
    # Only present when a match string is configured, so relevance can use
    # "exists match found of ..." to distinguish unconfigured from failed.
    if entry.match is not None:
        report["match found"] = bool(result.match_found)
    return report
