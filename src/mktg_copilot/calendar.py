"""A small US retail calendar, used only to label incidents so a known peak is not mistaken for a fault."""
from __future__ import annotations

from datetime import date, timedelta

# (start, end, name). Exact-day windows; no padding, so a nearby date never explains away a real fault.
RETAIL_EVENTS: tuple[tuple[date, date, str], ...] = (
    (date(2025, 11, 28), date(2025, 12, 1), "Black Friday weekend and Cyber Monday"),
    (date(2025, 12, 24), date(2025, 12, 25), "Christmas"),
    (date(2026, 1, 1), date(2026, 1, 1), "New Year's Day"),
    (date(2026, 2, 14), date(2026, 2, 14), "Valentine's Day"),
    (date(2026, 4, 5), date(2026, 4, 5), "Easter Sunday"),
    (date(2026, 5, 10), date(2026, 5, 10), "Mother's Day"),
    (date(2026, 5, 25), date(2026, 5, 25), "Memorial Day"),
    (date(2026, 7, 4), date(2026, 7, 4), "Independence Day"),
    (date(2026, 9, 7), date(2026, 9, 7), "Labor Day"),
)

NAMED_PERIODS = {
    "black friday weekend": (date(2025, 11, 28), date(2025, 12, 1)),
    "black friday": (date(2025, 11, 28), date(2025, 11, 28)),
    "cyber monday": (date(2025, 12, 1), date(2025, 12, 1)),
    "thanksgiving": (date(2025, 11, 27), date(2025, 11, 27)),
}


def event_context(start: date, end: date) -> str | None:
    for s, e, name in RETAIL_EVENTS:
        if start <= e and end >= s:
            return f"overlaps {name}, a known retail date"
    return None


def holiday_in_window(start: date, end: date) -> str | None:
    """Black Friday of the next season falls outside the data, but warn when a forecast window reaches it."""
    for yr in (start.year, start.year + 1):
        # fourth Thursday of November, plus one day
        d = date(yr, 11, 1)
        thursdays = [d + timedelta(days=i) for i in range(30) if (d + timedelta(days=i)).weekday() == 3]
        bf = thursdays[3] + timedelta(days=1)
        if start <= bf + timedelta(days=3) and end >= bf:
            return "Black Friday weekend"
    return None
