"""Process Proxy Agent commands: run the URL checks and write device reports."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .checker import CheckResult, check_url
from .command import Command, CommandError
from .config import Config, UrlEntry
from .device import build_report, device_id

log = logging.getLogger(__name__)

MAX_PARALLEL_CHECKS = 8


class ServerMonPlugin:
    def __init__(self, config: Config):
        self.config = config

    def process_command_dir(self, command_dir: Path | str) -> None:
        command_dir = Path(command_dir)
        if not command_dir.is_dir():
            raise FileNotFoundError(f"command directory does not exist: {command_dir}")

        for path in sorted(command_dir.iterdir()):
            if not path.is_file():
                continue
            try:
                command = Command.load(path)
            except CommandError as error:
                log.warning("skipping %s: %s", path.name, error)
                continue
            self.process_command(command)

    def process_command(self, command: Command) -> None:
        if command.is_refresh:
            self._process_refresh(command)
        else:
            self._process_unsupported(command)

    def _process_refresh(self, command: Command) -> None:
        entries = list(self.config.urls)
        if command.target_device:  # partial refresh of a single device
            entries = [e for e in entries if device_id(e.url) == command.target_device]
            if not entries:
                # Device was removed from the config: report nothing so it
                # expires (DeviceReportExpirationIntervalHours), and consume
                # the command so it does not linger in PendingCommands.
                log.warning(
                    "refresh for unknown device %r: no matching URL in config",
                    command.target_device,
                )
                _remove_command_file(command)
                return

        log.info("checking %d URL(s)", len(entries))
        for entry, result in self.run_checks(entries):
            report = build_report(entry, result, sequence=command.device_report_sequence)
            report_path = command.output_directory / f"{report['device id']}.report"
            _write_json(report_path, report)
            log.info("%s: %s", report["computer name"], result.detail)
        # Deleting the command file acknowledges the refresh is done; if
        # writing a report failed we raise before reaching this, leaving the
        # command in place for the next invocation to retry.
        _remove_command_file(command)

    def _process_unsupported(self, command: Command) -> None:
        """servermon devices are monitor-only, so any action command fails."""
        result = [
            {
                "CommandID": command.command_id,
                "DeviceID": command.target_device,
                "Result": "Error",
            }
        ]
        _write_json(command.output_directory / f"{command.command_id}.json", result)
        log.warning(
            "unsupported command %r for device %s: reported Error",
            command.name,
            command.target_device,
        )
        _remove_command_file(command)

    def run_checks(self, entries: list[UrlEntry]) -> list[tuple[UrlEntry, CheckResult]]:
        """Check every entry (in parallel) and pair each with its result."""
        if not entries:
            return []
        workers = min(MAX_PARALLEL_CHECKS, len(entries))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(self._check_one, entries))
        return list(zip(entries, results))

    def _check_one(self, entry: UrlEntry) -> CheckResult:
        return check_url(
            entry,
            timeout=self.config.timeout_for(entry),
            user_agent=self.config.user_agent,
        )


def _remove_command_file(command: Command) -> None:
    try:
        os.remove(command.location)
    except FileNotFoundError:
        pass  # some Proxy Agent versions clean up command files themselves
    except OSError as error:
        log.warning("could not remove command file %s: %s", command.location, error)


def _write_json(path: Path, payload: Any) -> None:
    """Write atomically so the Proxy Agent never reads a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
