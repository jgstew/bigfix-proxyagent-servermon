import json

import pytest

from servermon.command import Command, CommandError


def write_command(tmp_path, payload, name="0001.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_refresh_command(tmp_path):
    path = write_command(
        tmp_path, {"CommandName": "Refresh", "OutputDirectory": "C:\\Reports"}
    )
    command = Command.load(path)
    assert command.is_refresh
    assert str(command.output_directory) == "C:\\Reports"
    assert command.target_device == ""


def test_keys_are_case_insensitive(tmp_path):
    path = write_command(
        tmp_path,
        {
            "commandname": "locate",
            "OUTPUTDIRECTORY": "/tmp/out",
            "targetDevice": "abc123",
            "CommandID": "42",
        },
    )
    command = Command.load(path)
    assert command.name == "locate"
    assert command.target_device == "abc123"
    assert command.command_id == "42"


def test_non_refresh_requires_target_and_id(tmp_path):
    path = write_command(
        tmp_path, {"CommandName": "locate", "OutputDirectory": "/tmp/out"}
    )
    with pytest.raises(CommandError, match="targetdevice, commandid"):
        Command.load(path)


def test_missing_output_directory(tmp_path):
    path = write_command(tmp_path, {"CommandName": "refresh"})
    with pytest.raises(CommandError, match="outputdirectory"):
        Command.load(path)


def test_invalid_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(CommandError, match="cannot read"):
        Command.load(path)


def test_non_object_json(tmp_path):
    path = write_command(tmp_path, ["not", "an", "object"])
    with pytest.raises(CommandError, match="JSON object"):
        Command.load(path)
