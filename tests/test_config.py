import pytest

from servermon.config import (DEFAULT_TIMEOUT_SECONDS, DEFAULT_USER_AGENT,
                              ConfigError, load_config, remove_url_entry,
                              set_url_check_interval)


def write_config(tmp_path, text):
    path = tmp_path / "servermon.toml"
    path.write_text(text, encoding="utf-8")
    return path


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


def test_set_url_check_interval_inserts(tmp_path):
    path = write_config(tmp_path, SET_INTERVAL_CONFIG)
    set_url_check_interval(path, "https://one.example.com", 60)

    config = load_config(path)
    assert config.urls[0].check_interval_minutes == 60
    assert config.urls[1].check_interval_minutes == 15  # untouched
    text = path.read_text(encoding="utf-8")
    assert "# global comment" in text  # comments preserved
    assert "# entry comment" in text


def test_set_url_check_interval_replaces(tmp_path):
    path = write_config(tmp_path, SET_INTERVAL_CONFIG)
    set_url_check_interval(path, "https://two.example.com", 240)

    config = load_config(path)
    assert config.urls[1].check_interval_minutes == 240
    assert path.read_text(encoding="utf-8").count("check_interval_minutes") == 1


def test_set_url_check_interval_unknown_url(tmp_path):
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


def test_remove_url_entry(tmp_path):
    path = write_config(tmp_path, SET_INTERVAL_CONFIG)
    remove_url_entry(path, "https://one.example.com")

    config = load_config(path)
    assert [entry.url for entry in config.urls] == ["https://two.example.com"]
    text = path.read_text(encoding="utf-8")
    assert "# global comment" in text  # comments outside the block survive
    assert "one.example.com" not in text


def test_remove_last_url_entry_inserts_empty_list(tmp_path):
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


def test_remove_url_entry_unknown_url(tmp_path):
    path = write_config(tmp_path, SET_INTERVAL_CONFIG)
    with pytest.raises(ConfigError, match="no \\[\\[urls\\]\\] entry"):
        remove_url_entry(path, "https://nope.example.com")


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


def test_duplicate_device_names_rejected(tmp_path):
    # Same device name once the scheme is stripped -> same report file.
    with pytest.raises(ConfigError, match="both device"):
        load_config(
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
