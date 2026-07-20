"""Perform a single HTTP(S) check against a monitored URL."""

from __future__ import annotations

import email.utils
import functools
import http.client
import logging
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from .config import UrlEntry

log = logging.getLogger(__name__)

# Optional PEM bundle shipped alongside the plugin (repo root); loaded into
# the trust store when present, e.g. for internal CAs or machines whose OS
# store is missing public roots.
PLUGIN_CA_BUNDLE = Path(__file__).resolve().parent.parent / "ca-bundle.pem"

# Read at most this much of the response body when scanning for the match
# string, so a misconfigured URL (e.g. pointing at a large download) cannot
# stall the whole refresh.
MAX_BODY_BYTES = 1024 * 1024

# BigFix MIME date format ("Wed, 16 Jul 2026 10:00:00 -0400"), so relevance
# can cast the value with "as time". checked_at is produced with
# email.utils.format_datetime to stay locale-independent.
TIME_FORMAT = "%a, %d %b %Y %H:%M:%S %z"


@dataclass(frozen=True)
class CheckResult:
    url: str
    status_code: int  # 0 when no HTTP response was received at all
    success: bool
    detail: str
    response_time_ms: int
    match_found: bool | None  # None when no match string is configured
    checked_at: str  # local time in TIME_FORMAT
    server: str  # Server response header, "" when absent or unreachable
    peer_ip: str | None = None  # IP of the remote server actually connected to
    tls_version: str | None = None  # e.g. "TLSv1.3"; None for plain http
    bad_string_found: bool | None = None  # None when no_match is not configured
    # Server certificate expiry (notAfter) in TIME_FORMAT; None for plain
    # http, verify_tls = false, or an unparsable/absent cert.
    cert_expires: str | None = None
    # TCP connect time (DNS resolution + handshake, excluding TLS) of the last
    # connection made (the final one when redirects were followed); None when
    # no TCP connection was established.
    connect_time_ms: int | None = None


def check_url(entry: UrlEntry, *, timeout: float, user_agent: str) -> CheckResult:
    """Fetch the URL once (following redirects) and summarize the outcome.

    Never raises: network and TLS failures become a status code of 0 with an
    "ERROR: ..." detail, so one bad URL cannot abort a whole refresh.
    """
    checked_at = email.utils.format_datetime(datetime.now().astimezone())
    context = _ssl_context(entry.verify_tls)
    # Filled in by the timing connection factory when a TCP connect completes;
    # survives into the error paths (e.g. TLS failure after a good connect).
    timing: dict[str, int] = {}
    opener = urllib.request.build_opener(
        _TimingHTTPHandler(timing), _TimingHTTPSHandler(context, timing)
    )

    started = time.monotonic()
    try:
        # Inside the try: a malformed URL (e.g. an unclosed IPv6 bracket)
        # makes the Request constructor itself raise ValueError.
        request = urllib.request.Request(
            entry.url, headers={"User-Agent": user_agent, "Accept": "*/*"}
        )
        with opener.open(request, timeout=timeout) as response:
            peer_ip, tls_version, cert_expires = _connection_info(response)
            body = response.read(MAX_BODY_BYTES)
            elapsed_ms = _elapsed_ms(started)
            return _from_response(
                entry,
                response.status,
                response.headers,
                body,
                elapsed_ms,
                checked_at,
                peer_ip=peer_ip,
                tls_version=tls_version,
                cert_expires=cert_expires,
                connect_time_ms=timing.get("connect_ms"),
            )
    except urllib.error.HTTPError as error:
        # 4xx/5xx raise, but they are still HTTP responses worth reporting.
        peer_ip, tls_version, cert_expires = _connection_info(error)
        try:
            body = error.read(MAX_BODY_BYTES)
        except OSError:
            body = b""
        elapsed_ms = _elapsed_ms(started)
        return _from_response(
            entry,
            error.code,
            error.headers,
            body,
            elapsed_ms,
            checked_at,
            peer_ip=peer_ip,
            tls_version=tls_version,
            cert_expires=cert_expires,
            connect_time_ms=timing.get("connect_ms"),
        )
    except (
        urllib.error.URLError,
        ssl.SSLError,
        socket.timeout,
        ConnectionError,
        http.client.HTTPException,
    ) as error:
        reason = getattr(error, "reason", None) or error
        detail = f"ERROR: no HTTP response: {reason}"
    except Exception as error:  # keep a single bad URL from killing the refresh
        detail = f"ERROR: unexpected {type(error).__name__}: {error}"

    return CheckResult(
        url=entry.url,
        status_code=0,
        success=False,
        detail=detail,
        response_time_ms=_elapsed_ms(started),
        match_found=None if entry.match is None else False,
        checked_at=checked_at,
        server="",
        connect_time_ms=timing.get("connect_ms"),
    )


def _timed_connection_factory(cls: type, timing: dict[str, int]):
    """A connection factory for urllib's do_open() that times the TCP
    connect (http.client sets ``_create_connection`` per instance, so it can.

    be wrapped without touching the TLS wrap that HTTPS does afterwards).
    """

    def factory(host: str, **kwargs: Any) -> http.client.HTTPConnection:
        conn = cls(host, **kwargs)
        create = conn._create_connection

        def timed_create(address, *args, **kw):
            started = time.monotonic()
            sock = create(address, *args, **kw)
            timing["connect_ms"] = _elapsed_ms(started)
            return sock

        conn._create_connection = timed_create
        return conn

    return factory


class _TimingHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, timing: dict[str, int]) -> None:
        super().__init__()
        self._timing = timing

    def http_open(self, req):
        return self.do_open(
            _timed_connection_factory(http.client.HTTPConnection, self._timing), req
        )


class _TimingHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, context: ssl.SSLContext, timing: dict[str, int]) -> None:
        super().__init__(context=context)
        self._timing = timing

    def https_open(self, req):
        return self.do_open(
            _timed_connection_factory(http.client.HTTPSConnection, self._timing),
            req,
            context=self._context,
        )


# TTL search space for measure_network_hops. 64 is comfortably above real
# internet path lengths (typically < 30 hops).
MAX_TTL_HOPS = 64

# Cap on the per-probe connect timeout. A too-low TTL usually surfaces as a
# timeout on Windows (Linux gets a fast EHOSTUNREACH from the ICMP
# time-exceeded), so the binary search's failed probes each cost up to this
# long; the URL's full timeout would make the measurement take minutes.
HOP_PROBE_TIMEOUT_SECONDS = 5.0


def measure_network_hops(url: str, *, timeout: float) -> int | None:
    """Estimate the network hop count to the URL's host.

    Binary-searches the smallest IP TTL at which a plain TCP connect to the
    URL's host/port completes (the TLS/HTTP layers are never involved). About
    7 short connects per measurement. Never raises; returns None when the
    host is unresolvable/unreachable or the measurement is otherwise
    impossible. Anycast/CDN targets measure the path to the nearest edge, and
    routes change, so treat the value as an estimate.
    """
    try:
        parts = urlsplit(url)
        host = parts.hostname
        # .port is parsed lazily and raises for a non-numeric/out-of-range
        # port, just like urlsplit does for an unclosed IPv6 bracket.
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return None
    if not host:
        return None
    probe_timeout = min(timeout, HOP_PROBE_TIMEOUT_SECONDS)

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return None
    if not infos:
        return None
    # Probe one resolved address consistently for the whole search.
    family, _, _, _, sockaddr = infos[0]

    if not _probe_ttl(family, sockaddr, MAX_TTL_HOPS, probe_timeout):
        return None  # unreachable even without a TTL limit; nothing to measure
    low, high = 1, MAX_TTL_HOPS
    while low < high:
        mid = (low + high) // 2
        if _probe_ttl(family, sockaddr, mid, probe_timeout):
            high = mid
        else:
            low = mid + 1
    return low


def _probe_ttl(family: int, sockaddr: Any, ttl: int, timeout: float) -> bool:
    """Whether a TCP connect with the given IP TTL reaches the host."""
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            if family == socket.AF_INET6:
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_UNICAST_HOPS, ttl)
            else:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, ttl)
            sock.settimeout(timeout)
            try:
                sock.connect(sockaddr)
            except ConnectionRefusedError:
                return True  # an RST came back, so the packet reached the host
            except OSError:
                # Timeout, or the TTL expired in transit (Linux reports the
                # ICMP time-exceeded as EHOSTUNREACH; Windows just times out).
                return False
            return True
    except OSError:
        return False


_ssl_context_lock = threading.Lock()


def _ssl_context(verify: bool) -> ssl.SSLContext:
    """Shared SSL context for all checks, built once per process.

    The lock keeps parallel first callers from each building (and logging)
    their own context; lru_cache alone does not serialize the first call.
    """
    with _ssl_context_lock:
        return _build_ssl_context(verify)


@functools.lru_cache(maxsize=None)
def _build_ssl_context(verify: bool) -> ssl.SSLContext:
    """Build the SSL context used for all checks.

    The trust anchors are the combination of:
    1. the OS certificate store (on Windows, the ROOT and CA system stores)
       plus anything in the SSL_CERT_FILE / SSL_CERT_DIR env vars - this is
       what ssl.create_default_context() loads;
    2. the certifi bundle, when the certifi package is installed;
    3. an optional ``ca-bundle.pem`` file next to the plugin (repo root).

    A bundle that fails to load is logged and skipped rather than fatal.
    """
    context = ssl.create_default_context()
    if not verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    try:
        import certifi
    except ImportError:
        log.debug("certifi not installed; skipping")
    else:
        _load_ca_bundle(context, certifi.where(), "certifi bundle")

    if PLUGIN_CA_BUNDLE.is_file():
        _load_ca_bundle(context, str(PLUGIN_CA_BUNDLE), "plugin ca-bundle.pem")

    return context


def _load_ca_bundle(context: ssl.SSLContext, cafile: str, label: str) -> None:
    try:
        context.load_verify_locations(cafile=cafile)
        log.info("TLS trust: loaded %s (%s)", label, cafile)
    except (ssl.SSLError, OSError) as error:
        log.warning("TLS trust: could not load %s (%s): %s", label, cafile, error)


def _connection_info(
    response: Any,
) -> tuple[str | None, str | None, str | None]:
    """Best-effort (peer IP, TLS version, cert expiry) from the response's
    live socket.

    Reaches through http.client internals (there is no public API for this),
    so any surprise just degrades to (None, None, None).
    """
    try:
        fp = response.fp
        if hasattr(fp, "fp"):  # HTTPError wraps the HTTPResponse
            fp = fp.fp
        sock = fp.raw._sock
        peer = str(sock.getpeername()[0]).split("%")[0]  # drop IPv6 scope id
        if isinstance(sock, ssl.SSLSocket):
            return peer, sock.version(), _cert_expiry(sock.getpeercert())
        return peer, None, None
    except Exception:
        return None, None, None


def _cert_expiry(peercert: dict | None) -> str | None:
    """Server certificate expiry (notAfter) as a TIME_FORMAT string.

    getpeercert() returns the parsed dict only for a *validated* cert; it is
    ``{}`` when verify_tls is off and ``None`` when the peer sent no cert, so
    unverified and plain-http checks report no expiry.
    """
    if not peercert:
        return None
    not_after = peercert.get("notAfter")
    if not not_after:
        return None
    try:
        seconds = ssl.cert_time_to_seconds(not_after)
        return email.utils.format_datetime(
            datetime.fromtimestamp(seconds, tz=timezone.utc)
        )
    except (ValueError, OverflowError, OSError):
        return None


def _from_response(
    entry: UrlEntry,
    status: int,
    headers: Message | None,
    body: bytes,
    elapsed_ms: int,
    checked_at: str,
    peer_ip: str | None = None,
    tls_version: str | None = None,
    cert_expires: str | None = None,
    connect_time_ms: int | None = None,
) -> CheckResult:
    header_text, body_text = _response_texts(headers, body)

    match_found: bool | None = None
    match_note = ""
    if entry.match is not None:
        location = _find_pattern(entry.match, header_text, body_text)
        match_found = location is not None
        if match_found:
            match_note = f"; matched {entry.match!r} in {location}"
        else:
            match_note = f"; {entry.match!r} not found in headers or body"

    # A no_match hit means the server is reachable but serving a known-bad
    # page (e.g. "Could not connect to the database").
    bad_string_found: bool | None = None
    if entry.no_match is not None:
        location = _find_pattern(entry.no_match, header_text, body_text)
        bad_string_found = location is not None
        if bad_string_found:
            match_note += f"; no_match pattern {entry.no_match!r} found in {location}"

    status_ok = 200 <= status < 400
    success = status_ok and match_found is not False and bad_string_found is not True

    reason = http.client.responses.get(status, "")
    status_text = f"HTTP {status} {reason}".rstrip()
    prefix = "OK" if success else "FAILED"
    detail = f"{prefix}: {status_text} ({elapsed_ms} ms){match_note}"

    return CheckResult(
        url=entry.url,
        status_code=status,
        success=success,
        detail=detail,
        response_time_ms=elapsed_ms,
        match_found=match_found,
        checked_at=checked_at,
        server=str(headers.get("Server", "")).strip() if headers is not None else "",
        peer_ip=peer_ip,
        tls_version=tls_version,
        bad_string_found=bad_string_found,
        cert_expires=cert_expires,
        connect_time_ms=connect_time_ms,
    )


def _response_texts(headers: Message | None, body: bytes) -> tuple[str, str]:
    """Decode the response into searchable (header text, body text)."""
    if headers is not None:
        header_text = "\r\n".join(f"{key}: {value}" for key, value in headers.items())
    else:
        header_text = ""

    charset = (
        headers.get_content_charset() if headers is not None else None
    ) or "utf-8"
    try:
        body_text = body.decode(charset, errors="replace")
    except LookupError:  # server declared a charset Python does not know
        body_text = body.decode("utf-8", errors="replace")
    return header_text, body_text


def _find_pattern(pattern: str, header_text: str, body_text: str) -> str | None:
    """Where the case-insensitive regex matches ("headers"/"body"), or None."""
    compiled = re.compile(pattern, re.IGNORECASE)
    if compiled.search(header_text):
        return "headers"
    if compiled.search(body_text):
        return "body"
    return None


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)
