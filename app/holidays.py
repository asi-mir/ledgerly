"""Persian holidays, hardcoded for 2025-2027 (no calendar library). STUB."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Holiday:
    day: date
    name: str  # "Yalda Night", "Chaharshanbe Suri", "Nowruz", "Sizdah Bedar"
    kind: str  # "yalda" | "chaharshanbe_suri" | "nowruz" | "sizdah_bedar"


HOLIDAYS: list[Holiday] = []


def holidays_between(start: date, end: date) -> list[Holiday]:
    """Holidays with start <= day <= end, sorted by day."""
    return [h for h in HOLIDAYS if start <= h.day <= end]


def find_holiday(name: str, after: date) -> Holiday | None:
    """The next holiday on or after `after` whose name matches (e.g. 'yalda'). None if unknown."""
    key = name.lower().replace(" ", "")
    for h in sorted(HOLIDAYS, key=lambda h: h.day):
        if h.day >= after and (key in h.name.lower().replace(" ", "") or key in h.kind.replace("_", "")):
            return h
    return None
