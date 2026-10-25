"""Forecast: TabPFN predicts each remaining day of the pay period. Wall Day = the day the budget runs out.

STUB: the contract below is fixed; the forecast work fills in the bodies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.engine import Engine


@dataclass
class DayForecast:
    day: date
    likely: float  # median (0.5 quantile)
    low: float | None = None  # 0.1 quantile, None if quantiles failed
    high: float | None = None  # 0.9 quantile
    extra: float = 0.0  # "can I afford" money added on this day
    actual: float | None = None  # money already spent (days up to and including today)


@dataclass
class Forecast:
    today: date
    period_start: date  # last payday (inclusive)
    period_end: date  # the day before the next payday (inclusive)
    budget: float
    spent_so_far: float  # in this pay period, up to and including today
    likely_total: float  # spent_so_far + predicted remaining days (+ extra)
    low_total: float | None
    high_total: float | None
    wall_day: date | None  # first day where spent + likely passes the budget
    wall_day_early: date | None  # same with the high quantile ("maybe as early as")
    biggest_reason: str | None  # e.g. "Yalda Night"
    birthdays: int  # birthdays from today to period_end
    holidays: list[str] = field(default_factory=list)  # holiday names in the rest of the period
    days: list[DayForecast] = field(default_factory=list)
    method: str = "tabpfn"  # tabpfn | tabpfn-median | average
    note: str | None = None  # e.g. "I need a few more weeks of receipts"
    extra: list[tuple[date, float]] = field(default_factory=list)


async def run_forecast(engine: Engine, today: date, extra: list[tuple[date, float]] | None = None) -> Forecast:
    """Forecast the rest of the pay period from the ledger and the events.

    extra: money the user is thinking about spending, as (day, amount). It is added on top of
    the prediction for that day; TabPFN itself is not called again (its daily predictions are
    cached per day and per data fingerprint).
    """
    raise NotImplementedError


def format_forecast(f: Forecast, currency: str = "₺") -> str:
    """The /forecast reply (plain text with emoji)."""
    raise NotImplementedError


def format_afford(base: Forecast, with_extra: Forecast, amount: float, on: date, about: str | None, currency: str = "₺") -> str:
    """One or two lines: can I afford it, and how Wall Day moves."""
    raise NotImplementedError
