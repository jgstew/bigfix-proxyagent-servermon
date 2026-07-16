from datetime import datetime

from servermon.checker import TIME_FORMAT, check_url
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
