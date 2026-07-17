"""Persist the most recent error per device across plugin runs.

Device reports fully replace a device's previous data in BigFix (confirmed
against a live Proxy Agent), so "keep the last error visible after it
clears" cannot be done by omitting report keys - the plugin has to remember
errors itself and keep reporting them.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .util import write_json_atomic

if TYPE_CHECKING:
    from .checker import CheckResult

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LastError:
    detail: str
    time: str


class ErrorState:
    """Last error per device id, backed by a JSON file (or in-memory only
    when no path is given).
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._data: dict[str, dict[str, str]] = {}
        if path is not None and path.is_file():
            try:
                with path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._data = {
                        device: entry
                        for device, entry in data.items()
                        if isinstance(entry, dict)
                        and isinstance(entry.get("detail"), str)
                        and isinstance(entry.get("time"), str)
                    }
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
                log.warning("state file %s unreadable, starting fresh: %s", path, error)

    def record(self, device_id: str, result: CheckResult) -> LastError | None:
        """Record the result of a check; return the device's last error.

        A failed check becomes the new last error; a successful check leaves
        the previously recorded error in place. Returns None only when the
        device has never failed.
        """
        if not result.success:
            self._data[device_id] = {"detail": result.detail, "time": result.checked_at}
        entry = self._data.get(device_id)
        if entry is None:
            return None
        return LastError(detail=entry["detail"], time=entry["time"])

    def save(self) -> None:
        if self.path is None:
            return
        try:
            write_json_atomic(self.path, self._data)
        except OSError as error:
            # Losing error history must not break monitoring itself.
            log.warning("could not write state file %s: %s", self.path, error)
