from __future__ import annotations

import datetime as dt
import logging
from typing import List

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from config import CurrencyReportConfig

log = logging.getLogger(__name__)


def generate_schedule_times(config: CurrencyReportConfig) -> List[dt.time]:
    """
    Generate timezone-aware times for one day based on the configured start time and interval.
    These times are used by discord.ext.tasks.loop to trigger jobs every day.
    """
    tz = _resolve_timezone(config.timezone)
    interval = max(1, config.interval_minutes)
    start_minutes = _normalize_minutes(config.hour, config.minute)
    minutes = start_minutes
    seen: set[int] = set()
    times: list[dt.time] = []

    while minutes not in seen:
        seen.add(minutes)
        hour, minute = divmod(minutes, 60)
        times.append(dt.time(hour=hour, minute=minute, tzinfo=tz))
        minutes = (minutes + interval) % (24 * 60)
        if interval >= 24 * 60:
            break

    if not times:
        times.append(dt.time(hour=config.hour % 24, minute=config.minute % 60, tzinfo=tz))

    times.sort(key=lambda t: (t.hour, t.minute))
    return times


def compute_next_run(config: CurrencyReportConfig, reference: dt.datetime | None = None) -> dt.datetime:
    """
    Compute the next scheduled run datetime in the configured timezone, based on the
    defined interval and anchor time.
    """
    tz = _resolve_timezone(config.timezone)
    now = reference.astimezone(tz) if reference else dt.datetime.now(tz)
    times = generate_schedule_times(config)
    for time_obj in times:
        candidate = dt.datetime.combine(now.date(), dt.time(time_obj.hour, time_obj.minute, tzinfo=tz))
        if candidate > now:
            return candidate

    first_time = min(times, key=lambda t: (t.hour, t.minute))
    next_day = now.date() + dt.timedelta(days=1)
    return dt.datetime.combine(next_day, dt.time(first_time.hour, first_time.minute, tzinfo=tz))


def _normalize_minutes(hour: int, minute: int) -> int:
    hour = hour % 24
    minute = minute % 60
    return hour * 60 + minute


def _resolve_timezone(tz_name: str) -> dt.tzinfo:
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        fallback_hours = 9 if tz_name in {"Asia/Seoul", "KST"} else 0
        log.warning("Timezone %s not found. Falling back to UTC%+d.", tz_name, fallback_hours)
        return dt.timezone(dt.timedelta(hours=fallback_hours))
