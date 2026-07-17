import pytest

from servermon.config import (DEFAULT_TIMEOUT_SECONDS, DEFAULT_USER_AGENT,
                              ConfigError, load_config)


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
