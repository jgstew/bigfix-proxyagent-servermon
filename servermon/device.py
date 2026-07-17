"""Derive the BigFix device identity for a monitored URL and build device reports."""

from __future__ import annotations

import hashlib
import ipaddress
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from . import __version__

if TYPE_CHECKING:
    from .checker import CheckResult
    from .config import UrlEntry
    from .state import LastError

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


def build_report(
    entry: UrlEntry,
    result: CheckResult,
    sequence: int | None = None,
    last_error: LastError | None = None,
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
        "servermon version": __version__,
        "url": entry.url,
        "http response code": result.status_code,
        "http check result": result.detail,
        "check success": result.success,
        "response time ms": result.response_time_ms,
        "last check time": result.checked_at,
    }
    # TLS protocol version of the connection, and the remote IP actually
    # connected to. The IP also feeds the reserved "IP Address" console
    # property via the built-in network inspectors.
    if result.tls_version is not None:
        report["tls version"] = result.tls_version
    if result.peer_ip is not None:
        report["remote ip address"] = result.peer_ip
        report["network"] = _network_structure(result.peer_ip)
    # Only present when a match string is configured, so relevance can use
    # "exists match found of ..." to distinguish unconfigured from failed.
    if entry.match is not None:
        report["match found"] = bool(result.match_found)
    # The most recent error this device has ever had (tracked in the plugin's
    # state file, see state.py). Device reports fully replace prior data in
    # BigFix, so this must be re-sent every report to stay visible after a
    # transient error clears. Absent only if the device has never failed.
    if last_error is not None:
        report["http check last error"] = last_error.detail
        report["http check last error time"] = last_error.time
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
