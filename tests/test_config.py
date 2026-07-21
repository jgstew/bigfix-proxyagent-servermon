import pytest

import servermon.config
from servermon.config import (DEFAULT_USER_AGENT, ConfigError, add_url_entry,
                              clear_url_option, load_config, remove_url_entry,
                              set_url_option, set_url_refresh_interval)


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
    # No [settings] timeout -> None here; the SDK default (45s) applies via
    # Config.timeout_for.
    assert config.timeout_seconds is None
    assert config.timeout_for(entry) == 45
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
            timeout_seconds = 120
            verify_tls = false
            """,
        )
    )
    assert config.timeout_seconds == 5
    assert config.user_agent == "custom-agent/1.0"
    first, second = config.urls
    assert first.match == "Example Domain"
    # First URL inherits the [settings] timeout; second overrides it.
    assert config.timeout_for(first) == 5
    assert second.verify_tls is False
    assert config.timeout_for(second) == 120


def test_timeout_for_default_settings_and_clamping(tmp_path):
    config = load_config(
        write_config(
            tmp_path,
            """
            [settings]
            timeout_seconds = 20

            [[urls]]
            url = "https://a.example.com"

            [[urls]]
            url = "https://b.example.com"
            timeout_seconds = 1

            [[urls]]
            url = "https://c.example.com"
            timeout_seconds = 5000
            """,
        )
    )
    a, b, c = config.urls
    assert config.timeout_for(a) == 20   # inherits [settings]
    assert config.timeout_for(b) == 45   # below the 2s minimum -> default
    assert config.timeout_for(c) == 900  # above the 900s maximum -> capped


def test_timeout_defaults_to_forty_five_without_settings(tmp_path):
    config = load_config(
        write_config(
            tmp_path,
            """
            [[urls]]
            url = "https://example.com"
            """,
        )
    )
    assert config.timeout_seconds is None
    assert config.timeout_for(config.urls[0]) == 45


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
            refresh_interval_minutes = 120
            """,
        )
    )
    assert config.urls[0].refresh_interval_minutes == 120


def test_check_interval_accepts_any_integer(tmp_path):
    # Out-of-range values are accepted at parse time and normalized later by
    # Config.refresh_interval_for; only a non-integer is a config error.
    config = load_config(
        write_config(
            tmp_path,
            """
            [[urls]]
            url = "https://example.com"
            refresh_interval_minutes = -5
            """,
        )
    )
    assert config.urls[0].refresh_interval_minutes == -5


def test_check_interval_must_be_an_integer(tmp_path):
    with pytest.raises(ConfigError, match="refresh_interval_minutes"):
        load_config(
            write_config(
                tmp_path,
                """
                [[urls]]
                url = "https://example.com"
                refresh_interval_minutes = 1.5
                """,
            )
        )


def test_settings_refresh_interval_default_and_precedence(tmp_path):
    config = load_config(
        write_config(
            tmp_path,
            """
            [settings]
            refresh_interval_minutes = 45

            [[urls]]
            url = "https://a.example.com"

            [[urls]]
            url = "https://b.example.com"
            refresh_interval_minutes = 120
            """,
        )
    )
    assert config.refresh_interval_minutes == 45
    # First URL inherits the [settings] default; second overrides it.
    assert config.refresh_interval_for(config.urls[0]) == 45
    assert config.refresh_interval_for(config.urls[1]) == 120


def test_refresh_interval_defaults_to_thirty_without_settings(tmp_path):
    config = load_config(
        write_config(
            tmp_path,
            """
            [[urls]]
            url = "https://example.com"
            """,
        )
    )
    assert config.refresh_interval_minutes is None
    assert config.refresh_interval_for(config.urls[0]) == 30


def test_settings_refresh_interval_must_be_an_integer(tmp_path):
    with pytest.raises(ConfigError, match="settings.refresh_interval_minutes"):
        load_config(
            write_config(
                tmp_path,
                """
                [settings]
                refresh_interval_minutes = "soon"

                [[urls]]
                url = "https://example.com"
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
refresh_interval_minutes = 15
"""


def test_set_url_refresh_interval_inserts(tmp_path, write_backend):
    path = write_config(tmp_path, SET_INTERVAL_CONFIG)
    set_url_refresh_interval(path, "https://one.example.com", 60)

    config = load_config(path)
    assert config.urls[0].refresh_interval_minutes == 60
    assert config.urls[1].refresh_interval_minutes == 15  # untouched
    text = path.read_text(encoding="utf-8")
    assert "# global comment" in text  # comments preserved by both backends
    assert "# entry comment" in text


def test_set_url_refresh_interval_replaces(tmp_path, write_backend):
    path = write_config(tmp_path, SET_INTERVAL_CONFIG)
    set_url_refresh_interval(path, "https://two.example.com", 240)

    config = load_config(path)
    assert config.urls[1].refresh_interval_minutes == 240
    assert path.read_text(encoding="utf-8").count("refresh_interval_minutes") == 1


def test_set_url_refresh_interval_unknown_url(tmp_path, write_backend):
    path = write_config(tmp_path, SET_INTERVAL_CONFIG)
    with pytest.raises(ConfigError, match="no \\[\\[urls\\]\\] entry"):
        set_url_refresh_interval(path, "https://nope.example.com", 60)


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


def test_set_url_option_string(tmp_path, write_backend):
    path = write_config(tmp_path, SET_INTERVAL_CONFIG)
    set_url_option(path, "https://one.example.com", "match", "hello world")

    config = load_config(path)
    assert config.urls[0].match == "hello world"
    assert "# global comment" in path.read_text(encoding="utf-8")  # preserved


def test_set_url_option_float(tmp_path, write_backend):
    path = write_config(tmp_path, SET_INTERVAL_CONFIG)
    set_url_option(path, "https://one.example.com", "timeout_seconds", 12.5)
    assert load_config(path).urls[0].timeout_seconds == 12.5


def test_set_url_option_bool(tmp_path, write_backend):
    path = write_config(tmp_path, SET_INTERVAL_CONFIG)
    set_url_option(path, "https://one.example.com", "verify_tls", False)
    assert load_config(path).urls[0].verify_tls is False


def test_set_url_option_regex_with_backslash_roundtrips(tmp_path, write_backend):
    # A backslash regex must survive the write and re-parse identically under
    # both the tomlkit and regex backends.
    path = write_config(tmp_path, SET_INTERVAL_CONFIG)
    set_url_option(path, "https://two.example.com", "no_match", r"\d{3}\s+error")
    assert load_config(path).urls[1].no_match == r"\d{3}\s+error"


def test_set_url_option_replaces_existing_value(tmp_path, write_backend):
    path = write_config(tmp_path, SET_INTERVAL_CONFIG)
    set_url_option(path, "https://two.example.com", "refresh_interval_minutes", 240)
    text = path.read_text(encoding="utf-8")
    assert text.count("refresh_interval_minutes") == 1  # replaced, not duplicated
    assert load_config(path).urls[1].refresh_interval_minutes == 240


def test_set_url_option_rejects_bad_value(tmp_path, write_backend):
    # A negative timeout is caught by the re-parse gate; file left unchanged.
    path = write_config(tmp_path, SET_INTERVAL_CONFIG)
    with pytest.raises(ConfigError):
        set_url_option(path, "https://one.example.com", "timeout_seconds", -1.0)
    assert load_config(path).urls[0].timeout_seconds is None


def test_set_url_option_unknown_url(tmp_path, write_backend):
    path = write_config(tmp_path, SET_INTERVAL_CONFIG)
    with pytest.raises(ConfigError, match="no \\[\\[urls\\]\\] entry"):
        set_url_option(path, "https://nope.example.com", "match", "x")


def test_clear_url_option_removes_value(tmp_path, write_backend):
    path = write_config(tmp_path, SET_INTERVAL_CONFIG)
    # two.example.com has refresh_interval_minutes = 15
    clear_url_option(path, "https://two.example.com", "refresh_interval_minutes")

    assert load_config(path).urls[1].refresh_interval_minutes is None
    text = path.read_text(encoding="utf-8")
    assert "refresh_interval_minutes" not in text
    assert "# global comment" in text  # unrelated content preserved
    assert "two.example.com" in text  # the entry itself stays


def test_clear_url_option_absent_key_is_noop(tmp_path, write_backend):
    path = write_config(tmp_path, SET_INTERVAL_CONFIG)
    clear_url_option(path, "https://one.example.com", "timeout_seconds")  # not set
    config = load_config(path)
    assert config.urls[0].timeout_seconds is None
    assert config.urls[0].match == "ok"  # its other options untouched


def test_clear_url_option_unknown_url(tmp_path, write_backend):
    path = write_config(tmp_path, SET_INTERVAL_CONFIG)
    with pytest.raises(ConfigError, match="no \\[\\[urls\\]\\] entry"):
        clear_url_option(path, "https://nope.example.com", "match")


def test_add_url_entry_appends(tmp_path, write_backend):
    path = write_config(tmp_path, SET_INTERVAL_CONFIG)
    add_url_entry(path, "https://three.example.com:888")

    config = load_config(path)
    assert [entry.url for entry in config.urls] == [
        "https://one.example.com",
        "https://two.example.com",
        "https://three.example.com:888",
    ]
    text = path.read_text(encoding="utf-8")
    assert "# global comment" in text  # existing content preserved by both backends
    assert "refresh_interval_minutes = 15" in text  # existing entries untouched


def test_add_url_entry_rejects_duplicate(tmp_path, write_backend):
    # Same normalized URL (trailing slash) -> same device id -> rejected by the
    # re-parse gate, and the file is left unchanged.
    path = write_config(tmp_path, SET_INTERVAL_CONFIG)
    with pytest.raises(ConfigError):
        add_url_entry(path, "https://one.example.com/")
    assert len(load_config(path).urls) == 2


def test_add_url_entry_rejects_malformed_url(tmp_path, write_backend):
    path = write_config(tmp_path, SET_INTERVAL_CONFIG)
    with pytest.raises(ConfigError):
        add_url_entry(path, "ftp://not-http.example.com")
    assert len(load_config(path).urls) == 2


def test_add_url_entry_to_empty_list(tmp_path, write_backend):
    # A config left as `urls = []` by a full delete must accept a new entry
    # (a [[urls]] table cannot coexist with `urls = []`).
    path = write_config(tmp_path, "urls = []\n")
    add_url_entry(path, "https://first.example.com")

    config = load_config(path)
    assert [entry.url for entry in config.urls] == ["https://first.example.com"]


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
    with pytest.raises(ConfigError, match="refresh_interval_minutes"):
        load_config(
            write_config(
                tmp_path,
                """
                [[urls]]
                url = "https://example.com"
                refresh_interval_minutes = true
                """,
            )
        )


def test_edit_invalid_toml_rejected_by_tomlkit(tmp_path):
    if servermon.config.load_tomlkit() is None:
        pytest.skip("tomlkit not available")
    path = write_config(tmp_path, "urls = [")
    with pytest.raises(ConfigError, match="invalid TOML"):
        set_url_refresh_interval(path, "https://one.example.com", 60)


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


def test_set_url_refresh_interval_missing_file(tmp_path, write_backend):
    with pytest.raises(ConfigError, match="cannot read"):
        set_url_refresh_interval(tmp_path / "nope.toml", "https://one.example.com", 60)


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
