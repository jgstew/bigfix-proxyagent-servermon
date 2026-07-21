"""Persist per-device history (last error, last URL contact) across runs.

Device reports fully replace a device's previous data in BigFix (confirmed
against a live Proxy Agent), so anything that must outlive one check - the
most recent error, the last time the URL actually responded - has to be
remembered by the plugin and re-sent with every report.

Built on the SDK's :class:`~bigfix_proxyagent.state.DeviceStateStore`, which
provides the JSON backing, merge-on-save concurrency handling, report caching,
and pending-deletion machinery; this subclass adds servermon's typed
accessors and the set of fields it persists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from bigfix_proxyagent.state import DeviceStateStore

from . import __version__

if TYPE_CHECKING:
    from .checker import CheckResult


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
    # Most recently measured network hop count (measure_network_hops URLs
    # only); None if never successfully measured.
    network_hops: int | None = None


class DeviceState(DeviceStateStore):
    """Servermon's per-device history (backed by a JSON file, or in-memory
    only when no path is given).
    """

    def record(
        self,
        device_id: str,
        result: CheckResult,
        *,
        hops_measured: bool = False,
        network_hops: int | None = None,
    ) -> DeviceRecord:
        """Record the result of a check; return the device's history.

        Receiving any HTTP response (even a 500) counts as contact with the
        URL; a failed check becomes the new last error. Values not updated
        by this check keep their previously recorded state. When a network
        hops measurement was attempted (hops_measured), its time is recorded
        even if it failed - so a failure still waits a full hops interval
        before the next attempt - but a failed measurement keeps the
        previously known hop count.
        """
        entry = self.get(device_id)
        entry["last check"] = result.checked_at
        # The servermon version that produced this check, so a later run can
        # tell whether the plugin was upgraded since (see last_check_version).
        entry["last check version"] = __version__
        if result.status_code != 0:
            entry["last contact"] = result.checked_at
        if not result.success:
            entry["last error"] = {"detail": result.detail, "time": result.checked_at}
        if hops_measured:
            entry["last hops check"] = result.checked_at
            if network_hops is not None:
                entry["network hops"] = network_hops
        self.update(device_id, entry)
        return _to_record(entry)

    def last_check(self, device_id: str) -> str | None:
        """When this device was last actually checked (any outcome), used to
        honor per-URL refresh_interval_minutes across plugin runs.
        """
        value = self._data.get(device_id, {}).get("last check")
        return value if isinstance(value, str) else None

    def last_check_version(self, device_id: str) -> str | None:
        """Servermon version in effect when this device was last checked.

        None if never checked or the state predates version tracking. Used to
        force a fresh check after a plugin upgrade.
        """
        value = self._data.get(device_id, {}).get("last check version")
        return value if isinstance(value, str) else None

    def last_hops_check(self, device_id: str) -> str | None:
        """When a network hops measurement was last attempted for this
        device (successful or not); None if never.
        """
        value = self._data.get(device_id, {}).get("last hops check")
        return value if isinstance(value, str) else None

    def _clean_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        if isinstance(entry.get("detail"), str) and isinstance(entry.get("time"), str):
            # Migrate the pre-0.2 format, where the entry was the last error.
            entry = {"last error": {"detail": entry["detail"], "time": entry["time"]}}

        # The base keeps the generic keys ("last report", "pending deletion");
        # add servermon's own persisted fields.
        cleaned = super()._clean_entry(entry)
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
        if isinstance(entry.get("last check version"), str):
            cleaned["last check version"] = entry["last check version"]
        if isinstance(entry.get("last hops check"), str):
            cleaned["last hops check"] = entry["last hops check"]
        hops = entry.get("network hops")
        if isinstance(hops, int) and not isinstance(hops, bool):
            cleaned["network hops"] = hops
        return cleaned


def _to_record(entry: dict[str, Any]) -> DeviceRecord:
    error = entry.get("last error")
    last_error = None
    if isinstance(error, dict):
        last_error = LastError(detail=error["detail"], time=error["time"])
    hops = entry.get("network hops")
    return DeviceRecord(
        last_error=last_error,
        last_contact=entry.get("last contact"),
        network_hops=hops if isinstance(hops, int) else None,
    )
