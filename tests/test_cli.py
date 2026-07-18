import json

import pytest

from servermon import cli
from servermon.cli import main


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


def test_check_json_outputs_device_reports(config_file, capsys):
    main(["--config", str(config_file), "--check", "--json"])
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


def test_command_dir_required_without_check(config_file):
    with pytest.raises(SystemExit):
        main(["--config", str(config_file)])
