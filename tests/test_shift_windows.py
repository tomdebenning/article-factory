from datetime import datetime, timezone

from article_factory.services.shift_windows import (
    rolling_shift_windows,
    shift_window_containing,
    today_and_tomorrow_shift_windows,
)


def test_shift_window_morning_utc() -> None:
    dt = datetime(2026, 7, 17, 8, 30, tzinfo=timezone.utc)
    window = shift_window_containing(dt)
    assert window.shift_key == "morning"
    assert window.starts_at.hour == 6
    assert window.ends_at.hour == 12


def test_today_and_tomorrow_shift_windows() -> None:
    dt = datetime(2026, 7, 17, 8, 30, tzinfo=timezone.utc)
    windows = today_and_tomorrow_shift_windows(from_dt=dt)
    assert len(windows) == 8
    assert windows[0].shift_key == "night"
    assert windows[3].shift_key == "evening"
    assert windows[4].shift_key == "night"
    assert windows[4].starts_at.date().day == 18


def test_rolling_four_shifts() -> None:
    dt = datetime(2026, 7, 17, 8, 30, tzinfo=timezone.utc)
    windows = rolling_shift_windows(from_dt=dt, count=4)
    assert len(windows) == 4
    assert windows[0].shift_key == "morning"
    assert windows[1].shift_key == "afternoon"
    assert windows[2].shift_key == "evening"
    assert windows[3].shift_key == "night"
