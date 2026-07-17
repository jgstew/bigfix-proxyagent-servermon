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
    assert ok["http response code"] == 200
    assert ok["check success"] is True
    assert ok["match found"] is True
    assert ok["data source"] == "servermon"
    assert ok["computer name"].endswith("/ok")
    assert not ok["computer name"].startswith("http")

    missing = read_report(output, f"{http_server}/does-not-exist")
    assert missing["http response code"] == 404
    assert missing["check success"] is False
    assert missing["http check result"].startswith("FAILED:")
    assert "match found" not in missing

    # Last-error keys appear only in failed reports, so BigFix retains the
    # previous error across later successful reports.
    assert missing["http check last error"] == missing["http check result"]
    assert missing["http check last error time"] == missing["last check time"]
    assert "http check last error" not in ok

    # Deleting the command file acknowledges the refresh was processed.
    assert not command_file.is_file()


def test_partial_refresh_targets_one_device(http_server, dirs):
    pending, output = dirs
    plugin = make_plugin(http_server)
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
    assert first["http response code"] == 500
    assert first["http check last error"] == first["http check result"]

    # Second run in a new plugin instance: /flaky has recovered, but the
    # previous error (and its time) must still be present in the report.
    plugin = ServerMonPlugin(config, state_file=state_file)
    write_command(pending, {"CommandName": "refresh", "OutputDirectory": str(output)})
    plugin.process_command_dir(pending)
    second = read_report(output, f"{http_server}/flaky")
    assert second["http response code"] == 200
    assert second["check success"] is True
    assert second["http check last error"] == first["http check last error"]
    assert second["http check last error time"] == first["http check last error time"]
    # Both runs got HTTP responses (500 then 200), so the contact time
    # advances to the latest check.
    assert second["last device report time"] == second["last check time"]


def test_partial_refresh_falls_back_to_target_hint(http_server, dirs):
    """If the device id does not match, the target hint (the URL, per
    TargetHintRelevance in settings.json) still finds the entry.
    """
    pending, output = dirs
    plugin = make_plugin(http_server)
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
        "http response code": 299,
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
    assert report["http response code"] == 299  # cached, not re-checked
    assert report["last check time"] == "Mon, 13 Jul 2026 07:00:00 -0400"
    # ...but the report itself is fresh so the Proxy Agent treats it as new.
    assert report["last server communication"] != "Mon, 13 Jul 2026 07:00:00 -0400"
    assert not command_file.is_file()


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

    assert read_report(output, url)["http response code"] == 200


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


def test_delete_device_command(http_server, dirs, tmp_path):
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
    remaining = load_config(config_path)
    assert [e.url for e in remaining.urls] == [f"{http_server}/ok"]
    assert "# keep me" in config_path.read_text(encoding="utf-8")
    # Device history is gone from the state file too.
    assert target not in json.loads(state_file.read_text(encoding="utf-8"))
    assert not command_file.is_file()


def test_delete_device_last_entry_leaves_valid_config(http_server, dirs, tmp_path):
    pending, output = dirs
    config_path = tmp_path / "servermon.toml"
    config_path.write_text(
        f'[[urls]]\nurl = "{http_server}/ok"\n', encoding="utf-8"
    )
    target = device_id(f"{http_server}/ok")
    plugin = ServerMonPlugin(load_config(config_path), config_path=config_path)
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

    result = json.loads(
        next(iter(output.glob("778-0-*.json"))).read_text(encoding="utf-8")
    )
    assert result[0]["Result"] == "Completed"
    assert load_config(config_path).urls == ()


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
    assert report["http response code"] == 0
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
