import logging
import socket
import ssl
import types
from datetime import datetime
from unittest.mock import MagicMock

import pytest

import servermon.checker
from servermon.checker import (TIME_FORMAT, _build_ssl_context, _cert_expiry,
                               _connection_info, _load_ca_bundle, _probe_ttl,
                               _response_texts, _ssl_context, check_url)
from servermon.config import UrlEntry


def check(url, **entry_kwargs):
    entry = UrlEntry(url=url, **entry_kwargs)
    return check_url(entry, timeout=5, user_agent="servermon-tests")


def test_success(http_server):
    result = check(f"{http_server}/ok")
    assert result.status_code == 200
    assert result.success is True
    assert result.detail.startswith("OK: HTTP 200")
    assert result.match_found is None
    assert result.response_time_ms >= 0
    assert result.server == "servermon-test/1.0"
    assert result.peer_ip == "127.0.0.1"
    assert result.tls_version is None  # plain http test server
    assert result.cert_expires is None  # no TLS -> no cert expiry


class TestCertExpiry:
    def test_parses_notafter_to_mime_time(self):
        # getpeercert() format, GMT, space-padded day.
        expiry = _cert_expiry({"notAfter": "Jun  4 11:04:38 2035 GMT"})
        parsed = datetime.strptime(expiry, TIME_FORMAT)
        assert (parsed.year, parsed.month, parsed.day) == (2035, 6, 4)
        assert parsed.utcoffset().total_seconds() == 0  # normalized to UTC

    def test_none_when_no_cert(self):
        assert _cert_expiry(None) is None  # peer sent no certificate

    def test_none_when_unverified(self):
        assert _cert_expiry({}) is None  # verify_tls off -> empty dict

    def test_none_when_no_notafter_key(self):
        assert _cert_expiry({"subject": ()}) is None

    def test_none_on_unparseable_value(self):
        assert _cert_expiry({"notAfter": "not a date"}) is None


def test_checked_at_is_bigfix_mime_time(http_server):
    result = check(f"{http_server}/ok")
    parsed = datetime.strptime(result.checked_at, TIME_FORMAT)
    assert abs((datetime.now().astimezone() - parsed).total_seconds()) < 60


def test_http_404(http_server):
    result = check(f"{http_server}/does-not-exist")
    assert result.status_code == 404
    assert result.success is False
    assert result.detail.startswith("FAILED: HTTP 404")


def test_http_500(http_server):
    result = check(f"{http_server}/error")
    assert result.status_code == 500
    assert result.success is False


def test_redirect_followed(http_server):
    result = check(f"{http_server}/redirect")
    assert result.status_code == 200
    assert result.success is True


def test_match_in_body(http_server):
    result = check(f"{http_server}/ok", match="hello from the servermon")
    assert result.success is True
    assert result.match_found is True
    assert "in body" in result.detail


def test_match_is_case_insensitive(http_server):
    result = check(f"{http_server}/ok", match="HELLO FROM THE SERVERMON")
    assert result.success is True
    assert result.match_found is True


def test_match_is_regex(http_server):
    result = check(f"{http_server}/ok", match=r"hello\s+from\s+the\s+\w+ test")
    assert result.success is True
    assert result.match_found is True


def test_match_in_headers(http_server):
    result = check(f"{http_server}/ok", match="header-needle")
    assert result.success is True
    assert result.match_found is True
    assert "in headers" in result.detail


def test_match_missing_fails_check(http_server):
    result = check(f"{http_server}/ok", match="not on this page")
    assert result.status_code == 200
    assert result.success is False
    assert result.match_found is False
    assert "not found" in result.detail


def test_match_searched_in_error_response(http_server):
    # A match can still be found on an error page, but the check stays failed
    # because of the status code.
    result = check(f"{http_server}/error", match="internal problem")
    assert result.status_code == 500
    assert result.match_found is True
    assert result.success is False


def test_no_match_hit_fails_check(http_server):
    # /ok body: "hello from the servermon test server", HTTP 200.
    result = check(f"{http_server}/ok", no_match="SERVERMON TEST")
    assert result.status_code == 200
    assert result.success is False
    assert result.bad_string_found is True
    assert "no_match pattern" in result.detail
    assert result.detail.startswith("FAILED:")


def test_no_match_is_regex(http_server):
    result = check(f"{http_server}/ok", no_match=r"hello\s+from\s+the")
    assert result.success is False
    assert result.bad_string_found is True


def test_no_match_absent_passes(http_server):
    result = check(f"{http_server}/ok", no_match="could not connect to the database")
    assert result.success is True
    assert result.bad_string_found is False


def test_no_match_searches_headers(http_server):
    result = check(f"{http_server}/ok", no_match="HEADER-NEEDLE")
    assert result.success is False
    assert "in headers" in result.detail


def test_no_match_not_configured(http_server):
    result = check(f"{http_server}/ok")
    assert result.bad_string_found is None


def test_match_and_no_match_combined(http_server):
    # match satisfied but no_match trips -> still a failure.
    result = check(
        f"{http_server}/ok", match="hello from", no_match="test server"
    )
    assert result.match_found is True
    assert result.bad_string_found is True
    assert result.success is False


def test_connection_refused(closed_port_url):
    result = check(closed_port_url)
    assert result.status_code == 0
    assert result.success is False
    assert result.detail.startswith("ERROR:")
    assert result.server == ""


def test_connection_refused_with_match(closed_port_url):
    result = check(closed_port_url, match="anything")
    assert result.match_found is False


def test_unresolvable_host():
    result = check("http://servermon-invalid.invalid/")
    assert result.status_code == 0
    assert result.success is False
    assert result.detail.startswith("ERROR:")


def test_malformed_url_never_raises():
    # "http://[::1" passes config validation (scheme prefix only) but makes
    # urllib's Request constructor raise ValueError; check_url promises to
    # never raise, so this must degrade to a status-0 error result.
    result = check("http://[::1")
    assert result.status_code == 0
    assert result.success is False
    assert result.detail.startswith("ERROR: unexpected ValueError")


def test_control_characters_in_url_are_an_error():
    result = check("http://exa mple.com/")
    assert result.status_code == 0
    assert result.success is False
    assert result.detail.startswith("ERROR:")


def test_timeout_is_an_error_not_a_hang(http_server):
    # /slow stalls for 2s; with a 0.25s timeout the check must come back
    # quickly as a status-0 error.
    entry = UrlEntry(url=f"{http_server}/slow")
    result = check_url(entry, timeout=0.25, user_agent="servermon-tests")
    assert result.status_code == 0
    assert result.success is False
    assert result.detail.startswith("ERROR: no HTTP response")
    assert result.response_time_ms < 2000  # gave up, did not wait out /slow


def test_https_against_plain_http_server_is_error(http_server):
    # TLS handshake against a plain-HTTP port fails even with verification
    # off; must degrade to a status-0 error, never raise.
    url = http_server.replace("http://", "https://") + "/ok"
    result = check(url, verify_tls=False)
    assert result.status_code == 0
    assert result.success is False
    assert result.detail.startswith("ERROR:")


def test_match_beyond_body_cap_not_found(http_server):
    # The needle sits after MAX_BODY_BYTES; the cap must hide it so a huge
    # response cannot stall the refresh (documents the 1 MiB scan limit).
    result = check(f"{http_server}/big", match="needle-beyond-cap")
    assert result.status_code == 200
    assert result.match_found is False
    assert result.success is False


def test_match_within_body_cap_found(http_server):
    # Positive control for the cap test: filler inside the cap is found.
    result = check(f"{http_server}/big", match="xxxx")
    assert result.match_found is True


def test_unknown_charset_falls_back_to_utf8(http_server):
    # The server declares charset=klingon-piqad; decode must fall back to
    # UTF-8 (with replacement) instead of raising, and still find the match.
    result = check(f"{http_server}/latin", match="body-needle")
    assert result.success is True
    assert result.match_found is True


def test_no_match_undetermined_on_connection_failure(closed_port_url):
    # With no response there is nothing to scan: bad_string_found stays None
    # (undetermined) rather than claiming the bad string was absent.
    result = check(closed_port_url, no_match="anything")
    assert result.status_code == 0
    assert result.bad_string_found is None


@pytest.fixture
def fresh_ssl_context_cache():
    _build_ssl_context.cache_clear()
    yield
    _build_ssl_context.cache_clear()


def _some_system_ca_pem() -> str:
    ders = ssl.create_default_context().get_ca_certs(binary_form=True)
    if not ders:
        pytest.skip("no system CA certificates available")
    return ssl.DER_cert_to_PEM_cert(ders[0])


def test_ssl_context_verify_disabled(fresh_ssl_context_cache):
    context = _ssl_context(False)
    assert context.verify_mode == ssl.CERT_NONE
    assert context.check_hostname is False


def test_ssl_context_verify_enabled(fresh_ssl_context_cache):
    context = _ssl_context(True)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_load_ca_bundle_adds_certs(tmp_path):
    bundle = tmp_path / "bundle.pem"
    bundle.write_text(_some_system_ca_pem(), encoding="utf-8")

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    assert len(context.get_ca_certs()) == 0
    _load_ca_bundle(context, str(bundle), "test bundle")
    assert len(context.get_ca_certs()) == 1


def test_load_ca_bundle_invalid_is_not_fatal(tmp_path, caplog):
    bundle = tmp_path / "junk.pem"
    bundle.write_text("this is not a PEM file", encoding="utf-8")

    context = ssl.create_default_context()
    with caplog.at_level(logging.WARNING, logger="servermon.checker"):
        _load_ca_bundle(context, str(bundle), "junk bundle")  # must not raise
    assert any("could not load junk bundle" in r.message for r in caplog.records)


def test_plugin_ca_bundle_loaded_when_present(
    fresh_ssl_context_cache, tmp_path, monkeypatch, caplog
):
    bundle = tmp_path / "ca-bundle.pem"
    bundle.write_text(_some_system_ca_pem(), encoding="utf-8")
    monkeypatch.setattr(servermon.checker, "PLUGIN_CA_BUNDLE", bundle)

    with caplog.at_level(logging.INFO, logger="servermon.checker"):
        _ssl_context(True)
    assert any("plugin ca-bundle.pem" in r.message for r in caplog.records)


class TestConnectTime:
    def test_connect_time_measured_on_success(self, http_server):
        result = check(f"{http_server}/ok")
        assert isinstance(result.connect_time_ms, int)
        assert 0 <= result.connect_time_ms <= result.response_time_ms

    def test_connect_time_measured_on_http_error(self, http_server):
        result = check(f"{http_server}/error")
        assert isinstance(result.connect_time_ms, int)

    def test_connect_time_none_when_unresolvable(self):
        result = check("http://nonexistent.invalid")
        assert result.status_code == 0
        assert result.connect_time_ms is None


class TestConnectionInfo:
    def test_tls_socket_reports_version_and_cert(self):
        # A Mock with spec=ssl.SSLSocket passes the isinstance check, letting
        # the TLS branch be exercised without a real TLS server.
        sock = MagicMock(spec=ssl.SSLSocket)
        sock.getpeername.return_value = ("93.184.216.34", 443)
        sock.version.return_value = "TLSv1.3"
        sock.getpeercert.return_value = {"notAfter": "Jun  4 11:04:38 2035 GMT"}
        raw = types.SimpleNamespace(_sock=sock)
        response = types.SimpleNamespace(fp=types.SimpleNamespace(raw=raw))

        peer, tls, expiry = _connection_info(response)
        assert peer == "93.184.216.34"
        assert tls == "TLSv1.3"
        assert expiry is not None

    def test_surprise_shapes_degrade_to_none(self):
        # _connection_info reaches through http.client internals; anything
        # unexpected must degrade to (None, None, None), never raise.
        assert _connection_info(object()) == (None, None, None)


def test_response_texts_without_headers():
    header_text, body_text = _response_texts(None, b"plain body")
    assert header_text == ""
    assert body_text == "plain body"


class TestMeasureNetworkHops:
    def test_loopback_is_one_hop(self, http_server):
        hops = servermon.checker.measure_network_hops(
            f"{http_server}/ok", timeout=5
        )
        assert hops == 1

    def test_connection_refused_still_measures(self):
        # An RST from a closed port proves the packet reached the host, so
        # loopback measures 1 hop even with nothing listening.
        hops = servermon.checker.measure_network_hops(
            "http://127.0.0.1:1", timeout=5
        )
        assert hops == 1

    def test_unresolvable_returns_none(self):
        hops = servermon.checker.measure_network_hops(
            "http://nonexistent.invalid", timeout=1
        )
        assert hops is None

    def test_never_raises_on_garbage_url(self):
        assert servermon.checker.measure_network_hops("http://", timeout=1) is None

    def test_never_raises_on_unsplittable_url(self):
        # urlsplit itself raises ValueError for an unclosed IPv6 bracket.
        assert (
            servermon.checker.measure_network_hops("http://[::1", timeout=1) is None
        )

    def test_never_raises_on_invalid_port(self):
        # urlsplit parses the port lazily; a non-numeric port raises there.
        assert (
            servermon.checker.measure_network_hops("http://host:banana", timeout=1)
            is None
        )

    def test_empty_getaddrinfo_returns_none(self, monkeypatch):
        # getaddrinfo normally raises for unknown hosts, but an empty result
        # list is possible and must not crash the binary search.
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [])
        assert servermon.checker.measure_network_hops(
            "http://example.com", timeout=1
        ) is None

    def test_unreachable_at_max_ttl_returns_none(self, monkeypatch):
        # If even the unlimited-TTL probe fails, there is nothing to measure.
        monkeypatch.setattr(
            servermon.checker, "_probe_ttl", lambda *args: False
        )
        assert servermon.checker.measure_network_hops(
            "http://127.0.0.1", timeout=1
        ) is None

    def test_binary_search_converges_on_first_working_ttl(self, monkeypatch):
        # Probes fail below TTL 5 and succeed from 5 up; the search must
        # land exactly on 5, exercising both halves of the bisection.
        monkeypatch.setattr(
            servermon.checker,
            "_probe_ttl",
            lambda family, sockaddr, ttl, timeout: ttl >= 5,
        )
        assert servermon.checker.measure_network_hops(
            "http://127.0.0.1", timeout=1
        ) == 5


class TestProbeTtl:
    def test_ipv6_refused_counts_as_reached(self):
        # An RST from a closed IPv6 loopback port proves the packet arrived;
        # exercises the IPV6_UNICAST_HOPS socket option branch.
        with socket.socket(socket.AF_INET6) as sock:
            try:
                sock.bind(("::1", 0))
            except OSError:
                pytest.skip("IPv6 loopback unavailable")
            port = sock.getsockname()[1]
        assert _probe_ttl(
            socket.AF_INET6, ("::1", port, 0, 0), ttl=64, timeout=5
        ) is True

    def test_connect_timeout_is_false(self, monkeypatch):
        # A connect that times out (or otherwise errors without an RST) means
        # the packet did not reach the host, so the probe reports False. Mock
        # the socket rather than rely on a real "unreachable" address: some
        # networks (e.g. a captive portal or corporate proxy) answer the SYN
        # for any address, including RFC 5737 TEST-NET-1.
        class _FakeSocket:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def setsockopt(self, *args):
                pass

            def settimeout(self, *args):
                pass

            def connect(self, *args):
                raise TimeoutError("timed out")  # a subclass of OSError

        monkeypatch.setattr(servermon.checker.socket, "socket", _FakeSocket)
        assert _probe_ttl(
            socket.AF_INET, ("192.0.2.1", 80), ttl=64, timeout=0.2
        ) is False

    def test_unreachable_address_is_false_real_socket(self):
        # Real-socket companion to test_connect_timeout_is_false, as a canary
        # for the actual connect path. 192.0.2.1 (TEST-NET-1, RFC 5737) is
        # unassigned; a high, non-standard port dodges transparent HTTP/HTTPS
        # proxies that answer 80/443 for any address. If the connect still
        # reports reachable, the network is intercepting arbitrary ports too -
        # skip (don't fail), since real unreachability can't be observed here.
        result = _probe_ttl(
            socket.AF_INET, ("192.0.2.1", 48819), ttl=64, timeout=0.2
        )
        if result is not False:
            pytest.skip("network intercepts connects to unrouted addresses")
        assert result is False

    def test_socket_creation_failure_is_false(self):
        # An invalid address family makes socket() itself raise OSError.
        assert _probe_ttl(9999, ("127.0.0.1", 80), ttl=64, timeout=0.2) is False


def test_certifi_bundle_loaded_when_installed(
    fresh_ssl_context_cache, tmp_path, monkeypatch, caplog
):
    # certifi is an optional extra; fake it to prove its bundle gets loaded.
    import sys

    bundle = tmp_path / "certifi.pem"
    bundle.write_text(_some_system_ca_pem(), encoding="utf-8")
    monkeypatch.setitem(
        sys.modules,
        "certifi",
        types.SimpleNamespace(where=lambda: str(bundle)),
    )
    with caplog.at_level(logging.INFO, logger="servermon.checker"):
        _ssl_context(True)
    assert any("certifi bundle" in r.message for r in caplog.records)
