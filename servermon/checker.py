"""Perform a single HTTP(S) check against a monitored URL."""

from __future__ import annotations

import email.utils
import http.client
import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from email.message import Message
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import UrlEntry

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


def check_url(entry: UrlEntry, *, timeout: float, user_agent: str) -> CheckResult:
    """Fetch the URL once (following redirects) and summarize the outcome.

    Never raises: network and TLS failures become a status code of 0 with an
    "ERROR: ..." detail, so one bad URL cannot abort a whole refresh.
    """
    checked_at = email.utils.format_datetime(datetime.now().astimezone())
    request = urllib.request.Request(
        entry.url, headers={"User-Agent": user_agent, "Accept": "*/*"}
    )

    context = ssl.create_default_context()
    if not entry.verify_tls:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            body = response.read(MAX_BODY_BYTES)
            elapsed_ms = _elapsed_ms(started)
            return _from_response(
                entry, response.status, response.headers, body, elapsed_ms, checked_at
            )
    except urllib.error.HTTPError as error:
        # 4xx/5xx raise, but they are still HTTP responses worth reporting.
        try:
            body = error.read(MAX_BODY_BYTES)
        except OSError:
            body = b""
        elapsed_ms = _elapsed_ms(started)
        return _from_response(entry, error.code, error.headers, body, elapsed_ms, checked_at)
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
    )


def _from_response(
    entry: UrlEntry,
    status: int,
    headers: Message | None,
    body: bytes,
    elapsed_ms: int,
    checked_at: str,
) -> CheckResult:
    match_found: bool | None = None
    match_note = ""
    if entry.match is not None:
        location = _find_match(entry.match, headers, body)
        match_found = location is not None
        if match_found:
            match_note = f"; matched {entry.match!r} in {location}"
        else:
            match_note = f"; {entry.match!r} not found in headers or body"

    status_ok = 200 <= status < 400
    success = status_ok and match_found is not False

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
    )


def _find_match(match: str, headers: Message | None, body: bytes) -> str | None:
    """Return where the match string was found ("headers" or "body"), or None."""
    if headers is not None:
        header_text = "\r\n".join(f"{key}: {value}" for key, value in headers.items())
        if match in header_text:
            return "headers"

    charset = (headers.get_content_charset() if headers is not None else None) or "utf-8"
    try:
        text = body.decode(charset, errors="replace")
    except LookupError:  # server declared a charset Python does not know
        text = body.decode("utf-8", errors="replace")
    if match in text:
        return "body"
    return None


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)
