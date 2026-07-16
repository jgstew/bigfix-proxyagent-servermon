"""End-to-end tests: command files in, device reports / command results out."""

import json

import pytest

from servermon.config import Config, UrlEntry
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
    PendingCommands (observed on a real deployment)."""
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

    result = json.loads((output / "314159.json").read_text(encoding="utf-8"))
    assert result == [{"CommandID": "314159", "DeviceID": target, "Result": "Error"}]
    # Non-refresh command files are consumed by the plugin (like trask).
    assert not command_file.is_file()


def test_invalid_command_file_is_skipped(http_server, dirs):
    pending, output = dirs
    plugin = make_plugin(http_server)
    (pending / "junk.json").write_text("{not json", encoding="utf-8")
    write_command(
        pending, {"CommandName": "refresh", "OutputDirectory": str(output)}, name="0002.json"
    )

    plugin.process_command_dir(pending)  # must not raise

    assert len(list(output.glob("*.report"))) == 2


def test_missing_command_dir_raises(http_server, tmp_path):
    plugin = make_plugin(http_server)
    with pytest.raises(FileNotFoundError):
        plugin.process_command_dir(tmp_path / "nope")
