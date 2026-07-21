"""Process Proxy Agent commands: run the URL checks and write device reports."""

from __future__ import annotations

import email.utils
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from bigfix_proxyagent.config import (Field, Settings, apply_set_command,
                                      parse_bool, parse_int,
                                      parse_positive_float, parse_regex)
from bigfix_proxyagent.plugin import ProxyAgentPlugin
from bigfix_proxyagent.util import major_minor

from . import __version__
from .checker import CheckResult, check_url, measure_network_hops
from .command import Command
from .config import (Config, ConfigError, UrlEntry, add_url_entry,
                     clear_url_option, remove_url_entry, set_url_option,
                     set_url_refresh_interval)
from .device import build_report, device_id, device_name
from .state import DeviceState

log = logging.getLogger(__name__)

MAX_PARALLEL_CHECKS = 8

# An opted-in URL (measure_network_hops) gets a hop measurement alongside 1 in
# every N regular checks; the cadence is N x the URL's effective check
# interval, deliberately not separately configurable.
HOPS_EVERY_N_CHECKS = 6

SET_REFRESH_INTERVAL = "set refresh interval"
DELETE_DEVICE = "delete device"
# "push link <url>": a whitelisted actionscript command (ProxyPluginCommands.json
# lists it under "Divide Container") that we reuse to add a new monitored URL
# from the console. It can target any servermon device - the target is
# irrelevant, the URL to add comes from the arguments (see README "Adding a URL").
PUSH_LINK = "push link"
# "set <field> <value>": whitelisted "set" command reused as a generic per-URL
# option setter, targeted at a URL device. "set refresh interval" is a separate
# (longer) whitelist entry the agent matches first, so it routes to its own
# handler; everything else arrives here as name "set" with "<field> <value>".
SET = "set"


class ServerMonPlugin(ProxyAgentPlugin):
    def __init__(
        self,
        config: Config,
        state_file: Path | None = None,
        config_path: Path | None = None,
    ) -> None:
        self.config = config
        self.config_path = config_path  # needed by "set refresh interval"
        self.state = DeviceState(state_file, backend=config.state_backend)

    def commands(self):
        # The whitelisted actionscript commands servermon handles; the command
        # loop (refresh dispatch, unsupported -> Error, ack) lives in the SDK's
        # ProxyAgentPlugin base class.
        return {
            SET_REFRESH_INTERVAL: self._process_set_refresh_interval,
            DELETE_DEVICE: self._process_delete_device,
            PUSH_LINK: self._process_push_link,
            SET: self._process_set,
        }

    def handle_refresh(self, command: Command) -> None:
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
                self.remove_command_file(command)
                return
        else:
            entries = list(self.config.urls)

        new_entries: list[UrlEntry] = []
        # Device ids that received a report this invocation (via a real check
        # or a cached replay); used to finalize deferred deletions.
        reported_ids: set[str] = set()
        if not command.command_id:
            # Honor per-URL refresh_interval_minutes for Proxy Agent driven
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
                self.remove_command_file(command)
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
            self.write_command_result(command, results)
            for _, result, report in rows:
                log.info("check now %s: %s", report["computer name"], result.detail)
        else:
            for _, result, report in rows:
                self.write_report(command.output_directory, report)
                log.info("%s: %s", report["computer name"], result.detail)
            # Now that the post-action refresh has been answered with a
            # report, complete any deferred "delete device" removals.
            self._finalize_pending_deletions(reported_ids)
        # Deleting the command file acknowledges the refresh is done; if
        # writing a report failed we raise before reaching this, leaving the
        # command in place for the next invocation to retry.
        self.remove_command_file(command)

    def _process_set_refresh_interval(self, command: Command) -> None:
        """Actionscript "set refresh interval <minutes>": persist a per-URL
        refresh_interval_minutes into the servermon.toml config file.
        """
        outcome = "Error"
        entries = self._match_target(command.target_device, command.target_hint)
        minutes = parse_int(str(command.command_arguments))
        if not entries:
            log.warning(
                "%s: no URL in config for device %r",
                SET_REFRESH_INTERVAL,
                command.target_device,
            )
        elif minutes is None:
            log.warning(
                "%s: arguments %r are not an integer of minutes",
                SET_REFRESH_INTERVAL,
                command.get("commandarguments"),
            )
        elif self.config_path is None:
            log.warning("%s: no config file path to update", SET_REFRESH_INTERVAL)
        else:
            try:
                set_url_refresh_interval(self.config_path, entries[0].url, minutes)
                # Also update the in-memory config so later commands in this
                # same invocation see (and report) the new interval.
                updated = replace(entries[0], refresh_interval_minutes=minutes)
                self.config = replace(
                    self.config,
                    urls=tuple(
                        updated if e is entries[0] else e for e in self.config.urls
                    ),
                )
                outcome = "Completed"
                log.info(
                    "%s: set refresh_interval_minutes = %d for %s",
                    SET_REFRESH_INTERVAL,
                    minutes,
                    entries[0].url,
                )
            except ConfigError as error:
                log.warning("%s failed: %s", SET_REFRESH_INTERVAL, error)

        self.respond(command, outcome)
        self.remove_command_file(command)

    def _process_set(self, command: Command) -> None:
        """Actionscript "set <field> <value>": set one per-URL option on the
        targeted device's ``[[urls]]`` entry in servermon.toml.

        Supported fields mirror the config keys: match, no_match,
        timeout_seconds, refresh_interval_minutes, verify_tls,
        measure_network_hops. The value is validated for the field's type
        before writing; an empty value clears the field (reverts it to its
        default). An unknown field, a bad value, an unknown device, or a write
        that would not parse reports Error.
        """
        outcome = "Error"
        entries = self._match_target(command.target_device, command.target_hint)
        if not entries:
            log.warning("%s: no URL in config for device %r", SET,
                        command.target_device)
        elif self.config_path is None:
            log.warning("%s: no config file path to update", SET)
        else:
            entry = entries[0]
            # The SDK parses and validates "<field> <value>" against _SETTABLE
            # (unknown/disallowed field, bad value -> Error) and calls back here
            # only to persist an accepted change.
            outcome = apply_set_command(
                command,
                _SETTABLE,
                lambda field, value, clearing: self._apply_to_entry(
                    entry, field, value, clearing
                ),
            )

        self.respond(command, outcome)
        self.remove_command_file(command)

    def _apply_to_entry(
        self, entry: UrlEntry, field: str, value: object, clearing: bool
    ) -> None:
        """Persist one settable option on ``entry`` in the config file and in
        memory.

        Raises ConfigError if the write fails (reported as Error).
        """
        if clearing:
            clear_url_option(self.config_path, entry.url, field)
        else:
            set_url_option(self.config_path, entry.url, field, value)
        # Update the in-memory config so a later command/refresh in this same
        # invocation sees the new value.
        self.config = replace(
            self.config,
            urls=tuple(
                replace(e, **{field: value}) if e is entry else e
                for e in self.config.urls
            ),
        )

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

        self.respond(command, outcome)
        self.remove_command_file(command)

    def _process_push_link(self, command: Command) -> None:
        """Actionscript "push link <url>": add a new [[urls]] entry to
        servermon.toml.

        Unlike the other actions this ignores the targeted device - it may be
        sent to any servermon device to register a brand-new URL. The whole
        argument is the URL; it is appended to the config (and the in-memory
        copy, so a later refresh in the same batch reports it). A duplicate or
        malformed URL is rejected by the config writer and reported as Error.
        """
        outcome = "Error"
        url = str(command.get("commandarguments")).strip()
        if not url:
            log.warning("%s: no URL given", PUSH_LINK)
        elif self.config_path is None:
            log.warning("%s: no config file path to update", PUSH_LINK)
        else:
            try:
                add_url_entry(self.config_path, url)
                # Reflect the addition in-memory so a full refresh later in this
                # same invocation picks it up as a newly configured URL.
                self.config = replace(
                    self.config, urls=self.config.urls + (UrlEntry(url=url),)
                )
                outcome = "Completed"
                log.info("%s: added %s", PUSH_LINK, url)
            except ConfigError as error:
                log.warning("%s failed: %s", PUSH_LINK, error)

        self.respond(command, outcome)
        self.remove_command_file(command)

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
        report["refresh interval"] = self.config.refresh_interval_for(entry)
        if command.device_report_sequence is not None:
            report["device report sequence"] = command.device_report_sequence
            report["deviceReportSequence"] = command.device_report_sequence
        self.write_report(command.output_directory, report)
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
        interval = self.config.refresh_interval_for(entry)
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
        previous = major_minor(self.state.last_check_version(device_id(entry.url)))
        current = major_minor(__version__)
        if previous is None or current is None:
            return False
        return current > previous

    def _is_due(self, entry: UrlEntry) -> bool:
        interval = self.config.refresh_interval_for(entry)
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
                refresh_interval=self.config.refresh_interval_for(entry),
                computer_name=self.config.display_name(entry),
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


# "set <field> <value>" fields, mapped to the SDK parser that turns the raw
# argument text into the typed value. Keys match UrlEntry fields; each field's
# default (restored when "set <field>" is given with no value) is the
# corresponding UrlEntry default. Every field here is settable from BigFix;
# to lock one to file-only editing, pass Field(..., settable=False).
_SETTABLE_PARSERS = {
    "match": parse_regex,
    "no_match": parse_regex,
    "timeout_seconds": parse_positive_float,
    "refresh_interval_minutes": parse_int,
    "verify_tls": parse_bool,
    "measure_network_hops": parse_bool,
}
_URL_ENTRY_DEFAULTS = {
    f.name: f.default for f in fields(UrlEntry) if f.name in _SETTABLE_PARSERS
}
_SETTABLE = Settings(
    {
        name: Field(parser, default=_URL_ENTRY_DEFAULTS[name])
        for name, parser in _SETTABLE_PARSERS.items()
    }
)
