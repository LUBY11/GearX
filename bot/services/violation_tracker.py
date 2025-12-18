from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass
class ViolationRecord:
    count: int
    last_triggered: dt.datetime
    last_decay_check: dt.datetime


class ViolationTracker:
    """
    Tracks incremental violation counts per (guild, user) and automatically
    decays strikes when the user stays quiet for a configurable window so
    false positives don't linger forever.
    """

    def __init__(self, decay_hours: int = 24):
        self._records: dict[tuple[int, int], ViolationRecord] = {}
        self._decay_after = dt.timedelta(hours=decay_hours)

    def increment(self, guild_id: int, user_id: int) -> int:
        now = dt.datetime.utcnow()
        key = (guild_id, user_id)
        record = self._records.get(key)
        if record:
            # If the user has been quiet, slowly decay strikes so rare false positives reset.
            elapsed = now - record.last_decay_check
            if elapsed >= self._decay_after:
                decay_steps = int(elapsed // self._decay_after)
                record.count = max(0, record.count - decay_steps)
                record.last_decay_check = now
        if record is None or record.count <= 0:
            record = ViolationRecord(count=0, last_triggered=now, last_decay_check=now)
        record.count += 1
        record.last_triggered = now
        self._records[key] = record
        return record.count

    def reset(self, guild_id: int, user_id: int) -> None:
        self._records.pop((guild_id, user_id), None)
