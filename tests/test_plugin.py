"""End-to-end tests: command files in, device reports / command results out."""

import email.utils
import json
from datetime import datetime

import pytest

from servermon.config import Config, UrlEntry, load_config
from servermon.device import device_id
from servermon.plugin import ServerMonPlugin


@pytest.fixture
def dirs(tmp_path):
    pending = tmp_path / "PendingCommands"
    output = tmp_path / "DeviceReports"
    pending.mkdir()
    output.mkdir()
    return pending, output


def write_command(pending, payload, name="0001.json"):
    path = pending / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def make_plugin(http_server):
    config = Config(
        urls=(
            UrlEntry(url=f"{http_server}/ok", match="hello from"),
            UrlEntry(url=f"{http_server}/does-not-exist"),
        ),
        timeout_seconds=5,
    )
    return ServerMonPlugin(config)


def read_report(output, url):
    path = output / f"{device_id(url)}.report"
    assert path.is_file(), f"missing report for {url}"
    return json.loads(path.read_text(encoding="utf-8"))


def test_full_refresh_writes_all_reports(http_server, dirs):
    pending, output = dirs
    plugin = make_plugin(http_server)
    command_file = write_command(
        pending, {"CommandName": "refresh", "OutputDirectory": str(output)}
    )

    plugin.process_command_dir(pending)

    ok = read_report(output, f"{http_server}/ok")
    assert ok["http check"]["response code"] == 200
    assert ok["http check"]["success"] is True
    assert ok["http check"]["match found"] is True
    assert ok["data source"] == "servermon"
    assert ok["computer name"].endswith("/ok")
    assert not ok["computer name"].startswith("http")
    # No per-URL interval configured -> the settings.json heartbeat (60).
    assert ok["refresh interval"] == 60

    missing = read_report(output, f"{http_server}/does-not-exist")
    assert missing["http check"]["response code"] == 404
    assert missing["http check"]["success"] is False
    assert missing["http check"]["result"].startswith("FAILED:")
    assert "match found" not in missing["http check"]

    # Last-error keys appear only in failed reports, so BigFix retains the
    # previous error across later successful reports.
    assert missing["http check"]["last error"] == missing["http check"]["result"]
    assert missing["http check"]["last error time"] == missing["last check time"]
    assert "last error" not in ok["http check"]

    # Deleting the command file acknowledges the refresh was processed.
    assert not command_file.is_file()


def test_partial_refresh_targets_one_device(http_server, dirs, tmp_path):
    pending, output = dirs
    # The other URL is already known (checked before), so the targeted
    # refresh must not touch it.
    state_file = tmp_path / "servermon-state.json"
    state_file.write_text(
        json.dumps(
            {device_id(f"{http_server}/does-not-exist"): {"last check": "whenever"}}
        ),
        encoding="utf-8",
    )
    config = Config(
        urls=(
            UrlEntry(url=f"{http_server}/ok", match="hello from"),
            UrlEntry(url=f"{http_server}/does-not-exist"),
        ),
        timeout_seconds=5,
    )
    plugin = ServerMonPlugin(config, state_file=state_file)
    target = device_id(f"{http_server}/ok")
    write_command(
        pending,
        {
            "CommandName": "refresh",
            "OutputDirectory": str(output),
            "TargetDevice": target,
        },
    )

    plugin.process_command_dir(pending)

    reports = list(output.glob("*.report"))
    assert len(reports) == 1
    assert reports[0].name == f"{target}.report"


def test_new_config_url_reported_on_any_refresh(http_server, dirs, tmp_path):
    """A URL newly added to servermon.toml is picked up on the next
    invocation even if the Proxy Agent only sent a targeted refresh for a.

    different, already-known device.
    """
    pending, output = dirs
    known = f"{http_server}/ok"
    new = f"{http_server}/error"
    state_file = tmp_path / "servermon-state.json"
    state_file.write_text(
        json.dumps({device_id(known): {"last check": "whenever"}}), encoding="utf-8"
    )
    config = Config(
        urls=(UrlEntry(url=known), UrlEntry(url=new)), timeout_seconds=5
    )
    plugin = ServerMonPlugin(config, state_file=state_file)
    write_command(
        pending,
        {
            "CommandName": "refresh",
            "OutputDirectory": str(output),
            "TargetDevice": device_id(known),
            "deviceReportSequence": 5,
        },
        name=f"Refresh-{device_id(known)}.command",
    )

    plugin.process_command_dir(pending)

    targeted = read_report(output, known)
    assert targeted["deviceReportSequence"] == 5

    piggybacked = read_report(output, new)
    assert piggybacked["http check"]["response code"] == 500
    # The sequence belongs to the targeted device, not the new one.
    assert "deviceReportSequence" not in piggybacked
    assert "device report sequence" not in piggybacked


def test_partial_refresh_unknown_device_writes_nothing(http_server, dirs):
    pending, output = dirs
    plugin = make_plugin(http_server)
    command_file = write_command(
        pending,
        {
            "CommandName": "refresh",
            "OutputDirectory": str(output),
            "TargetDevice": "no-such-device",
        },
    )

    plugin.process_command_dir(pending)

    assert list(output.glob("*.report")) == []
    # Consumed anyway, so it does not linger in PendingCommands forever.
    assert not command_file.is_file()


def test_real_world_per_device_refresh_command(http_server, dirs):
    """The exact command shape a live 10.x Proxy Agent writes into
    PendingCommands (observed on a real deployment).
    """
    pending, output = dirs
    plugin = make_plugin(http_server)
    target = device_id(f"{http_server}/ok")
    command_file = write_command(
        pending,
        {
            "outputDirectory": str(output),
            "targetDevice": target,
            "commandName": "refresh",
            "requiredProperties": [
                "check success",
                "http check last error",
                "http check last error time",
                "http check result",
                "http response code",
                "in proxy agent context",
                "last check time",
                "match found",
                "response time ms",
                "servermon version",
                "url",
            ],
            "deviceReportSequence": 2,
        },
        name=f"Refresh-{target}.command",
    )

    plugin.process_command_dir(pending)

    report = read_report(output, f"{http_server}/ok")
    assert report["device id"] == target
    assert report["device report sequence"] == 2
    assert report["deviceReportSequence"] == 2
    assert not command_file.is_file()


def test_unsupported_command_reports_error(http_server, dirs):
    pending, output = dirs
    plugin = make_plugin(http_server)
    target = device_id(f"{http_server}/ok")
    command_file = write_command(
        pending,
        {
            "CommandName": "locate",
            "OutputDirectory": str(output),
            "TargetDevice": target,
            "CommandID": "314159",
        },
    )

    plugin.process_command_dir(pending)

    # Result files are named <commandID>-<PID>-<seq>.json per the plugin
    # interface spec, so concurrent plugin instances cannot collide.
    result_files = list(output.glob("314159-*.json"))
    assert len(result_files) == 1
    result = json.loads(result_files[0].read_text(encoding="utf-8"))
    assert result == [{"CommandID": "314159", "DeviceID": target, "Result": "Error"}]
    # Non-refresh command files are consumed by the plugin (like trask).
    assert not command_file.is_file()


def test_last_error_persists_across_runs(http_server, dirs, tmp_path):
    """A recovered device keeps reporting its most recent error, even from a
    fresh plugin process (state comes from the state file).
    """
    pending, output = dirs
    state_file = tmp_path / "servermon-state.json"
    config = Config(urls=(UrlEntry(url=f"{http_server}/flaky"),), timeout_seconds=5)

    # First run: /flaky returns 500, so the error is recorded and reported.
    plugin = ServerMonPlugin(config, state_file=state_file)
    write_command(pending, {"CommandName": "refresh", "OutputDirectory": str(output)})
    plugin.process_command_dir(pending)
    first = read_report(output, f"{http_server}/flaky")
    assert first["http check"]["response code"] == 500
    assert first["http check"]["last error"] == first["http check"]["result"]

    # Second run in a new plugin instance: /flaky has recovered, but the
    # previous error (and its time) must still be present in the report.
    plugin = ServerMonPlugin(config, state_file=state_file)
    write_command(pending, {"CommandName": "refresh", "OutputDirectory": str(output)})
    plugin.process_command_dir(pending)
    second = read_report(output, f"{http_server}/flaky")
    assert second["http check"]["response code"] == 200
    assert second["http check"]["success"] is True
    assert second["http check"]["last error"] == first["http check"]["last error"]
    assert second["http check"]["last error time"] == first["http check"]["last error time"]
    # Both runs got HTTP responses (500 then 200), so the contact time
    # advances to the latest check.
    assert second["last device report time"] == second["last check time"]


def test_partial_refresh_falls_back_to_target_hint(http_server, dirs, tmp_path):
    """If the device id does not match, the target hint (the URL, per
    TargetHintRelevance in settings.json) still finds the entry.
    """
    pending, output = dirs
    # Mark the other URL as already known so it is not piggybacked as new.
    state_file = tmp_path / "servermon-state.json"
    state_file.write_text(
        json.dumps(
            {device_id(f"{http_server}/does-not-exist"): {"last check": "whenever"}}
        ),
        encoding="utf-8",
    )
    config = Config(
        urls=(
            UrlEntry(url=f"{http_server}/ok", match="hello from"),
            UrlEntry(url=f"{http_server}/does-not-exist"),
        ),
        timeout_seconds=5,
    )
    plugin = ServerMonPlugin(config, state_file=state_file)
    write_command(
        pending,
        {
            "CommandName": "refresh",
            "OutputDirectory": str(output),
            "TargetDevice": "some-legacy-or-foreign-id",
            "TargetHint": f"{http_server}/ok",
        },
        name="Refresh-hint.command",
    )

    plugin.process_command_dir(pending)

    reports = list(output.glob("*.report"))
    assert len(reports) == 1
    assert reports[0].name == f"{device_id(f'{http_server}/ok')}.report"


def test_non_command_files_ignored(http_server, dirs):
    pending, output = dirs
    plugin = make_plugin(http_server)
    stray = pending / "Thumbs.db"
    stray.write_text("not a command", encoding="utf-8")
    (pending / "subdir.json").mkdir()  # a directory is not a command either

    plugin.process_command_dir(pending)  # must not raise

    assert stray.is_file()  # not consumed
    assert list(output.glob("*.report")) == []


def test_report_contains_last_server_communication(http_server, dirs):
    pending, output = dirs
    plugin = make_plugin(http_server)
    write_command(pending, {"CommandName": "refresh", "OutputDirectory": str(output)})

    plugin.process_command_dir(pending)

    report = read_report(output, f"{http_server}/ok")
    assert report["last server communication"] == report["last check time"]
    # The URL answered, so the contact time (-> console Last Report Time)
    # is this check's time.
    assert report["last device report time"] == report["last check time"]


def test_action_refresh_writes_command_result(http_server, dirs):
    """A refresh carrying a commandID is an actionscript-driven "check now":
    it expects a command result (Completed/Failed per the check outcome).

    instead of device reports.
    """
    pending, output = dirs
    plugin = make_plugin(http_server)
    target = device_id(f"{http_server}/does-not-exist")
    command_file = write_command(
        pending,
        {
            "commandName": "refresh",
            "outputDirectory": str(output),
            "targetDevice": target,
            "commandID": "271828-0",
        },
        name="271828-0.command",
    )

    plugin.process_command_dir(pending)

    # No device reports: the outputDirectory is the action-results dir.
    assert list(output.glob("*.report")) == []
    result_files = list(output.glob("271828-0-*.json"))
    assert len(result_files) == 1
    result = json.loads(result_files[0].read_text(encoding="utf-8"))
    assert result == [
        {"CommandID": "271828-0", "DeviceID": target, "Result": "Failed"}
    ]
    assert not command_file.is_file()


def test_action_refresh_success_reports_completed(http_server, dirs):
    pending, output = dirs
    plugin = make_plugin(http_server)
    target = device_id(f"{http_server}/ok")
    write_command(
        pending,
        {
            "commandName": "refresh",
            "outputDirectory": str(output),
            "targetDevice": target,
            "commandID": "271829-0",
        },
    )

    plugin.process_command_dir(pending)

    result = json.loads(
        next(iter(output.glob("271829-0-*.json"))).read_text(encoding="utf-8")
    )
    assert result[0]["Result"] == "Completed"


def test_check_interval_replays_cached_report(http_server, dirs, tmp_path):
    """Within the check interval, no new HTTP check happens but the cached
    report is still re-submitted (fresh), so pending actions and the Proxy.

    Agent keep flowing.
    """
    pending, output = dirs
    url = f"{http_server}/ok"
    target = device_id(url)
    now = email.utils.format_datetime(datetime.now().astimezone())
    # A sentinel response code proves the report came from the cache.
    cached_report = {
        "device id": target,
        "computer name": "cached-device",
        "data source": "servermon",
        "http check": {"response code": 299},
        "last check time": "Mon, 13 Jul 2026 07:00:00 -0400",
        "last server communication": "Mon, 13 Jul 2026 07:00:00 -0400",
    }
    state_file = tmp_path / "servermon-state.json"
    state_file.write_text(
        json.dumps({target: {"last check": now, "last report": cached_report}}),
        encoding="utf-8",
    )
    config = Config(
        urls=(UrlEntry(url=url, check_interval_minutes=60),), timeout_seconds=5
    )
    plugin = ServerMonPlugin(config, state_file=state_file)
    command_file = write_command(
        pending, {"CommandName": "refresh", "OutputDirectory": str(output)}
    )

    plugin.process_command_dir(pending)

    report = read_report(output, url)
    assert report["http check"]["response code"] == 299  # cached, not re-checked
    assert report["last check time"] == "Mon, 13 Jul 2026 07:00:00 -0400"
    # ...but the report itself is fresh so the Proxy Agent treats it as new.
    assert report["last server communication"] != "Mon, 13 Jul 2026 07:00:00 -0400"
    # The reported cadence reflects the current config, not the cached one.
    assert report["refresh interval"] == 60
    assert not command_file.is_file()


def _within_interval_state(target, extra=None):
    """State for a URL checked just now, with a sentinel-code cached report,
    so a plain refresh within the interval would replay the cache (299).
    """
    now = email.utils.format_datetime(datetime.now().astimezone())
    entry = {
        "last check": now,
        "last report": {
            "device id": target,
            "computer name": "cached-device",
            "data source": "servermon",
            "http check": {"response code": 299},
        },
    }
    entry.update(extra or {})
    return {target: entry}


def test_version_bump_forces_check_within_interval(http_server, dirs, tmp_path):
    """A servermon major/minor upgrade forces a real HTTP check even within
    the URL's check interval, so any report-shape change reaches BigFix.

    promptly instead of waiting out the interval on a stale cached report.
    """
    pending, output = dirs
    url = f"{http_server}/ok"
    target = device_id(url)
    state_file = tmp_path / "servermon-state.json"
    # An older major.minor than the current __version__.
    state_file.write_text(
        json.dumps(_within_interval_state(target, {"last check version": "0.0.0"})),
        encoding="utf-8",
    )
    config = Config(
        urls=(UrlEntry(url=url, check_interval_minutes=60),), timeout_seconds=5
    )
    plugin = ServerMonPlugin(config, state_file=state_file)
    write_command(pending, {"CommandName": "refresh", "OutputDirectory": str(output)})

    plugin.process_command_dir(pending)

    # A real check ran (200), not the cached sentinel (299).
    assert read_report(output, url)["http check"]["response code"] == 200


def test_patch_bump_does_not_force_check(http_server, dirs, tmp_path):
    """A patch-level upgrade (same major.minor) does not force a check: the
    cached report is replayed as usual within the interval.
    """
    from servermon import __version__

    pending, output = dirs
    url = f"{http_server}/ok"
    target = device_id(url)
    major, minor = __version__.split(".")[:2]
    same_minor = f"{major}.{minor}.0"  # same major.minor, patch may differ
    state_file = tmp_path / "servermon-state.json"
    state_file.write_text(
        json.dumps(
            _within_interval_state(target, {"last check version": same_minor})
        ),
        encoding="utf-8",
    )
    config = Config(
        urls=(UrlEntry(url=url, check_interval_minutes=60),), timeout_seconds=5
    )
    plugin = ServerMonPlugin(config, state_file=state_file)
    write_command(pending, {"CommandName": "refresh", "OutputDirectory": str(output)})

    plugin.process_command_dir(pending)

    assert read_report(output, url)["http check"]["response code"] == 299  # cached


def test_version_downgrade_does_not_force_check(http_server, dirs, tmp_path):
    """Running an older servermon than last time (a rollback) does not force
    a check; only an upgrade does.
    """
    pending, output = dirs
    url = f"{http_server}/ok"
    target = device_id(url)
    state_file = tmp_path / "servermon-state.json"
    state_file.write_text(
        json.dumps(
            _within_interval_state(target, {"last check version": "999.0.0"})
        ),
        encoding="utf-8",
    )
    config = Config(
        urls=(UrlEntry(url=url, check_interval_minutes=60),), timeout_seconds=5
    )
    plugin = ServerMonPlugin(config, state_file=state_file)
    write_command(pending, {"CommandName": "refresh", "OutputDirectory": str(output)})

    plugin.process_command_dir(pending)

    assert read_report(output, url)["http check"]["response code"] == 299  # cached


def test_missing_last_check_version_does_not_force_check(http_server, dirs, tmp_path):
    """State predating this feature has no recorded version, so there is no
    baseline to compare: fall back to the normal interval (replay the cache).

    rather than re-checking every URL on the first upgrade.
    """
    pending, output = dirs
    url = f"{http_server}/ok"
    target = device_id(url)
    state_file = tmp_path / "servermon-state.json"
    state_file.write_text(
        json.dumps(_within_interval_state(target)), encoding="utf-8"
    )
    config = Config(
        urls=(UrlEntry(url=url, check_interval_minutes=60),), timeout_seconds=5
    )
    plugin = ServerMonPlugin(config, state_file=state_file)
    write_command(pending, {"CommandName": "refresh", "OutputDirectory": str(output)})

    plugin.process_command_dir(pending)

    assert read_report(output, url)["http check"]["response code"] == 299  # cached


class TestMajorMinor:
    def test_parses_major_minor_ignoring_patch(self):
        from servermon.plugin import _major_minor

        assert _major_minor("2.3.0") == (2, 3)
        assert _major_minor("10.4.7.dev1") == (10, 4)

    def test_returns_none_for_unusable_versions(self):
        from servermon.plugin import _major_minor

        assert _major_minor(None) is None
        assert _major_minor("") is None
        assert _major_minor("2") is None  # no minor component
        assert _major_minor("2.x") is None  # non-numeric minor


def test_unparseable_current_version_does_not_force(
    http_server, dirs, tmp_path, monkeypatch
):
    """If the running version cannot be parsed, fall back to the normal
    interval rather than forcing (or suppressing) a check on every URL.
    """
    import servermon.plugin

    monkeypatch.setattr(servermon.plugin, "__version__", "not-a-version")
    pending, output = dirs
    url = f"{http_server}/ok"
    target = device_id(url)
    state_file = tmp_path / "servermon-state.json"
    state_file.write_text(
        json.dumps(_within_interval_state(target, {"last check version": "0.0.0"})),
        encoding="utf-8",
    )
    config = Config(
        urls=(UrlEntry(url=url, check_interval_minutes=60),), timeout_seconds=5
    )
    plugin = ServerMonPlugin(config, state_file=state_file)
    write_command(pending, {"CommandName": "refresh", "OutputDirectory": str(output)})

    plugin.process_command_dir(pending)

    assert read_report(output, url)["http check"]["response code"] == 299  # cached


def test_replayed_cached_report_echoes_sequence(http_server, dirs, tmp_path):
    pending, output = dirs
    url = f"{http_server}/ok"
    target = device_id(url)
    now = email.utils.format_datetime(datetime.now().astimezone())
    cached_report = {
        "device id": target,
        "computer name": "cached-device",
        "data source": "servermon",
        "http check": {"response code": 299},
    }
    state_file = tmp_path / "servermon-state.json"
    state_file.write_text(
        json.dumps({target: {"last check": now, "last report": cached_report}}),
        encoding="utf-8",
    )
    config = Config(
        urls=(UrlEntry(url=url, check_interval_minutes=60),), timeout_seconds=5
    )
    plugin = ServerMonPlugin(config, state_file=state_file)
    write_command(
        pending,
        {
            "CommandName": "refresh",
            "OutputDirectory": str(output),
            "TargetDevice": target,
            "deviceReportSequence": 9,
        },
    )

    plugin.process_command_dir(pending)

    report = read_report(output, url)
    assert report["http check"]["response code"] == 299  # replayed from cache
    # The Proxy Agent's sequence number must ride along on the replay too.
    assert report["device report sequence"] == 9
    assert report["deviceReportSequence"] == 9


def test_garbage_last_check_time_checks_again(http_server, dirs, tmp_path):
    # An unparsable "last check" (hand-edited state) must fail open: check.
    pending, output = dirs
    url = f"{http_server}/ok"
    state_file = tmp_path / "servermon-state.json"
    state_file.write_text(
        json.dumps({device_id(url): {"last check": "not a date"}}), encoding="utf-8"
    )
    config = Config(
        urls=(UrlEntry(url=url, check_interval_minutes=60),), timeout_seconds=5
    )
    plugin = ServerMonPlugin(config, state_file=state_file)
    write_command(pending, {"CommandName": "refresh", "OutputDirectory": str(output)})

    plugin.process_command_dir(pending)

    assert read_report(output, url)["http check"]["response code"] == 200


def test_check_interval_without_cache_checks_anyway(http_server, dirs, tmp_path):
    pending, output = dirs
    url = f"{http_server}/ok"
    now = email.utils.format_datetime(datetime.now().astimezone())
    state_file = tmp_path / "servermon-state.json"
    # Recently checked, but no cached report (e.g. state from an older
    # plugin version): must fall back to a real check.
    state_file.write_text(
        json.dumps({device_id(url): {"last check": now}}), encoding="utf-8"
    )
    config = Config(
        urls=(UrlEntry(url=url, check_interval_minutes=60),), timeout_seconds=5
    )
    plugin = ServerMonPlugin(config, state_file=state_file)
    write_command(pending, {"CommandName": "refresh", "OutputDirectory": str(output)})

    plugin.process_command_dir(pending)

    assert read_report(output, url)["http check"]["response code"] == 200


def test_check_interval_elapsed_checks_again(http_server, dirs, tmp_path):
    pending, output = dirs
    state_file = tmp_path / "servermon-state.json"
    state_file.write_text(
        json.dumps(
            {
                device_id(f"{http_server}/ok"): {
                    "last check": "Mon, 13 Jul 2026 07:00:00 -0400"
                }
            }
        ),
        encoding="utf-8",
    )
    config = Config(
        urls=(UrlEntry(url=f"{http_server}/ok", check_interval_minutes=60),),
        timeout_seconds=5,
    )
    plugin = ServerMonPlugin(config, state_file=state_file)
    write_command(pending, {"CommandName": "refresh", "OutputDirectory": str(output)})

    plugin.process_command_dir(pending)

    assert (output / f"{device_id(f'{http_server}/ok')}.report").is_file()


def test_action_refresh_ignores_check_interval(http_server, dirs, tmp_path):
    pending, output = dirs
    state_file = tmp_path / "servermon-state.json"
    config = Config(
        urls=(UrlEntry(url=f"{http_server}/ok", check_interval_minutes=60),),
        timeout_seconds=5,
    )
    target = device_id(f"{http_server}/ok")

    plugin = ServerMonPlugin(config, state_file=state_file)
    write_command(pending, {"CommandName": "refresh", "OutputDirectory": str(output)})
    plugin.process_command_dir(pending)  # records a fresh "last check"

    plugin = ServerMonPlugin(config, state_file=state_file)
    write_command(
        pending,
        {
            "commandName": "refresh",
            "outputDirectory": str(output),
            "targetDevice": target,
            "commandID": "999-0",
        },
    )
    plugin.process_command_dir(pending)

    result = json.loads(
        next(iter(output.glob("999-0-*.json"))).read_text(encoding="utf-8")
    )
    assert result[0]["Result"] == "Completed"


def write_toml_config(tmp_path, http_server):
    path = tmp_path / "servermon.toml"
    path.write_text(
        f"""
        [[urls]]
        url = "{http_server}/ok"
        """,
        encoding="utf-8",
    )
    return path


def test_set_refresh_interval_command(http_server, dirs, tmp_path):
    from servermon.config import load_config

    pending, output = dirs
    config_path = write_toml_config(tmp_path, http_server)
    config = load_config(config_path)
    plugin = ServerMonPlugin(config, config_path=config_path)
    target = device_id(f"{http_server}/ok")
    command_file = write_command(
        pending,
        {
            "commandName": "set refresh interval",
            "commandArguments": "120",
            "outputDirectory": str(output),
            "targetDevice": target,
            "commandID": "555-0",
        },
    )

    plugin.process_command_dir(pending)

    result = json.loads(
        next(iter(output.glob("555-0-*.json"))).read_text(encoding="utf-8")
    )
    assert result == [{"CommandID": "555-0", "DeviceID": target, "Result": "Completed"}]
    assert load_config(config_path).urls[0].check_interval_minutes == 120
    assert not command_file.is_file()


def test_set_refresh_interval_invalid_arguments(http_server, dirs, tmp_path):
    from servermon.config import load_config

    pending, output = dirs
    config_path = write_toml_config(tmp_path, http_server)
    plugin = ServerMonPlugin(load_config(config_path), config_path=config_path)
    target = device_id(f"{http_server}/ok")
    write_command(
        pending,
        {
            "commandName": "set refresh interval",
            "commandArguments": "banana",
            "outputDirectory": str(output),
            "targetDevice": target,
            "commandID": "556-0",
        },
    )

    plugin.process_command_dir(pending)

    result = json.loads(
        next(iter(output.glob("556-0-*.json"))).read_text(encoding="utf-8")
    )
    assert result[0]["Result"] == "Error"
    assert load_config(config_path).urls[0].check_interval_minutes is None


def test_set_refresh_interval_unknown_target(http_server, dirs, tmp_path):
    from servermon.config import load_config

    pending, output = dirs
    config_path = write_toml_config(tmp_path, http_server)
    plugin = ServerMonPlugin(load_config(config_path), config_path=config_path)
    write_command(
        pending,
        {
            "commandName": "set refresh interval",
            "commandArguments": "120",
            "outputDirectory": str(output),
            "targetDevice": "no-such-device",
            "commandID": "558-0",
        },
    )

    plugin.process_command_dir(pending)

    result = json.loads(
        next(iter(output.glob("558-0-*.json"))).read_text(encoding="utf-8")
    )
    assert result[0]["Result"] == "Error"
    assert load_config(config_path).urls[0].check_interval_minutes is None


def test_set_refresh_interval_rejects_zero(http_server, dirs, tmp_path):
    from servermon.config import load_config

    pending, output = dirs
    config_path = write_toml_config(tmp_path, http_server)
    plugin = ServerMonPlugin(load_config(config_path), config_path=config_path)
    write_command(
        pending,
        {
            "commandName": "set refresh interval",
            "commandArguments": "0",
            "outputDirectory": str(output),
            "targetDevice": device_id(f"{http_server}/ok"),
            "commandID": "559-0",
        },
    )

    plugin.process_command_dir(pending)

    result = json.loads(
        next(iter(output.glob("559-0-*.json"))).read_text(encoding="utf-8")
    )
    assert result[0]["Result"] == "Error"
    assert load_config(config_path).urls[0].check_interval_minutes is None


def test_set_refresh_interval_without_config_path(http_server, dirs):
    pending, output = dirs
    plugin = make_plugin(http_server)  # no config_path
    target = device_id(f"{http_server}/ok")
    write_command(
        pending,
        {
            "commandName": "set refresh interval",
            "commandArguments": "30",
            "outputDirectory": str(output),
            "targetDevice": target,
            "commandID": "557-0",
        },
    )

    plugin.process_command_dir(pending)

    result = json.loads(
        next(iter(output.glob("557-0-*.json"))).read_text(encoding="utf-8")
    )
    assert result[0]["Result"] == "Error"


def write_two_url_config(tmp_path, http_server):
    path = tmp_path / "servermon.toml"
    path.write_text(
        f"""
        # keep me
        [[urls]]
        url = "{http_server}/ok"

        [[urls]]
        url = "{http_server}/other"
        check_interval_minutes = 60
        """,
        encoding="utf-8",
    )
    return path


def test_delete_device_command_is_deferred(http_server, dirs, tmp_path):
    """The delete command reports Completed and marks the device pending, but
    leaves it in the config so the post-action refresh still gets a report.
    """
    pending, output = dirs
    config_path = write_two_url_config(tmp_path, http_server)
    state_file = tmp_path / "servermon-state.json"
    target = device_id(f"{http_server}/other")
    state_file.write_text(
        json.dumps({target: {"last check": "whenever"}}), encoding="utf-8"
    )
    plugin = ServerMonPlugin(
        load_config(config_path), state_file=state_file, config_path=config_path
    )
    command_file = write_command(
        pending,
        {
            "commandName": "delete device",
            "outputDirectory": str(output),
            "targetDevice": target,
            "commandID": "777-0",
        },
    )

    plugin.process_command_dir(pending)

    result = json.loads(
        next(iter(output.glob("777-0-*.json"))).read_text(encoding="utf-8")
    )
    assert result == [{"CommandID": "777-0", "DeviceID": target, "Result": "Completed"}]
    # Still present in config (deferred), and flagged pending in state.
    assert [e.url for e in load_config(config_path).urls] == [
        f"{http_server}/ok",
        f"{http_server}/other",
    ]
    assert json.loads(state_file.read_text(encoding="utf-8"))[target][
        "pending deletion"
    ] is True
    assert not command_file.is_file()


def test_delete_device_finalized_by_post_action_refresh(http_server, dirs, tmp_path):
    """The post-action refresh reports the device one last time (completing
    the action) and then finalizes the removal from config and state.
    """
    pending, output = dirs
    config_path = write_two_url_config(tmp_path, http_server)
    state_file = tmp_path / "servermon-state.json"
    target = device_id(f"{http_server}/other")

    # Step 1: the delete command (defers).
    plugin = ServerMonPlugin(
        load_config(config_path), state_file=state_file, config_path=config_path
    )
    write_command(
        pending,
        {
            "commandName": "delete device",
            "outputDirectory": str(output),
            "targetDevice": target,
            "commandID": "780-0",
        },
    )
    plugin.process_command_dir(pending)

    # Step 2: the Proxy Agent's post-action refresh for that device.
    plugin = ServerMonPlugin(
        load_config(config_path), state_file=state_file, config_path=config_path
    )
    write_command(
        pending,
        {
            "commandName": "refresh",
            "outputDirectory": str(output),
            "targetDevice": target,
        },
        name=f"Refresh-{target}.command",
    )
    plugin.process_command_dir(pending)

    # A final report was written (so the action can complete)...
    assert (output / f"{target}.report").is_file()
    # ...and only now is the device gone from config and state.
    assert [e.url for e in load_config(config_path).urls] == [f"{http_server}/ok"]
    assert "# keep me" in config_path.read_text(encoding="utf-8")
    assert target not in json.loads(state_file.read_text(encoding="utf-8"))


def test_delete_device_last_entry_leaves_valid_config(http_server, dirs, tmp_path):
    pending, output = dirs
    config_path = tmp_path / "servermon.toml"
    config_path.write_text(
        f'[[urls]]\nurl = "{http_server}/ok"\n', encoding="utf-8"
    )
    state_file = tmp_path / "servermon-state.json"
    target = device_id(f"{http_server}/ok")

    plugin = ServerMonPlugin(
        load_config(config_path), state_file=state_file, config_path=config_path
    )
    write_command(
        pending,
        {
            "commandName": "delete device",
            "outputDirectory": str(output),
            "targetDevice": target,
            "commandID": "778-0",
        },
    )
    plugin.process_command_dir(pending)
    assert (
        json.loads(
            next(iter(output.glob("778-0-*.json"))).read_text(encoding="utf-8")
        )[0]["Result"]
        == "Completed"
    )

    # A heartbeat refresh finalizes the removal, leaving urls = [].
    plugin = ServerMonPlugin(
        load_config(config_path), state_file=state_file, config_path=config_path
    )
    write_command(pending, {"CommandName": "refresh", "OutputDirectory": str(output)})
    plugin.process_command_dir(pending)

    assert load_config(config_path).urls == ()


def test_set_refresh_interval_url_missing_from_file(http_server, dirs, tmp_path):
    """The device exists in the in-memory config but its [[urls]] entry is
    gone from the file (edited out from under the plugin): the edit fails.

    and the command reports Error instead of corrupting the file.
    """
    pending, output = dirs
    config_path = tmp_path / "servermon.toml"
    config_path.write_text(
        f'[[urls]]\nurl = "{http_server}/other"\n', encoding="utf-8"
    )
    config = Config(urls=(UrlEntry(url=f"{http_server}/ok"),), timeout_seconds=5)
    plugin = ServerMonPlugin(config, config_path=config_path)
    write_command(
        pending,
        {
            "commandName": "set refresh interval",
            "commandArguments": "120",
            "outputDirectory": str(output),
            "targetDevice": device_id(f"{http_server}/ok"),
            "commandID": "560-0",
        },
    )

    plugin.process_command_dir(pending)

    result = json.loads(
        next(iter(output.glob("560-0-*.json"))).read_text(encoding="utf-8")
    )
    assert result[0]["Result"] == "Error"


def test_finalize_deletion_survives_config_edit_failure(
    http_server, dirs, tmp_path
):
    """If finalizing a deferred deletion cannot remove the [[urls]] entry
    (the file changed under the plugin), the refresh must still complete,.

    and the device must stay pending for a later retry.
    """
    pending, output = dirs
    config_path = tmp_path / "servermon.toml"
    config_path.write_text(
        f'[[urls]]\nurl = "{http_server}/other"\n', encoding="utf-8"
    )
    target = device_id(f"{http_server}/ok")
    state_file = tmp_path / "servermon-state.json"
    state_file.write_text(
        json.dumps({target: {"pending deletion": True}}), encoding="utf-8"
    )
    config = Config(urls=(UrlEntry(url=f"{http_server}/ok"),), timeout_seconds=5)
    plugin = ServerMonPlugin(config, state_file=state_file, config_path=config_path)
    write_command(pending, {"CommandName": "refresh", "OutputDirectory": str(output)})

    plugin.process_command_dir(pending)  # must not raise

    assert (output / f"{target}.report").is_file()  # refresh still answered
    assert json.loads(state_file.read_text(encoding="utf-8"))[target][
        "pending deletion"
    ] is True  # still flagged for a later attempt


def test_delete_device_without_config_path(http_server, dirs):
    pending, output = dirs
    plugin = make_plugin(http_server)  # no config_path to edit
    target = device_id(f"{http_server}/ok")
    write_command(
        pending,
        {
            "commandName": "delete device",
            "outputDirectory": str(output),
            "targetDevice": target,
            "commandID": "781-0",
        },
    )

    plugin.process_command_dir(pending)

    result = json.loads(
        next(iter(output.glob("781-0-*.json"))).read_text(encoding="utf-8")
    )
    assert result[0]["Result"] == "Error"


def test_delete_device_unknown_target(http_server, dirs, tmp_path):
    pending, output = dirs
    config_path = write_two_url_config(tmp_path, http_server)
    plugin = ServerMonPlugin(load_config(config_path), config_path=config_path)
    write_command(
        pending,
        {
            "commandName": "delete device",
            "outputDirectory": str(output),
            "targetDevice": "no-such-device",
            "commandID": "779-0",
        },
    )

    plugin.process_command_dir(pending)

    result = json.loads(
        next(iter(output.glob("779-0-*.json"))).read_text(encoding="utf-8")
    )
    assert result[0]["Result"] == "Error"
    assert len(load_config(config_path).urls) == 2  # untouched


def test_unreachable_url_keeps_stale_contact_time(closed_port_url, dirs, tmp_path):
    """A URL that stops responding keeps its old last-contact time, so the
    console's Last Report Time goes visibly stale.
    """
    pending, output = dirs
    state_file = tmp_path / "servermon-state.json"
    old_contact = "Mon, 13 Jul 2026 07:00:00 -0400"
    state_file.write_text(
        json.dumps({device_id(closed_port_url): {"last contact": old_contact}}),
        encoding="utf-8",
    )
    config = Config(urls=(UrlEntry(url=closed_port_url),), timeout_seconds=5)
    plugin = ServerMonPlugin(config, state_file=state_file)
    write_command(pending, {"CommandName": "refresh", "OutputDirectory": str(output)})

    plugin.process_command_dir(pending)

    report = read_report(output, closed_port_url)
    assert report["http check"]["response code"] == 0
    assert report["last device report time"] == old_contact
    # The report itself is still fresh: the check happened now.
    assert report["last server communication"] == report["last check time"]


def test_invalid_command_file_is_skipped(http_server, dirs):
    pending, output = dirs
    plugin = make_plugin(http_server)
    (pending / "junk.json").write_text("{not json", encoding="utf-8")
    write_command(
        pending,
        {"CommandName": "refresh", "OutputDirectory": str(output)},
        name="0002.json",
    )

    plugin.process_command_dir(pending)  # must not raise

    assert len(list(output.glob("*.report"))) == 2


def test_missing_command_dir_raises(http_server, tmp_path):
    plugin = make_plugin(http_server)
    with pytest.raises(FileNotFoundError):
        plugin.process_command_dir(tmp_path / "nope")


def test_empty_entry_lists_are_noops(http_server):
    plugin = make_plugin(http_server)
    assert plugin.check_and_report([]) == []
    assert plugin.run_checks([]) == []


def test_command_file_already_removed_is_fine(http_server, dirs):
    # Some Proxy Agent versions clean up command files themselves; removing
    # an already-gone file must not raise.
    from servermon.command import Command
    from servermon.plugin import _remove_command_file

    pending, _ = dirs
    command = Command(pending / "gone.json", {"commandname": "refresh"})
    _remove_command_file(command)  # must not raise


def test_unremovable_command_file_is_logged_not_fatal(http_server, dirs, caplog):
    import logging

    from servermon.command import Command
    from servermon.plugin import _remove_command_file

    pending, _ = dirs
    blocker = pending / "cmd.json"
    blocker.mkdir()  # os.remove on a directory raises OSError
    command = Command(blocker, {"commandname": "refresh"})
    with caplog.at_level(logging.WARNING, logger="servermon.plugin"):
        _remove_command_file(command)  # must not raise
    assert any("could not remove" in r.message for r in caplog.records)


def test_report_write_failure_leaves_command_for_retry(http_server, dirs, tmp_path):
    """If writing a report fails, the refresh must raise *before* consuming
    the command file, so the next invocation retries it.
    """
    pending, _ = dirs
    plugin = make_plugin(http_server)
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")
    command_file = write_command(
        pending,
        # The output directory has a regular file as a path component, so
        # creating it (and writing any report) must fail.
        {"CommandName": "refresh", "OutputDirectory": str(blocker / "reports")},
    )

    with pytest.raises(OSError):
        plugin.process_command_dir(pending)

    assert command_file.is_file()  # left in place for the retry


def test_report_contains_connect_time(http_server, dirs):
    pending, output = dirs
    plugin = make_plugin(http_server)
    write_command(pending, {"CommandName": "refresh", "OutputDirectory": str(output)})

    plugin.process_command_dir(pending)

    report = read_report(output, f"{http_server}/ok")
    assert isinstance(report["http check"]["connect time ms"], int)


class TestNetworkHopsGating:
    def _plugin(self, http_server, tmp_path, opt_in=True):
        config = Config(
            urls=(
                UrlEntry(url=f"{http_server}/ok", measure_network_hops=opt_in),
            ),
            timeout_seconds=5,
        )
        return ServerMonPlugin(config, state_file=tmp_path / "state.json")

    def test_measured_on_first_check_then_cached(
        self, http_server, dirs, tmp_path, monkeypatch
    ):
        pending, output = dirs
        calls = []
        monkeypatch.setattr(
            "servermon.plugin.measure_network_hops",
            lambda url, timeout: calls.append(url) or 7,
        )
        plugin = self._plugin(http_server, tmp_path)

        # First refresh: never measured before -> measured now.
        write_command(
            pending, {"CommandName": "refresh", "OutputDirectory": str(output)}
        )
        plugin.process_command_dir(pending)
        assert len(calls) == 1
        report = read_report(output, f"{http_server}/ok")
        assert report["http check"]["network hops"] == 7

        # Second refresh right away: the regular check runs again, but the
        # hops measurement waits HOPS_EVERY_N_CHECKS intervals - the cached
        # value is still re-sent in the report.
        write_command(
            pending, {"CommandName": "refresh", "OutputDirectory": str(output)}
        )
        plugin.process_command_dir(pending)
        assert len(calls) == 1  # not measured again
        report = read_report(output, f"{http_server}/ok")
        assert report["http check"]["network hops"] == 7

    def test_not_measured_without_opt_in(
        self, http_server, dirs, tmp_path, monkeypatch
    ):
        pending, output = dirs
        calls = []
        monkeypatch.setattr(
            "servermon.plugin.measure_network_hops",
            lambda url, timeout: calls.append(url) or 7,
        )
        plugin = self._plugin(http_server, tmp_path, opt_in=False)
        write_command(
            pending, {"CommandName": "refresh", "OutputDirectory": str(output)}
        )
        plugin.process_command_dir(pending)
        assert calls == []
        assert "network hops" not in read_report(output, f"{http_server}/ok")[
            "http check"
        ]

    def test_action_driven_refresh_never_measures(
        self, http_server, dirs, tmp_path, monkeypatch
    ):
        pending, output = dirs
        calls = []
        monkeypatch.setattr(
            "servermon.plugin.measure_network_hops",
            lambda url, timeout: calls.append(url) or 7,
        )
        plugin = self._plugin(http_server, tmp_path)
        # A "check now" action: due for a hops measurement (never measured),
        # but action refreshes are the regular check only.
        write_command(
            pending,
            {
                "CommandName": "refresh",
                "OutputDirectory": str(output),
                "TargetDevice": device_id(f"{http_server}/ok"),
                "CommandID": "12345-0",
            },
        )
        plugin.process_command_dir(pending)
        assert calls == []

    def test_garbage_last_hops_check_measures_again(
        self, http_server, dirs, tmp_path, monkeypatch
    ):
        # An unparsable "last hops check" must fail open: measure now.
        pending, output = dirs
        calls = []
        monkeypatch.setattr(
            "servermon.plugin.measure_network_hops",
            lambda url, timeout: calls.append(url) or 7,
        )
        state_file = tmp_path / "state.json"
        state_file.write_text(
            json.dumps(
                {device_id(f"{http_server}/ok"): {"last hops check": "not a date"}}
            ),
            encoding="utf-8",
        )
        plugin = self._plugin(http_server, tmp_path)
        write_command(
            pending, {"CommandName": "refresh", "OutputDirectory": str(output)}
        )
        plugin.process_command_dir(pending)
        assert len(calls) == 1

    def test_failed_measurement_waits_full_interval(
        self, http_server, dirs, tmp_path, monkeypatch
    ):
        pending, output = dirs
        calls = []
        monkeypatch.setattr(
            "servermon.plugin.measure_network_hops",
            lambda url, timeout: calls.append(url) and None,
        )
        plugin = self._plugin(http_server, tmp_path)
        for _ in range(2):
            write_command(
                pending, {"CommandName": "refresh", "OutputDirectory": str(output)}
            )
            plugin.process_command_dir(pending)
        # The failed attempt still counts as the last attempt: no retry on
        # the very next check, and no stale value in the report.
        assert len(calls) == 1
        assert "network hops" not in read_report(output, f"{http_server}/ok")[
            "http check"
        ]
