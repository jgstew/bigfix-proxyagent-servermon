import string

from servermon import __version__
from servermon.checker import CheckResult
from servermon.config import UrlEntry
from servermon.device import (build_report, device_id, device_name,
                              device_name_with_port)
from servermon.state import DeviceRecord, LastError


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


class TestDeviceNameWithPort:
    def test_https_inserts_443(self):
        assert device_name_with_port("https://example.com") == "example.com:443"

    def test_http_inserts_80(self):
        assert device_name_with_port("http://example.com") == "example.com:80"

    def test_port_inserted_before_path(self):
        assert (
            device_name_with_port("https://example.com/health")
            == "example.com:443/health"
        )

    def test_existing_port_kept_as_is(self):
        # Already explicit -> nothing sensible to insert; plain name.
        assert device_name_with_port("https://example.com:8443") == "example.com:8443"

    def test_no_hostname_falls_back_to_plain_name(self):
        assert device_name_with_port("example.com") == "example.com"

    def test_unsplittable_url_falls_back_to_plain_name(self):
        # An unclosed IPv6 bracket makes urlsplit().port raise ValueError; the
        # name must still resolve so a failed check can report.
        assert device_name_with_port("http://[::1") == device_name("http://[::1")


class TestDeviceId:
    def test_stable(self):
        assert device_id("https://example.com") == device_id("https://example.com")

    def test_scheme_changes_identity(self):
        # Identity is now the full URL, so http:// and https:// of the same
        # host are distinct devices (allows monitoring both without collision).
        assert device_id("http://example.com") != device_id("https://example.com")

    def test_trailing_slash_does_not_change_identity(self):
        # A single trailing slash is normalized away: same resource, same id.
        assert device_id("https://example.com/") == device_id("https://example.com")

    def test_scheme_case_does_not_change_identity(self):
        assert device_id("HTTP://example.com") == device_id("http://example.com")

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

    def test_computer_name_defaults_to_base_name(self):
        report = build_report(UrlEntry(url="https://example.com"), make_result())
        assert report["computer name"] == device_name("https://example.com")

    def test_computer_name_override_used(self):
        # The disambiguated display name (with an explicit port) is threaded
        # in by the caller when two entries share a base name; the DNS name
        # stays the real hostname regardless.
        report = build_report(
            UrlEntry(url="https://example.com"),
            make_result(),
            computer_name="example.com:443",
        )
        assert report["computer name"] == "example.com:443"
        assert report["dns name"] == "example.com"

    def test_check_fields(self):
        report = build_report(UrlEntry(url="https://example.com"), make_result())
        check = report["http check"]
        assert check["url"] == "https://example.com"
        assert check["response code"] == 200
        assert check["result"].startswith("OK:")
        assert check["success"] is True
        assert check["response time ms"] == 12
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
        assert "tls version" not in report["http check"]

    def test_os_version_is_tls_version_for_https(self):
        report = build_report(
            UrlEntry(url="https://example.com"), make_result(tls_version="TLSv1.3")
        )
        assert report["operating system"]["version"] == "1.3"
        assert report["http check"]["tls version"] == "TLSv1.3"

    def test_malformed_url_still_builds_report(self):
        # "http://[::1" passes config validation but makes urlsplit raise;
        # the failed check must still produce a report so the device stays
        # visible in BigFix with the error, instead of aborting the refresh.
        url = "http://[::1"
        report = build_report(
            UrlEntry(url=url),
            make_result(
                url=url,
                status_code=0,
                success=False,
                detail="ERROR: unexpected ValueError: Invalid IPv6 URL",
                server="",
            ),
        )
        assert report["computer name"] == device_name(url)
        assert report["dns name"] == device_name(url)  # hostname unavailable
        assert report["http check"]["success"] is False

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
        assert report["http check"]["remote ip address"] == "93.184.216.34"
        interface = report["network"]["ip interfaces"][0]
        assert interface["address"] == "93.184.216.34"
        assert interface["loopback"] is False
        # Mirror the native <ip interface>: a peer we connected to is up, so the
        # same "up of ip interfaces of network" relevance works on both native
        # and proxied devices.
        assert interface["up"] is True
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

    def test_unparseable_ip_still_reported(self):
        # A peer address that ipaddress cannot parse (e.g. an unexpected
        # scoped/mapped form) must still be reported, classified by a plain
        # ":" heuristic instead of crashing report generation.
        report = build_report(
            UrlEntry(url="https://example.com"), make_result(peer_ip="bogus::ip%x")
        )
        interface = report["network"]["ip interfaces"][0]
        assert interface["address"] == "bogus::ip%x"
        assert interface["loopback"] is False
        assert "adapters" in report["network"]  # ":" -> treated as IPv6

    def test_no_network_when_no_connection(self):
        report = build_report(UrlEntry(url="https://example.com"), make_result())
        assert "network" not in report
        assert "remote ip address" not in report["http check"]

    def test_ssl_certificate_expiration_when_present(self):
        expiry = "Wed, 04 Jun 2035 11:04:38 +0000"
        report = build_report(
            UrlEntry(url="https://example.com"), make_result(cert_expires=expiry)
        )
        assert report["http check"]["ssl certificate expiration"] == expiry

    def test_no_ssl_certificate_expiration_without_cert(self):
        report = build_report(UrlEntry(url="https://example.com"), make_result())
        assert "ssl certificate expiration" not in report["http check"]

    def test_no_last_error_keys_on_success(self):
        report = build_report(UrlEntry(url="https://example.com"), make_result())
        assert "last error" not in report["http check"]
        assert "last error time" not in report["http check"]

    def test_last_error_keys_when_state_provides_one(self):
        last_error = LastError(
            detail="FAILED: HTTP 503 Service Unavailable (40 ms)",
            time="Tue, 14 Jul 2026 09:00:00 -0400",
        )
        report = build_report(
            UrlEntry(url="https://example.com"),
            make_result(),
            device_state=DeviceRecord(last_error=last_error),
        )
        assert report["http check"]["last error"] == last_error.detail
        assert report["http check"]["last error time"] == last_error.time

    def test_last_device_report_time_from_state(self):
        contact = "Tue, 14 Jul 2026 09:00:00 -0400"
        report = build_report(
            UrlEntry(url="https://example.com"),
            make_result(),
            device_state=DeviceRecord(last_contact=contact),
        )
        assert report["last device report time"] == contact

    def test_no_last_device_report_time_when_never_contacted(self):
        report = build_report(
            UrlEntry(url="https://example.com"),
            make_result(),
            device_state=DeviceRecord(),
        )
        assert "last device report time" not in report

    def test_sequence_echoed_when_present(self):
        entry = UrlEntry(url="https://example.com")
        without = build_report(entry, make_result())
        assert "device report sequence" not in without
        assert "deviceReportSequence" not in without

        with_seq = build_report(entry, make_result(), sequence=2)
        assert with_seq["device report sequence"] == 2
        assert with_seq["deviceReportSequence"] == 2

    def test_refresh_interval_uses_configured_value(self):
        report = build_report(
            UrlEntry(url="https://example.com", check_interval_minutes=240),
            make_result(),
            default_interval=60,
        )
        assert report["refresh interval"] == 240

    def test_refresh_interval_falls_back_to_default(self):
        report = build_report(
            UrlEntry(url="https://example.com"), make_result(), default_interval=60
        )
        assert report["refresh interval"] == 60

    def test_refresh_interval_absent_without_default(self):
        report = build_report(UrlEntry(url="https://example.com"), make_result())
        assert "refresh interval" not in report

    def test_bad_string_found_only_when_no_match_configured(self):
        plain = build_report(UrlEntry(url="https://example.com"), make_result())
        assert "bad string found" not in plain["http check"]

        with_no_match = build_report(
            UrlEntry(url="https://example.com", no_match="database error"),
            make_result(success=False, bad_string_found=True),
        )
        assert with_no_match["http check"]["bad string found"] is True

    def test_match_found_only_when_match_configured(self):
        no_match = build_report(UrlEntry(url="https://example.com"), make_result())
        assert "match found" not in no_match["http check"]

        with_match = build_report(
            UrlEntry(url="https://example.com", match="hello"),
            make_result(match_found=True),
        )
        assert with_match["http check"]["match found"] is True
