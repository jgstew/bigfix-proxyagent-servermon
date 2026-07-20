"""Process Proxy Agent commands: run the URL checks and write device reports."""

from __future__ import annotations

import email.utils
import itertools
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__
from .checker import CheckResult, check_url, measure_network_hops
from .command import Command, CommandError
from .config import (Config, ConfigError, UrlEntry, heartbeat_minutes,
                     remove_url_entry, set_url_check_interval)
from .device import build_report, device_id, device_name
from .state import DeviceState
from .util import write_json_atomic

log = logging.getLogger(__name__)

MAX_PARALLEL_CHECKS = 8

# An opted-in URL (measure_network_hops) gets a hop measurement alongside 1 in
# every N regular checks; the cadence is N x the URL's effective check
# interval, deliberately not separately configurable.
HOPS_EVERY_N_CHECKS = 6

SET_REFRESH_INTERVAL = "set refresh interval"
DELETE_DEVICE = "delete device"

# Command result files use the spec-suggested "<commandID>-<PID>-<seq>.json"
# naming so concurrently running plugin instances can never collide.
_result_seq = itertools.count()


class ServerMonPlugin:
    def __init__(
        self,
        config: Config,
        state_file: Path | None = None,
        config_path: Path | None = None,
    ) -> None:
        self.config = config
        self.config_path = config_path  # needed by "set refresh interval"
        self.state = DeviceState(state_file)
        # Default check cadence, reported via the "refresh interval"
        # inspector for URLs without their own check_interval_minutes.
        self.default_interval = heartbeat_minutes()

    def process_command_dir(self, command_dir: Path | str) -> None:
        command_dir = Path(command_dir)
        if not command_dir.is_dir():
            raise FileNotFoundError(f"command directory does not exist: {command_dir}")

        for path in sorted(command_dir.iterdir()):
            if not path.is_file():
                continue
            if path.suffix.lower() not in (".command", ".json"):
                # Stray files (editor temp files, Thumbs.db, ...) are not
                # commands; skip quietly rather than warning every run.
                log.debug("ignoring non-command file %s", path.name)
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
        elif command.name == SET_REFRESH_INTERVAL:
            self._process_set_refresh_interval(command)
        elif command.name == DELETE_DEVICE:
            self._process_delete_device(command)
        else:
            self._process_unsupported(command)

    def _process_refresh(self, command: Command) -> None:
        if command.target_device:  # partial refresh of a single device
            entries = self._match_target(command.target_device, command.target_hint)
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
        else:
            entries = list(self.config.urls)

        new_entries: list[UrlEntry] = []
        # Device ids that received a report this invocation (via a real check
        # or a cached replay); used to finalize deferred deletions.
        reported_ids: set[str] = set()
        if not command.command_id:
            # Honor per-URL check_interval_minutes for Proxy Agent driven
            # refreshes; action-driven ones (with a commandID) always check.
            # A skipped URL still gets its cached report re-submitted: the
            # Proxy Agent must always receive a report for a refresh (a
            # pending action waits on it), only the HTTP check is skipped.
            due = []
            for entry in entries:
                if self._is_due(entry) or not self._replay_cached_report(
                    entry, command
                ):
                    due.append(entry)
                else:
                    reported_ids.add(device_id(entry.url))  # replayed from cache

            # URLs newly added to servermon.toml have never been checked, and
            # a Proxy Agent that only sends per-device refreshes would never
            # ask for them. The plugin interface explicitly allows submitting
            # unrequested device reports, so pick them up on any refresh.
            covered = {device_id(e.url) for e in entries}
            new_entries = [
                e
                for e in self.config.urls
                if device_id(e.url) not in covered
                and self.state.last_check(device_id(e.url)) is None
            ]
            if new_entries:
                log.info(
                    "reporting %d newly configured URL(s)", len(new_entries)
                )

            entries = due
            if not entries and not new_entries:
                self._finalize_pending_deletions(reported_ids)
                _remove_command_file(command)
                return

        # Action-driven refreshes ("check now") are the regular check only:
        # they never pull a network hops measurement forward.
        rows = self.check_and_report(
            entries,
            sequence=command.device_report_sequence,
            allow_hops=not command.command_id,
        )
        if new_entries:
            # Checked separately: the command's deviceReportSequence belongs
            # to the targeted device only, so it is not echoed here.
            rows += self.check_and_report(new_entries)
        reported_ids.update(report["device id"] for _, _, report in rows)

        if command.command_id:
            # An actionscript-driven refresh (a "check now" action) carries a
            # commandID and its outputDirectory is the action-results dir, so
            # it expects a command result rather than device reports. Report
            # per-device Completed/Failed mirroring the check outcome; the
            # Proxy Agent sends a normal refresh after the action completes,
            # which produces the fresh device reports.
            results = [
                {
                    "CommandID": command.command_id,
                    "DeviceID": report["device id"],
                    "Result": "Completed" if result.success else "Failed",
                }
                for _, result, report in rows
            ]
            _write_command_result(command, results)
            for _, result, report in rows:
                log.info("check now %s: %s", report["computer name"], result.detail)
        else:
            for _, result, report in rows:
                report_path = command.output_directory / f"{report['device id']}.report"
                write_json_atomic(report_path, report)
                log.info("%s: %s", report["computer name"], result.detail)
            # Now that the post-action refresh has been answered with a
            # report, complete any deferred "delete device" removals.
            self._finalize_pending_deletions(reported_ids)
        # Deleting the command file acknowledges the refresh is done; if
        # writing a report failed we raise before reaching this, leaving the
        # command in place for the next invocation to retry.
        _remove_command_file(command)

    def _process_set_refresh_interval(self, command: Command) -> None:
        """Actionscript "set refresh interval <minutes>": persist a per-URL
        check_interval_minutes into the servermon.toml config file.
        """
        outcome = "Error"
        entries = self._match_target(command.target_device, command.target_hint)
        minutes = _parse_positive_int(str(command.get("commandarguments")))
        if not entries:
            log.warning(
                "%s: no URL in config for device %r",
                SET_REFRESH_INTERVAL,
                command.target_device,
            )
        elif minutes is None:
            log.warning(
                "%s: arguments %r are not a positive integer of minutes",
                SET_REFRESH_INTERVAL,
                command.get("commandarguments"),
            )
        elif self.config_path is None:
            log.warning("%s: no config file path to update", SET_REFRESH_INTERVAL)
        else:
            try:
                set_url_check_interval(self.config_path, entries[0].url, minutes)
                # Also update the in-memory config so later commands in this
                # same invocation see (and report) the new interval.
                updated = replace(entries[0], check_interval_minutes=minutes)
                self.config = replace(
                    self.config,
                    urls=tuple(
                        updated if e is entries[0] else e for e in self.config.urls
                    ),
                )
                outcome = "Completed"
                log.info(
                    "%s: set check_interval_minutes = %d for %s",
                    SET_REFRESH_INTERVAL,
                    minutes,
                    entries[0].url,
                )
            except ConfigError as error:
                log.warning("%s failed: %s", SET_REFRESH_INTERVAL, error)

        _write_command_result(
            command,
            [
                {
                    "CommandID": command.command_id,
                    "DeviceID": command.target_device,
                    "Result": outcome,
                }
            ],
        )
        _remove_command_file(command)

    def _process_delete_device(self, command: Command) -> None:
        """Actionscript "delete device": stop monitoring the targeted URL.

        The removal is *deferred*: the device is flagged for deletion but
        left in the config so the Proxy Agent's post-action refresh still
        gets a device report (without it the action would hang in "running").
        The next refresh reports the device one last time and then finalizes
        the removal - dropping the [[urls]] entry from servermon.toml and the
        device's history from the state file. With no further reports it then
        expires from BigFix after DeviceReportExpirationIntervalHours (or
        delete the computer from the console for immediate removal).
        """
        outcome = "Error"
        entries = self._match_target(command.target_device, command.target_hint)
        if not entries:
            log.warning(
                "%s: no URL in config for device %r",
                DELETE_DEVICE,
                command.target_device,
            )
        elif self.config_path is None:
            log.warning("%s: no config file path to update", DELETE_DEVICE)
        else:
            self.state.mark_pending_deletion(device_id(entries[0].url))
            self.state.save()
            outcome = "Completed"
            log.info(
                "%s: marked %s for deletion (finalized after next report)",
                DELETE_DEVICE,
                entries[0].url,
            )

        _write_command_result(
            command,
            [
                {
                    "CommandID": command.command_id,
                    "DeviceID": command.target_device,
                    "Result": outcome,
                }
            ],
        )
        _remove_command_file(command)

    def _finalize_pending_deletions(self, reported_ids: set[str]) -> None:
        """Complete deferred "delete device" removals for devices that were
        reported this invocation (their post-action refresh is now answered).
        """
        if self.config_path is None:
            return
        for entry in list(self.config.urls):
            did = device_id(entry.url)
            if did not in reported_ids or not self.state.is_pending_deletion(did):
                continue
            try:
                remove_url_entry(self.config_path, entry.url)
            except ConfigError as error:
                log.warning(
                    "%s: could not remove %s from config: %s",
                    DELETE_DEVICE,
                    entry.url,
                    error,
                )
                continue
            self.state.forget(did)
            self.config = replace(
                self.config, urls=tuple(e for e in self.config.urls if e is not entry)
            )
            log.info("%s: finalized removal of %s", DELETE_DEVICE, entry.url)
        self.state.save()

    def _replay_cached_report(self, entry: UrlEntry, command: Command) -> bool:
        """Re-submit the cached report for a URL within its check interval.

        Report freshness advances ("last server communication" is now) but
        all check data keeps its cached values. Returns False when there is
        no cached report yet, in which case the caller checks for real.
        """
        report = self.state.cached_report(device_id(entry.url))
        if report is None:
            return False
        report["last server communication"] = email.utils.format_datetime(
            datetime.now().astimezone()
        )
        # Keep the reported cadence current even when the cached report
        # predates a "set refresh interval" change.
        report["refresh interval"] = (
            entry.check_interval_minutes or self.default_interval
        )
        if command.device_report_sequence is not None:
            report["device report sequence"] = command.device_report_sequence
            report["deviceReportSequence"] = command.device_report_sequence
        report_path = command.output_directory / f"{report['device id']}.report"
        write_json_atomic(report_path, report)
        log.info(
            "%s: re-submitted cached report (within check interval)",
            report["computer name"],
        )
        return True

    def _match_target(self, target_device: str, target_hint: str) -> list[UrlEntry]:
        """Config entries for a targeted device: by device id, falling back
        to the target hint (the device's url, per TargetHintRelevance in.

        settings.json) so entries survive an id-scheme change.
        """
        entries = [e for e in self.config.urls if device_id(e.url) == target_device]
        if not entries and target_hint:
            entries = [
                e
                for e in self.config.urls
                if target_hint in (e.url, device_name(e.url))
            ]
        return entries

    def _hops_due(self, entry: UrlEntry) -> bool:
        """Whether this check should also measure network hops: the URL has
        opted in and HOPS_EVERY_N_CHECKS effective check intervals have.

        passed since the last measurement attempt.
        """
        if not entry.measure_network_hops:
            return False
        last = self.state.last_hops_check(device_id(entry.url))
        if last is None:
            return True
        try:
            last_dt = email.utils.parsedate_to_datetime(last)
        except (TypeError, ValueError):
            return True
        elapsed_minutes = (datetime.now().astimezone() - last_dt).total_seconds() / 60
        interval = entry.check_interval_minutes or self.default_interval
        # Same 10% slack as _is_due, so heartbeat jitter cannot push every
        # measurement one full check later.
        return elapsed_minutes >= interval * HOPS_EVERY_N_CHECKS * 0.9

    def _version_bumped_since_check(self, entry: UrlEntry) -> bool:
        """Whether servermon's major or minor version has increased since
        this URL was last checked.

        A minor/major bump can change the report shape or check semantics, so
        the URL is re-checked immediately rather than replaying a cached
        report until its interval elapses. Patch bumps do not trigger this.
        With no recorded version (state predating this feature) there is no
        baseline, so it returns False and the normal interval applies.
        """
        previous = _major_minor(self.state.last_check_version(device_id(entry.url)))
        current = _major_minor(__version__)
        if previous is None or current is None:
            return False
        return current > previous

    def _is_due(self, entry: UrlEntry) -> bool:
        interval = entry.check_interval_minutes
        if interval is None:
            return True
        last_check = self.state.last_check(device_id(entry.url))
        if last_check is None:
            return True
        if self._version_bumped_since_check(entry):
            log.info(
                "checking %s: servermon upgraded since its last check",
                device_name(entry.url),
            )
            return True
        try:
            last_dt = email.utils.parsedate_to_datetime(last_check)
        except (TypeError, ValueError):
            return True
        elapsed_minutes = (datetime.now().astimezone() - last_dt).total_seconds() / 60
        # 10% slack so heartbeat jitter cannot make an interval equal to the
        # heartbeat skip every other beat.
        due = elapsed_minutes >= interval * 0.9
        if not due:
            log.info(
                "skipping %s: checked %.0f min ago, interval is %d min",
                device_name(entry.url),
                elapsed_minutes,
                interval,
            )
        return due

    def _process_unsupported(self, command: Command) -> None:
        """Servermon devices are monitor-only, so any other action fails."""
        _write_command_result(
            command,
            [
                {
                    "CommandID": command.command_id,
                    "DeviceID": command.target_device,
                    "Result": "Error",
                }
            ],
        )
        log.warning(
            "unsupported command %r for device %s: reported Error",
            command.name,
            command.target_device,
        )
        _remove_command_file(command)

    def check_and_report(
        self,
        entries: list[UrlEntry],
        sequence: int | None = None,
        allow_hops: bool = True,
    ) -> list[tuple[UrlEntry, CheckResult, dict[str, Any]]]:
        """Check every entry and build its device report, updating (and
        persisting) the per-device last-error state.
        """
        if not entries:
            return []
        log.info("checking %d URL(s)", len(entries))
        hops_urls = (
            {e.url for e in entries if self._hops_due(e)} if allow_hops else set()
        )
        rows = []
        for entry, result, hops in self.run_checks(entries, hops_urls=hops_urls):
            device_state = self.state.record(
                device_id(entry.url),
                result,
                hops_measured=entry.url in hops_urls,
                network_hops=hops,
            )
            report = build_report(
                entry,
                result,
                sequence=sequence,
                device_state=device_state,
                default_interval=self.default_interval,
            )
            # Cache the report so refreshes within this URL's check interval
            # can re-submit it without a new HTTP check.
            self.state.store_report(device_id(entry.url), report)
            rows.append((entry, result, report))
        self.state.save()
        return rows

    def run_checks(
        self, entries: list[UrlEntry], hops_urls: set[str] | None = None
    ) -> list[tuple[UrlEntry, CheckResult, int | None]]:
        """Check every entry (in parallel); pair each with its result and
        its network hop count (None unless the URL is in hops_urls and the.

        measurement succeeded).
        """
        if not entries:
            return []
        hops_urls = hops_urls or set()
        workers = min(MAX_PARALLEL_CHECKS, len(entries))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(
                pool.map(lambda e: self._check_one(e, e.url in hops_urls), entries)
            )
        return [
            (entry, result, hops) for entry, (result, hops) in zip(entries, results)
        ]

    def _check_one(
        self, entry: UrlEntry, measure_hops: bool = False
    ) -> tuple[CheckResult, int | None]:
        result = check_url(
            entry,
            timeout=self.config.timeout_for(entry),
            user_agent=self.config.user_agent,
        )
        hops = None
        if measure_hops:
            hops = measure_network_hops(
                entry.url, timeout=self.config.timeout_for(entry)
            )
            if hops is None:
                log.info("network hops for %s: measurement failed", entry.url)
            else:
                log.info("network hops for %s: %d", entry.url, hops)
        return result, hops


def _major_minor(version: str | None) -> tuple[int, int] | None:
    """The (major, minor) of a version string, or None if it is absent or
    not of the form ``<int>.<int>[...]`` (e.g. a dev/rc suffix on the minor).
    """
    if not version:
        return None
    parts = version.split(".")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def _parse_positive_int(text: str) -> int | None:
    try:
        value = int(text.strip())
    except (AttributeError, ValueError):
        return None
    return value if value >= 1 else None


def _write_command_result(command: Command, results: list[dict[str, str]]) -> None:
    result_name = f"{command.command_id}-{os.getpid()}-{next(_result_seq)}.json"
    write_json_atomic(command.output_directory / result_name, results)


def _remove_command_file(command: Command) -> None:
    try:
        os.remove(command.location)
    except FileNotFoundError:
        pass  # some Proxy Agent versions clean up command files themselves
    except OSError as error:
        log.warning("could not remove command file %s: %s", command.location, error)
