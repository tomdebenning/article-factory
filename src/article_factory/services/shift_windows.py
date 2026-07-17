"""UTC six-hour shift windows for the newsroom."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal

ShiftKey = Literal["night", "morning", "afternoon", "evening"]

SHIFT_ORDER: tuple[ShiftKey, ...] = ("night", "morning", "afternoon", "evening")

SHIFT_LABELS: dict[ShiftKey, str] = {
    "night": "Night Shift · 12 AM – 6 AM UTC",
    "morning": "Morning Shift · 6 AM – 12 PM UTC",
    "afternoon": "Afternoon Shift · 12 PM – 6 PM UTC",
    "evening": "Evening Shift · 6 PM – 12 AM UTC",
}


@dataclass(frozen=True)
class ShiftWindow:
    shift_key: ShiftKey
    starts_at: datetime
    ends_at: datetime

    @property
    def label(self) -> str:
        return SHIFT_LABELS[self.shift_key]

    @property
    def window_key(self) -> str:
        return self.starts_at.strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def shift_key_for_hour(hour: int) -> ShiftKey:
    if hour < 6:
        return "night"
    if hour < 12:
        return "morning"
    if hour < 18:
        return "afternoon"
    return "evening"


def shift_window_containing(dt: datetime) -> ShiftWindow:
    """Return the six-hour UTC window that contains *dt*."""
    utc = _as_utc(dt)
    block_hour = (utc.hour // 6) * 6
    starts = utc.replace(hour=block_hour, minute=0, second=0, microsecond=0)
    ends = starts + timedelta(hours=6)
    return ShiftWindow(shift_key=shift_key_for_hour(starts.hour), starts_at=starts, ends_at=ends)


def rolling_shift_windows(*, from_dt: datetime | None = None, count: int = 4) -> list[ShiftWindow]:
    """Next *count* shift windows starting with the window that contains *from_dt*."""
    cursor = shift_window_containing(from_dt or datetime.now(timezone.utc))
    windows: list[ShiftWindow] = []
    for _ in range(max(1, count)):
        windows.append(cursor)
        cursor = ShiftWindow(
            shift_key=shift_key_for_hour(cursor.ends_at.hour),
            starts_at=cursor.ends_at,
            ends_at=cursor.ends_at + timedelta(hours=6),
        )
    return windows


def calendar_day_shift_windows(day: date) -> list[ShiftWindow]:
    """Four six-hour UTC windows for a calendar day."""
    base = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    windows: list[ShiftWindow] = []
    for hour in (0, 6, 12, 18):
        starts = base.replace(hour=hour)
        ends = starts + timedelta(hours=6)
        windows.append(
            ShiftWindow(shift_key=shift_key_for_hour(hour), starts_at=starts, ends_at=ends)
        )
    return windows


def today_and_tomorrow_shift_windows(*, from_dt: datetime | None = None) -> list[ShiftWindow]:
    """Today's four UTC shifts plus tomorrow's four (eight windows total)."""
    utc = _as_utc(from_dt or datetime.now(timezone.utc))
    today = utc.date()
    tomorrow = today + timedelta(days=1)
    return calendar_day_shift_windows(today) + calendar_day_shift_windows(tomorrow)
