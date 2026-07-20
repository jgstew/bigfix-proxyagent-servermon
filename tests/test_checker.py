import logging
import ssl
from datetime import datetime

import pytest

import servermon.checker
from servermon.checker import (TIME_FORMAT, _build_ssl_context, _cert_expiry,
                               _load_ca_bundle, _ssl_context, check_url)
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
