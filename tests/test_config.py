import pytest

import servermon.config
from servermon.config import (DEFAULT_TIMEOUT_SECONDS, DEFAULT_USER_AGENT,
                              ConfigError, load_config, remove_url_entry,
                              set_url_check_interval)


def write_config(tmp_path, text):
    path = tmp_path / "servermon.toml"
    path.write_text(text, encoding="utf-8")
    return path


# The write functions have two backends: vendored tomlkit (preferred) and
# regex line editing (fallback). Run every write test against both so neither
# path can rot. "tomlkit" is skipped if the vendored wheel is unavailable.
@pytest.fixture(params=["tomlkit", "fallback"])
def write_backend(request, monkeypatch):
    if request.param == "fallback":
        monkeypatch.setattr(servermon.config, "load_tomlkit", lambda: None)
    elif servermon.config.load_tomlkit() is None:
        pytest.skip("tomlkit not available")
    return request.param


def test_minimal_config(tmp_path):
    config = load_config(
        write_config(
            tmp_path,
            """
            [[urls]]
            url = "https://example.com"
            """,
        )
    )
    assert len(config.urls) == 1
    entry = config.urls[0]
    assert entry.url == "https://example.com"
    assert entry.match is None
    assert entry.verify_tls is True
    assert config.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
    assert config.user_agent == DEFAULT_USER_AGENT


def test_full_config(tmp_path):
    config = load_config(
        write_config(
            tmp_path,
            """
            [settings]
            timeout_seconds = 5
            user_agent = "custom-agent/1.0"

            [[urls]]
            url = "https://example.com"
            match = "Example Domain"

            [[urls]]
            url = "http://internal.example.local:8080/health"
            timeout_seconds = 2
            verify_tls = false
            """,
        )
    )
    assert config.timeout_seconds == 5
    assert config.user_agent == "custom-agent/1.0"
    first, second = config.urls
    assert first.match == "Example Domain"
    assert config.timeout_for(first) == 5
    assert second.verify_tls is False
    assert config.timeout_for(second) == 2


def test_no_match_option(tmp_path):
    config = load_config(
        write_config(
            tmp_path,
            """
            [[urls]]
            url = "https://example.com"
            no_match = "could not connect to( the)? database"
            """,
        )
    )
    assert config.urls[0].no_match == "could not connect to( the)? database"


def test_match_invalid_regex_rejected(tmp_path):
    with pytest.raises(ConfigError, match="'match' is not a valid regex"):
        load_config(
            write_config(
                tmp_path,
                """
                [[urls]]
                url = "https://example.com"
                match = "unclosed (paren"
                """,
            )
        )


def test_no_match_invalid_regex_rejected(tmp_path):
    with pytest.raises(ConfigError, match="not a valid regex"):
        load_config(
            write_config(
                tmp_path,
                """
                [[urls]]
                url = "https://example.com"
                no_match = "unclosed [bracket"
                """,
            )
        )


def test_check_interval_option(tmp_path):
    config = load_config(
        write_config(
            tmp_path,
            """
            [[urls]]
            url = "https://example.com"
            check_interval_minutes = 120
            """,
        )
    )
    assert config.urls[0].check_interval_minutes == 120


def test_check_interval_must_be_positive_integer(tmp_path):
    with pytest.raises(ConfigError, match="check_interval_minutes"):
        load_config(
            write_config(
                tmp_path,
                """
                [[urls]]
                url = "https://example.com"
                check_interval_minutes = -5
                """,
            )
        )


SET_INTERVAL_CONFIG = """\
# global comment
[settings]
timeout_seconds = 5

[[urls]]
url = "https://one.example.com"
match = "ok"  # entry comment

[[urls]]
url = "https://two.example.com"
check_interval_minutes = 15
"""


def test_set_url_check_interval_inserts(tmp_path, write_backend):
    path = write_config(tmp_path, SET_INTERVAL_CONFIG)
    set_url_check_interval(path, "https://one.example.com", 60)

    config = load_config(path)
    assert config.urls[0].check_interval_minutes == 60
    assert config.urls[1].check_interval_minutes == 15  # untouched
    text = path.read_text(encoding="utf-8")
    assert "# global comment" in text  # comments preserved by both backends
    assert "# entry comment" in text


def test_set_url_check_interval_replaces(tmp_path, write_backend):
    path = write_config(tmp_path, SET_INTERVAL_CONFIG)
    set_url_check_interval(path, "https://two.example.com", 240)

    config = load_config(path)
    assert config.urls[1].check_interval_minutes == 240
    assert path.read_text(encoding="utf-8").count("check_interval_minutes") == 1


def test_set_url_check_interval_unknown_url(tmp_path, write_backend):
    path = write_config(tmp_path, SET_INTERVAL_CONFIG)
    with pytest.raises(ConfigError, match="no \\[\\[urls\\]\\] entry"):
        set_url_check_interval(path, "https://nope.example.com", 60)


def test_heartbeat_minutes_reads_settings_json(tmp_path):
    from servermon.config import DEFAULT_HEARTBEAT_MINUTES, heartbeat_minutes

    settings = tmp_path / "settings.json"
    settings.write_text(
        '{"ID": "servermon", "DeviceReportRefreshIntervalMinutes": 15}',
        encoding="utf-8",
    )
    assert heartbeat_minutes(settings) == 15
    # Missing or unreadable settings.json falls back to the shipped default.
    assert heartbeat_minutes(tmp_path / "nope.json") == DEFAULT_HEARTBEAT_MINUTES


def test_empty_urls_list_is_allowed(tmp_path):
    config = load_config(write_config(tmp_path, "urls = []\n"))
    assert config.urls == ()


def test_remove_url_entry(tmp_path, write_backend):
    path = write_config(tmp_path, SET_INTERVAL_CONFIG)
    remove_url_entry(path, "https://one.example.com")

    config = load_config(path)
    assert [entry.url for entry in config.urls] == ["https://two.example.com"]
    text = path.read_text(encoding="utf-8")
    assert "# global comment" in text  # comments outside the block survive
    assert "one.example.com" not in text


def test_remove_last_url_entry_inserts_empty_list(tmp_path, write_backend):
    path = write_config(
        tmp_path,
        """
        [[urls]]
        url = "https://only.example.com"
        """,
    )
    remove_url_entry(path, "https://only.example.com")

    config = load_config(path)
    assert config.urls == ()
    assert "urls = []" in path.read_text(encoding="utf-8")


def test_remove_url_entry_unknown_url(tmp_path, write_backend):
    path = write_config(tmp_path, SET_INTERVAL_CONFIG)
    with pytest.raises(ConfigError, match="no \\[\\[urls\\]\\] entry"):
        remove_url_entry(path, "https://nope.example.com")


def test_vendored_tomlkit_is_loadable():
    # The vendored wheel must import (via zipimport) even with no pip-installed
    # tomlkit; this is the deployment path on the Proxy Agent host.
    from servermon._vendor import load_tomlkit, vendored_wheel_name

    assert vendored_wheel_name() is not None
    assert vendored_wheel_name().endswith(".whl")
    assert load_tomlkit() is not None  # installed or vendored, either is fine


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")


def test_invalid_toml(tmp_path):
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(write_config(tmp_path, "urls = ["))


def test_no_urls(tmp_path):
    with pytest.raises(ConfigError, match=r"\[\[urls\]\]"):
        load_config(write_config(tmp_path, "[settings]\ntimeout_seconds = 5\n"))


def test_url_requires_http_scheme(tmp_path):
    with pytest.raises(ConfigError, match="http://"):
        load_config(
            write_config(
                tmp_path,
                """
                [[urls]]
                url = "ftp://example.com"
                """,
            )
        )


def test_unknown_key_rejected(tmp_path):
    with pytest.raises(ConfigError, match="unknown key"):
        load_config(
            write_config(
                tmp_path,
                """
                [[urls]]
                url = "https://example.com"
                mtach = "typo"
                """,
            )
        )


def test_exact_duplicate_urls_rejected_with_positions(tmp_path):
    with pytest.raises(ConfigError, match="entries 1 and 3 are exact duplicates"):
        load_config(
            write_config(
                tmp_path,
                """
                [[urls]]
                url = "https://localhost:42444/fake2.html"

                [[urls]]
                url = "https://example.com"

                [[urls]]
                url = "https://localhost:42444/fake2.html"
                """,
            )
        )


def test_http_and_https_same_host_accepted_and_disambiguated(tmp_path):
    # Identity is now the full URL, so http:// and https:// of the same host
    # are distinct devices. Their scheme-less display names would collide, so
    # the loader disambiguates them by inserting the explicit default port.
    config = load_config(
        write_config(
            tmp_path,
            """
            [[urls]]
            url = "https://example.com"

            [[urls]]
            url = "http://example.com/"
            """,
        )
    )
    assert len(config.urls) == 2
    names = {entry.url: config.display_name(entry) for entry in config.urls}
    assert names["https://example.com"] == "example.com:443"
    assert names["http://example.com/"] == "example.com:80"


def test_display_name_without_collision_is_base(tmp_path):
    config = load_config(
        write_config(
            tmp_path,
            """
            [[urls]]
            url = "https://example.com"
            """,
        )
    )
    assert config.display_name(config.urls[0]) == "example.com"


def test_display_name_collision_inserts_port_before_path(tmp_path):
    config = load_config(
        write_config(
            tmp_path,
            """
            [[urls]]
            url = "https://example.com/health"

            [[urls]]
            url = "http://example.com/health"
            """,
        )
    )
    names = {entry.url: config.display_name(entry) for entry in config.urls}
    assert names["https://example.com/health"] == "example.com:443/health"
    assert names["http://example.com/health"] == "example.com:80/health"


def test_normalized_duplicate_rejected(tmp_path):
    # Same normalized URL (differ only by a trailing slash) -> same device id.
    with pytest.raises(ConfigError, match="same device"):
        load_config(
            write_config(
                tmp_path,
                """
                [[urls]]
                url = "https://example.com"

                [[urls]]
                url = "https://example.com/"
                """,
            )
        )


def test_exact_duplicate_rejected(tmp_path):
    with pytest.raises(ConfigError, match="exact duplicate"):
        load_config(
            write_config(
                tmp_path,
                """
                [[urls]]
                url = "https://example.com"

                [[urls]]
                url = "https://example.com"
                """,
            )
        )


def test_settings_must_be_a_table(tmp_path):
    with pytest.raises(ConfigError, match=r"\[settings\] must be a table"):
        load_config(
            write_config(
                tmp_path,
                """
                settings = "oops"

                [[urls]]
                url = "https://example.com"
                """,
            )
        )


def test_urls_entry_must_be_a_table(tmp_path):
    with pytest.raises(ConfigError, match="must be a table"):
        load_config(write_config(tmp_path, 'urls = ["https://example.com"]\n'))


def test_empty_user_agent_rejected(tmp_path):
    with pytest.raises(ConfigError, match="user_agent"):
        load_config(
            write_config(
                tmp_path,
                """
                [settings]
                user_agent = ""

                [[urls]]
                url = "https://example.com"
                """,
            )
        )


def test_verify_tls_must_be_boolean(tmp_path):
    with pytest.raises(ConfigError, match="verify_tls"):
        load_config(
            write_config(
                tmp_path,
                """
                [[urls]]
                url = "https://example.com"
                verify_tls = "no"
                """,
            )
        )


def test_measure_network_hops_must_be_boolean(tmp_path):
    with pytest.raises(ConfigError, match="measure_network_hops"):
        load_config(
            write_config(
                tmp_path,
                """
                [[urls]]
                url = "https://example.com"
                measure_network_hops = 1
                """,
            )
        )


def test_empty_match_rejected(tmp_path):
    with pytest.raises(ConfigError, match="'match' must be a non-empty string"):
        load_config(
            write_config(
                tmp_path,
                """
                [[urls]]
                url = "https://example.com"
                match = ""
                """,
            )
        )


def test_bad_per_url_timeout_rejected(tmp_path):
    with pytest.raises(ConfigError, match="'timeout_seconds'"):
        load_config(
            write_config(
                tmp_path,
                """
                [[urls]]
                url = "https://example.com"
                timeout_seconds = 0
                """,
            )
        )


def test_check_interval_boolean_rejected(tmp_path):
    # TOML "true" parses as a Python bool, which would pass a naive int
    # check; it must still be rejected.
    with pytest.raises(ConfigError, match="check_interval_minutes"):
        load_config(
            write_config(
                tmp_path,
                """
                [[urls]]
                url = "https://example.com"
                check_interval_minutes = true
                """,
            )
        )


def test_heartbeat_minutes_rejects_bad_values(tmp_path):
    from servermon.config import DEFAULT_HEARTBEAT_MINUTES, heartbeat_minutes

    cases = [
        '{"DeviceReportRefreshIntervalMinutes": true}',  # bool is not an int
        '{"DeviceReportRefreshIntervalMinutes": 0}',  # below minimum
        '{"DeviceReportRefreshIntervalMinutes": "15"}',  # string is not an int
        "{not json",
    ]
    for i, content in enumerate(cases):
        settings = tmp_path / f"settings-{i}.json"
        settings.write_text(content, encoding="utf-8")
        assert heartbeat_minutes(settings) == DEFAULT_HEARTBEAT_MINUTES, content


def test_edit_invalid_toml_rejected_by_tomlkit(tmp_path):
    if servermon.config.load_tomlkit() is None:
        pytest.skip("tomlkit not available")
    path = write_config(tmp_path, "urls = [")
    with pytest.raises(ConfigError, match="invalid TOML"):
        set_url_check_interval(path, "https://one.example.com", 60)


def test_remove_url_line_without_urls_header(tmp_path, monkeypatch):
    # Regex fallback: a top-level "url =" line with no [[urls]] header above
    # it must be a clear error, not a bogus deletion.
    monkeypatch.setattr(servermon.config, "load_tomlkit", lambda: None)
    path = write_config(
        tmp_path,
        """
        url = "https://stray.example.com"

        [[urls]]
        url = "https://real.example.com"
        """,
    )
    with pytest.raises(ConfigError, match=r"no \[\[urls\]\] header"):
        remove_url_entry(path, "https://stray.example.com")


def test_corrupting_edit_is_refused(tmp_path, monkeypatch):
    """The regex fallback validates its result before writing: an edit that
    would leave unparsable TOML must raise and leave the file untouched.

    A multiline match value fools the fallback's line-based table-end
    detection, so removing the entry would cut the string in half.
    """
    monkeypatch.setattr(servermon.config, "load_tomlkit", lambda: None)
    original = """\
[[urls]]
url = "https://one.example.com"

[[urls]]
url = "https://victim.example.com"
match = \"\"\"
[abc]
more regex\"\"\"
"""
    path = write_config(tmp_path, original)
    load_config(path)  # sanity: the original file is valid

    with pytest.raises(ConfigError, match="edit would corrupt"):
        remove_url_entry(path, "https://victim.example.com")
    assert path.read_text(encoding="utf-8") == original  # untouched


def test_set_url_check_interval_missing_file(tmp_path, write_backend):
    with pytest.raises(ConfigError, match="cannot read"):
        set_url_check_interval(tmp_path / "nope.toml", "https://one.example.com", 60)


def test_remove_url_entry_missing_file(tmp_path, write_backend):
    with pytest.raises(ConfigError, match="cannot read"):
        remove_url_entry(tmp_path / "nope.toml", "https://one.example.com")


def test_bad_timeout_rejected(tmp_path):
    with pytest.raises(ConfigError, match="timeout_seconds"):
        load_config(
            write_config(
                tmp_path,
                """
                [settings]
                timeout_seconds = -1

                [[urls]]
                url = "https://example.com"
                """,
            )
        )
