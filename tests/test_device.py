import string

from servermon import __version__
from servermon.checker import CheckResult
from servermon.config import UrlEntry
from servermon.device import build_report, device_id, device_name
from servermon.state import LastError


def make_result(**overrides) -> CheckResult:
    values = {
        "url": "https://example.com",
        "status_code": 200,
        "success": True,
        "detail": "OK: HTTP 200 OK (12 ms)",
        "response_time_ms": 12,
        "match_found": None,
        "checked_at": "Wed, 15 Jul 2026 10:00:00 -0400",
        "server": "nginx/1.25.3",
    }
    values.update(overrides)
    return CheckResult(**values)


class TestDeviceName:
    def test_strips_https_scheme(self):
        assert device_name("https://example.com") == "example.com"

    def test_strips_http_scheme(self):
        assert device_name("http://example.com") == "example.com"

    def test_strips_trailing_slash(self):
        assert device_name("https://example.com/") == "example.com"

    def test_keeps_path_port_and_case(self):
        assert (
            device_name("https://Example.com:8443/Health") == "Example.com:8443/Health"
        )

    def test_no_scheme_passthrough(self):
        assert device_name("example.com") == "example.com"


class TestDeviceId:
    def test_stable(self):
        assert device_id("https://example.com") == device_id("https://example.com")

    def test_scheme_does_not_change_identity(self):
        assert device_id("http://example.com") == device_id("https://example.com")

    def test_different_urls_differ(self):
        assert device_id("https://example.com") != device_id("https://example.org")

    def test_filesystem_safe(self):
        value = device_id("https://example.com/some/path?q=1")
        assert len(value) == 64
        assert set(value) <= set(string.hexdigits.lower())


class TestBuildReport:
    def test_required_proxy_agent_keys(self):
        report = build_report(UrlEntry(url="https://example.com"), make_result())
        assert report["device id"] == device_id("https://example.com")
        assert report["data source"] == "servermon"
        assert report["computer name"] == "example.com"

    def test_check_fields(self):
        report = build_report(UrlEntry(url="https://example.com"), make_result())
        assert report["url"] == "https://example.com"
        assert report["http response code"] == 200
        assert report["http check result"].startswith("OK:")
        assert report["check success"] is True
        assert report["response time ms"] == 12
        assert report["last check time"] == "Wed, 15 Jul 2026 10:00:00 -0400"
        assert report["in proxy agent context"] is True

    def test_server_header_becomes_operating_system(self):
        report = build_report(UrlEntry(url="https://example.com"), make_result())
        assert report["operating system"]["name"] == "nginx/1.25.3"

    def test_operating_system_fallback(self):
        report = build_report(
            UrlEntry(url="https://example.com"), make_result(server="")
        )
        assert report["operating system"]["name"] == "servermon"

    def test_os_version_is_plugin_version_for_plain_http(self):
        report = build_report(UrlEntry(url="http://example.com"), make_result())
        assert report["operating system"]["version"] == __version__
        assert "tls version" not in report

    def test_os_version_is_tls_version_for_https(self):
        report = build_report(
            UrlEntry(url="https://example.com"), make_result(tls_version="TLSv1.3")
        )
        assert report["operating system"]["version"] == "1.3"
        assert report["tls version"] == "TLSv1.3"

    def test_reserved_property_inspectors(self):
        report = build_report(
            UrlEntry(url="https://example.com:8443/health"), make_result()
        )
        assert report["device type"] == "Web Server"
        assert report["dns name"] == "example.com"

    def test_remote_ip_fills_network_inspectors(self):
        report = build_report(
            UrlEntry(url="https://example.com"), make_result(peer_ip="93.184.216.34")
        )
        assert report["remote ip address"] == "93.184.216.34"
        interface = report["network"]["ip interfaces"][0]
        assert interface["address"] == "93.184.216.34"
        assert interface["loopback"] is False
        assert "adapters" not in report["network"]

    def test_loopback_ip_flagged(self):
        report = build_report(
            UrlEntry(url="http://localhost:8080"), make_result(peer_ip="127.0.0.1")
        )
        assert report["network"]["ip interfaces"][0]["loopback"] is True

    def test_ipv6_ip_also_fills_adapters(self):
        report = build_report(
            UrlEntry(url="https://example.com"),
            make_result(peer_ip="2606:2800:220:1:248:1893:25c8:1946"),
        )
        adapter = report["network"]["adapters"][0]
        assert adapter["up"] is True
        assert adapter["ipv6 interfaces"][0]["address"] == (
            "2606:2800:220:1:248:1893:25c8:1946"
        )

    def test_no_network_when_no_connection(self):
        report = build_report(UrlEntry(url="https://example.com"), make_result())
        assert "network" not in report
        assert "remote ip address" not in report

    def test_no_last_error_keys_on_success(self):
        report = build_report(UrlEntry(url="https://example.com"), make_result())
        assert "http check last error" not in report
        assert "http check last error time" not in report

    def test_last_error_keys_when_state_provides_one(self):
        last_error = LastError(
            detail="FAILED: HTTP 503 Service Unavailable (40 ms)",
            time="Tue, 14 Jul 2026 09:00:00 -0400",
        )
        report = build_report(
            UrlEntry(url="https://example.com"), make_result(), last_error=last_error
        )
        assert report["http check last error"] == last_error.detail
        assert report["http check last error time"] == last_error.time

    def test_sequence_echoed_when_present(self):
        entry = UrlEntry(url="https://example.com")
        without = build_report(entry, make_result())
        assert "device report sequence" not in without
        assert "deviceReportSequence" not in without

        with_seq = build_report(entry, make_result(), sequence=2)
        assert with_seq["device report sequence"] == 2
        assert with_seq["deviceReportSequence"] == 2

    def test_bad_string_found_only_when_no_match_configured(self):
        plain = build_report(UrlEntry(url="https://example.com"), make_result())
        assert "bad string found" not in plain

        with_no_match = build_report(
            UrlEntry(url="https://example.com", no_match="database error"),
            make_result(success=False, bad_string_found=True),
        )
        assert with_no_match["bad string found"] is True

    def test_match_found_only_when_match_configured(self):
        no_match = build_report(UrlEntry(url="https://example.com"), make_result())
        assert "match found" not in no_match

        with_match = build_report(
            UrlEntry(url="https://example.com", match="hello"),
            make_result(match_found=True),
        )
        assert with_match["match found"] is True
