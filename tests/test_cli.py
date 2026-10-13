import json
from pathlib import Path

import pytest

from servermon import cli
from servermon.cli import main


@pytest.fixture(autouse=True)
def isolated_runtime_files(tmp_path, monkeypatch):
    """Keep main() from writing the repo's real state file and log file
    (their defaults live next to the plugin, not in a temp dir).
    """
    monkeypatch.setattr(cli, "DEFAULT_STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(cli, "DEFAULT_LOG_FILE", tmp_path / "servermon.log")


@pytest.fixture
def config_file(tmp_path, http_server):
    path = tmp_path / "servermon.toml"
    path.write_text(
        f"""
        [settings]
        timeout_seconds = 5

        [[urls]]
        url = "{http_server}/ok"
        match = "hello from"

        [[urls]]
        url = "{http_server}/does-not-exist"
        """,
        encoding="utf-8",
    )
    return path


def test_validate_ok(config_file, capsys):
    assert main(["--config", str(config_file), "--validate"]) == 0
    assert "2 URL(s)" in capsys.readouterr().out


def test_validate_bad_config(tmp_path, capsys):
    path = tmp_path / "bad.toml"
    path.write_text("[settings]\n", encoding="utf-8")
    assert main(["--config", str(path), "--validate"]) == 1
    assert "INVALID" in capsys.readouterr().out


def test_check_reports_each_url(config_file, capsys):
    # Exit code 1 because /does-not-exist returns 404.
    assert main(["--config", str(config_file), "--check"]) == 1
    out = capsys.readouterr().out
    assert "OK: HTTP 200" in out
    assert "FAILED: HTTP 404" in out


def test_check_all_ok_exits_zero(tmp_path, http_server, capsys):
    path = tmp_path / "servermon.toml"
    path.write_text(f'[[urls]]\nurl = "{http_server}/ok"\n', encoding="utf-8")
    assert main(["--config", str(path), "--check"]) == 0
    assert "OK: HTTP 200" in capsys.readouterr().out


def test_check_json_outputs_device_reports(config_file, capsys):
    # Exit code 1: JSON output does not change the failure signal.
    assert main(["--config", str(config_file), "--check", "--json"]) == 1
    reports = json.loads(capsys.readouterr().out)
    assert len(reports) == 2
    assert {report["http check"]["response code"] for report in reports} == {200, 404}
    assert all("last check time" in report for report in reports)


def test_command_dir_mode(config_file, tmp_path):
    pending = tmp_path / "PendingCommands"
    output = tmp_path / "DeviceReports"
    pending.mkdir()
    output.mkdir()
    (pending / "0001.json").write_text(
        json.dumps({"CommandName": "refresh", "OutputDirectory": str(output)}),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--config",
            str(config_file),
            "--configOptions",
            "",
            "--commandDir",
            str(pending),
        ]
    )

    assert exit_code == 0
    assert len(list(output.glob("*.report"))) == 2


def test_missing_config_falls_back_to_default(
    config_file, tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(cli, "DEFAULT_CONFIG", config_file.resolve())
    missing = tmp_path / "does-not-exist.toml"

    assert main(["--config", str(missing), "--validate"]) == 0
    # The default config's absolute path is what got used and reported.
    assert str(config_file.resolve()) in capsys.readouterr().out


def test_config_not_found_anywhere(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "DEFAULT_CONFIG", (tmp_path / "default.toml").resolve())

    assert main(["--config", str(tmp_path / "missing.toml"), "--validate"]) == 1
    out = capsys.readouterr().out
    assert "INVALID" in out
    assert "also tried default" in out


def test_log_file_directory_auto_created(config_file, tmp_path):
    log_file = tmp_path / "nested" / "Logs" / "servermon.log"
    assert (
        main(["--config", str(config_file), "--validate", "--log-file", str(log_file)])
        == 0
    )
    assert log_file.exists()


def write_refresh(pending, output):
    pending.mkdir(parents=True)
    output.mkdir(parents=True)
    (pending / "0001.json").write_text(
        json.dumps({"CommandName": "refresh", "OutputDirectory": str(output)}),
        encoding="utf-8",
    )


def test_default_command_dir_is_pending_commands_in_plugin_root():
    repo_root = Path(__file__).resolve().parent.parent
    assert cli.PLUGIN_ROOT == repo_root
    assert cli.DEFAULT_COMMAND_DIR == repo_root / "PendingCommands"


def test_command_dir_defaults_to_pending_commands(config_file, tmp_path, monkeypatch):
    # Neither --commandDir nor settings.command_dir: use the default folder.
    pending = tmp_path / "PendingCommands"
    output = tmp_path / "DeviceReports"
    write_refresh(pending, output)
    monkeypatch.setattr(cli, "DEFAULT_COMMAND_DIR", pending)

    assert main(["--config", str(config_file)]) == 0
    assert len(list(output.glob("*.report"))) == 2
    assert not list(pending.iterdir())  # command consumed


def config_with_command_dir(tmp_path, http_server, command_dir):
    path = tmp_path / "servermon.toml"
    path.write_text(
        f'[settings]\ncommand_dir = "{command_dir}"\n\n'
        f'[[urls]]\nurl = "{http_server}/ok"\n',
        encoding="utf-8",
    )
    return path


def test_command_dir_from_config(tmp_path, http_server, monkeypatch):
    pending = tmp_path / "FromConfig"
    output = tmp_path / "DeviceReports"
    write_refresh(pending, output)
    monkeypatch.setattr(cli, "DEFAULT_COMMAND_DIR", tmp_path / "unused-default")
    config = config_with_command_dir(tmp_path, http_server, pending.as_posix())

    assert main(["--config", str(config)]) == 0
    assert len(list(output.glob("*.report"))) == 1


def test_relative_config_command_dir_resolves_against_plugin_root(
    tmp_path, http_server, monkeypatch
):
    # Relative to the plugin folder, never the (unpredictable) working dir.
    root = tmp_path / "plugin-root"
    pending = root / "Relative" / "Commands"
    output = tmp_path / "DeviceReports"
    write_refresh(pending, output)
    monkeypatch.setattr(cli, "PLUGIN_ROOT", root)
    config = config_with_command_dir(tmp_path, http_server, "Relative/Commands")

    assert main(["--config", str(config)]) == 0
    assert len(list(output.glob("*.report"))) == 1


def test_command_line_beats_config_command_dir(tmp_path, http_server):
    pending = tmp_path / "FromCli"
    output = tmp_path / "DeviceReports"
    write_refresh(pending, output)
    config = config_with_command_dir(
        tmp_path, http_server, (tmp_path / "missing").as_posix()
    )

    assert main(["--config", str(config), "--commandDir", str(pending)]) == 0
    assert len(list(output.glob("*.report"))) == 1


def test_missing_command_dir_exits_one(config_file, tmp_path, monkeypatch, capsys):
    # A missing command folder is logged and reported via the exit code,
    # not a traceback.
    missing = tmp_path / "PendingCommands"
    monkeypatch.setattr(cli, "DEFAULT_COMMAND_DIR", missing)

    assert main(["--config", str(config_file)]) == 1
    # setup_logging replaces caplog's handler, so read the stderr log instead.
    err = capsys.readouterr().err
    assert "command directory does not exist" in err
    assert str(missing) in err


def test_unknown_arguments_are_ignored(config_file):
    # A future Proxy Agent may pass flags this version does not know about;
    # they must be ignored, not fatal.
    assert main(["--config", str(config_file), "--validate", "--futureFlag", "x"]) == 0


def test_bad_config_without_validate_exits_one(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "DEFAULT_CONFIG", (tmp_path / "default.toml").resolve())
    assert main(["--config", str(tmp_path / "missing.toml"), "--check"]) == 1


def test_missing_default_config(tmp_path, monkeypatch, capsys):
    # Requesting exactly the (missing) default config: no fallback to try.
    missing_default = (tmp_path / "default.toml").resolve()
    monkeypatch.setattr(cli, "DEFAULT_CONFIG", missing_default)
    assert main(["--config", str(missing_default), "--validate"]) == 1
    out = capsys.readouterr().out
    assert "INVALID" in out
    assert "also tried default" not in out


def test_unwritable_log_file_is_not_fatal(config_file, tmp_path):
    # A path component that is a regular file makes the log dir creation
    # fail; the run must continue with stderr-only logging.
    blocker = tmp_path / "blocker"
    blocker.write_text("", encoding="utf-8")
    log_file = blocker / "Logs" / "servermon.log"
    assert (
        main(["--config", str(config_file), "--validate", "--log-file", str(log_file)])
        == 0
    )
    assert not log_file.exists()
