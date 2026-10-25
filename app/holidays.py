"""Persian holidays, hardcoded for 2025-2027 (no calendar library at runtime).

The dates were checked once against the Persian calendar (jdatetime):
Yalda Night = the evening of Azar 30, Chaharshanbe Suri = the Tuesday evening before the last
Wednesday of the year, Nowruz = Farvardin 1, Sizdah Bedar = Farvardin 13.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Holiday:
    day: date
    name: str  # "Yalda Night", "Chaharshanbe Suri", "Nowruz", "Sizdah Bedar"
    kind: str  # "yalda" | "chaharshanbe_suri" | "nowruz" | "sizdah_bedar"


NAMES = {
    "yalda": "Yalda Night",
    "chaharshanbe_suri": "Chaharshanbe Suri",
    "nowruz": "Nowruz",
    "sizdah_bedar": "Sizdah Bedar",
}

# Small numbers for the forecast table (0 = no holiday).
KIND_CODES = {"yalda": 1, "chaharshanbe_suri": 2, "nowruz": 3, "sizdah_bedar": 4}

EMOJI = {"yalda": "🍉", "chaharshanbe_suri": "🔥", "nowruz": "🌱", "sizdah_bedar": "🌳"}

_DATES = {
    "chaharshanbe_suri": [date(2025, 3, 18), date(2026, 3, 17), date(2027, 3, 16)],
    "nowruz": [date(2025, 3, 21), date(2026, 3, 21), date(2027, 3, 21)],
    "sizdah_bedar": [date(2025, 4, 2), date(2026, 4, 2), date(2027, 4, 2)],
    "yalda": [date(2025, 12, 21), date(2026, 12, 21), date(2027, 12, 21)],
}

HOLIDAYS: list[Holiday] = sorted(
    (Holiday(day, NAMES[kind], kind) for kind, days in _DATES.items() for day in days),
    key=lambda h: h.day,
)


def holidays_between(start: date, end: date) -> list[Holiday]:
    """Holidays with start <= day <= end, sorted by day."""
    return [h for h in HOLIDAYS if start <= h.day <= end]


def holiday_on(day: date) -> Holiday | None:
    for h in HOLIDAYS:
        if h.day == day:
            return h
    return None


def next_holiday(after: date) -> Holiday | None:
    """The first holiday on or after `after`."""
    for h in HOLIDAYS:
        if h.day >= after:
            return h
    return None


def find_holiday(name: str, after: date) -> Holiday | None:
    """The next holiday on or after `after` whose name matches (e.g. 'yalda'). None if unknown."""
    key = name.lower().replace(" ", "")
    for h in HOLIDAYS:
        if h.day >= after and (key in h.name.lower().replace(" ", "") or key in h.kind.replace("_", "")):
            return h
    return None
