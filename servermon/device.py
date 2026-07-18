"""Derive the BigFix device identity for a monitored URL and build device reports."""

from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from . import __version__

if TYPE_CHECKING:
    from .checker import CheckResult
    from .config import UrlEntry
    from .state import DeviceRecord

DATA_SOURCE = "servermon"

# The computer hosting this proxy agent plugin: generally the Windows BigFix
# relay running the Proxy Agent. Resolved once at import (a local syscall, no
# network resolution); it never changes for the life of the process. Falls back
# to "Unknown" if the hostname cannot be determined.
try:
    PLUGIN_HOST = socket.gethostname()
except OSError:
    PLUGIN_HOST = "Unknown"

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


def build_report(
    entry: UrlEntry,
    result: CheckResult,
    sequence: int | None = None,
    device_state: DeviceRecord | None = None,
    default_interval: int | None = None,
) -> dict[str, Any]:
    """Build the device report written to ``<device id>.report``.

    "device id", "data source", and "computer name" are the keys the Proxy
    Agent requires; the rest become relevance-inspectable device properties
    declared in Inspectors/servermon.inspectors.
    """
    report: dict[str, Any] = {
        "device id": device_id(entry.url),
        "data source": DATA_SOURCE,
        "computer name": device_name(entry.url),
        # Reserved-property inspectors from the proxy agent's built-in
        # inspector list, filled with the closest URL-device equivalents:
        # the "OS" is the web server software (Server header), its version
        # is the TLS protocol version (or the plugin version for plain
        # http), and the DNS name is the URL's hostname.
        "device type": "Web Server",
        "dns name": urlsplit(entry.url).hostname or device_name(entry.url),
        "operating system": {
            "name": result.server or DATA_SOURCE,
            "version": (
                result.tls_version.removeprefix("TLSv")  # "TLSv1.3" -> "1.3"
                if result.tls_version
                else __version__
            ),
        },
        "in proxy agent context": True,
        # The standard "proxy agent plugin" inspector object (see the built-in
        # main.inspectors list). "name" is this plugin's name and "host" is the
        # computer running the Proxy Agent (the BigFix relay); "last report
        # time" is when this report was produced.
        "proxy agent plugin": {
            "name": DATA_SOURCE,
            "version": __version__,
            "host": PLUGIN_HOST,
            "last report time": result.checked_at,
        },
        "servermon version": __version__,
        "url": entry.url,
        "http response code": result.status_code,
        "http check result": result.detail,
        "check success": result.success,
        "response time ms": result.response_time_ms,
        "last check time": result.checked_at,
        # The Proxy Agent only treats a report as new if the "effective
        # device communication" time advances, and it feeds the console's
        # Last Report Time; reporting the check time guarantees both track
        # the checks regardless of file modification times.
        "last server communication": result.checked_at,
    }
    # TLS protocol version of the connection, and the remote IP actually
    # connected to. The IP also feeds the reserved "IP Address" console
    # property via the built-in network inspectors.
    if result.tls_version is not None:
        report["tls version"] = result.tls_version
    if result.cert_expires is not None:
        report["ssl certificate expires"] = result.cert_expires
    if result.peer_ip is not None:
        report["remote ip address"] = result.peer_ip
        report["network"] = _network_structure(result.peer_ip)
    # The effective check cadence in minutes: this URL's configured
    # check_interval_minutes, else the plugin-wide heartbeat
    # (DeviceReportRefreshIntervalMinutes from settings.json).
    interval = entry.check_interval_minutes
    if interval is None:
        interval = default_interval
    if interval is not None:
        report["refresh interval"] = interval
    # Only present when a match string is configured, so relevance can use
    # "exists match found of ..." to distinguish unconfigured from failed.
    if entry.match is not None:
        report["match found"] = bool(result.match_found)
    # Likewise only present when a no_match pattern is configured; true means
    # the server was reachable but served the known-bad content.
    if entry.no_match is not None:
        report["bad string found"] = bool(result.bad_string_found)
    # Per-device history tracked in the plugin's state file (see state.py).
    # Device reports fully replace prior data in BigFix, so these must be
    # re-sent with every report to stay visible.
    if device_state is not None:
        # The most recent error this device has ever had; absent only if
        # the device has never failed.
        if device_state.last_error is not None:
            report["http check last error"] = device_state.last_error.detail
            report["http check last error time"] = device_state.last_error.time
        # Last time the URL actually answered with an HTTP response. When
        # present, the Proxy Agent uses it to generate the console's Last
        # Report Time, so a URL that stops responding shows a visibly stale
        # Last Report Time while "last server communication" (the check
        # time) keeps the reports themselves fresh.
        if device_state.last_contact is not None:
            report["last device report time"] = device_state.last_contact
    # Echo the report sequence number from the refresh command back to the
    # Proxy Agent. The expected key spelling is not publicly documented, so
    # both styles are included; the extra key is harmless either way.
    if sequence is not None:
        report["device report sequence"] = sequence
        report["deviceReportSequence"] = sequence
    return report


def _network_structure(peer_ip: str) -> dict[str, Any]:
    """Model the remote server's IP as the device's built-in network
    inspectors ("ip interfaces of network", "adapters of network").
    """
    try:
        parsed = ipaddress.ip_address(peer_ip)
        loopback = parsed.is_loopback
        is_ipv6 = parsed.version == 6
    except ValueError:
        loopback = False
        is_ipv6 = ":" in peer_ip

    network: dict[str, Any] = {
        "ip interfaces": [{"address": peer_ip, "loopback": loopback}],
    }
    if is_ipv6:
        # The reserved "IPv6 Address" property reads addresses via adapters.
        network["adapters"] = [
            {
                "up": True,
                "loopback": loopback,
                "ipv6 interfaces": [{"address": peer_ip}],
            }
        ]
    return network
