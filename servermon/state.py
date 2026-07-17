"""Persist per-device history (last error, last URL contact) across runs.

Device reports fully replace a device's previous data in BigFix (confirmed
against a live Proxy Agent), so anything that must outlive one check - the
most recent error, the last time the URL actually responded - has to be
remembered by the plugin and re-sent with every report.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .util import write_json_atomic

if TYPE_CHECKING:
    from .checker import CheckResult

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LastError:
    detail: str
    time: str


@dataclass(frozen=True)
class DeviceRecord:
    """What the state store knows about one device."""

    last_error: LastError | None = None
    # Last time an HTTP response was actually received from the URL (any
    # status code); None if the URL has never responded.
    last_contact: str | None = None


class DeviceState:
    """Per-device history, backed by a JSON file (or in-memory only when no
    path is given).
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._data: dict[str, dict[str, Any]] = _read_state(path)
        # Devices updated/removed by this instance, kept separately so save()
        # can merge them over the file's current contents instead of
        # overwriting it wholesale (see save()).
        self._updates: dict[str, dict[str, Any]] = {}
        self._removals: set[str] = set()

    def record(self, device_id: str, result: CheckResult) -> DeviceRecord:
        """Record the result of a check; return the device's history.

        Receiving any HTTP response (even a 500) counts as contact with the
        URL; a failed check becomes the new last error. Values not updated
        by this check keep their previously recorded state.
        """
        entry = dict(self._data.get(device_id, {}))
        entry["last check"] = result.checked_at
        if result.status_code != 0:
            entry["last contact"] = result.checked_at
        if not result.success:
            entry["last error"] = {"detail": result.detail, "time": result.checked_at}
        self._data[device_id] = entry
        self._updates[device_id] = entry
        return _to_record(entry)

    def last_check(self, device_id: str) -> str | None:
        """When this device was last actually checked (any outcome), used to
        honor per-URL check_interval_minutes across plugin runs.
        """
        value = self._data.get(device_id, {}).get("last check")
        return value if isinstance(value, str) else None

    def store_report(self, device_id: str, report: dict[str, Any]) -> None:
        """Cache the device's report so refreshes within its check interval
        can re-submit it without performing a new HTTP check.
        """
        entry = dict(self._data.get(device_id, {}))
        entry["last report"] = {
            key: value
            for key, value in report.items()
            if key not in ("device report sequence", "deviceReportSequence")
        }
        self._data[device_id] = entry
        self._updates[device_id] = entry

    def cached_report(self, device_id: str) -> dict[str, Any] | None:
        report = self._data.get(device_id, {}).get("last report")
        return dict(report) if isinstance(report, dict) else None

    def mark_pending_deletion(self, device_id: str) -> None:
        """Flag a device for deletion without removing it yet.

        "delete device" defers the actual removal until the device has been
        reported one more time, so the Proxy Agent's post-action refresh gets
        a device report and the action can transition out of "running".
        """
        entry = dict(self._data.get(device_id, {}))
        entry["pending deletion"] = True
        self._data[device_id] = entry
        self._updates[device_id] = entry

    def is_pending_deletion(self, device_id: str) -> bool:
        return bool(self._data.get(device_id, {}).get("pending deletion"))

    def forget(self, device_id: str) -> None:
        """Drop all history for a device (used to finalize "delete device")."""
        self._data.pop(device_id, None)
        self._updates.pop(device_id, None)
        self._removals.add(device_id)

    def save(self) -> None:
        if self.path is None:
            return
        try:
            # The Proxy Agent may run several plugin instances concurrently
            # (never against the same device), so another instance may have
            # saved since we loaded. Re-read and overlay only this
            # instance's updates so theirs are not rolled back.
            current = _read_state(self.path)
            current.update(self._updates)
            for device in self._removals:
                current.pop(device, None)
            write_json_atomic(self.path, current)
        except OSError as error:
            # Losing history must not break monitoring itself.
            log.warning("could not write state file %s: %s", self.path, error)


def _to_record(entry: dict[str, Any]) -> DeviceRecord:
    error = entry.get("last error")
    last_error = None
    if isinstance(error, dict):
        last_error = LastError(detail=error["detail"], time=error["time"])
    return DeviceRecord(last_error=last_error, last_contact=entry.get("last contact"))


def _read_state(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        log.warning("state file %s unreadable, starting fresh: %s", path, error)
        return {}
    if not isinstance(data, dict):
        return {}

    state: dict[str, dict[str, Any]] = {}
    for device, entry in data.items():
        if not isinstance(entry, dict):
            continue
        if isinstance(entry.get("detail"), str) and isinstance(entry.get("time"), str):
            # Migrate the pre-0.2 format, where the entry was the last error.
            entry = {"last error": {"detail": entry["detail"], "time": entry["time"]}}

        cleaned: dict[str, Any] = {}
        error = entry.get("last error")
        if (
            isinstance(error, dict)
            and isinstance(error.get("detail"), str)
            and isinstance(error.get("time"), str)
        ):
            cleaned["last error"] = {"detail": error["detail"], "time": error["time"]}
        if isinstance(entry.get("last contact"), str):
            cleaned["last contact"] = entry["last contact"]
        if isinstance(entry.get("last check"), str):
            cleaned["last check"] = entry["last check"]
        if isinstance(entry.get("last report"), dict):
            cleaned["last report"] = entry["last report"]
        if entry.get("pending deletion") is True:
            cleaned["pending deletion"] = True
        if cleaned:
            state[device] = cleaned
    return state
