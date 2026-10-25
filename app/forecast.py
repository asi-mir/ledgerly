"""Forecast: TabPFN predicts each remaining day of the pay period. Wall Day = the day the budget runs out.

One row = one day. The target is the money spent that day (days with no receipts = 0).
Every column is something the household knows in advance: the calendar, the events they told
the bot about, the Persian holidays, the month (prices rise), and recent spending frozen at a
start date. One TabPFN fit + predict covers all remaining days. Its daily predictions are
cached per (day, data fingerprint), so a second /forecast or a "can I afford" is instant.
"""

from __future__ import annotations

import asyncio
import calendar
import logging
import math
import time
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

from sqlalchemy.engine import Engine

from app import holidays as hol
from app import tracing
from app.money import format_money, round_to

log = logging.getLogger("ledgerly.forecast")

MIN_DAYS = 30  # fewer days of receipts than this: simple average
QUANTILES = [0.1, 0.5, 0.9]
SHOP_WINDOW = 3  # people shop in the 3 days before a birthday or a dinner they host
FAR = 30  # "days until" columns stop counting here
NOTE_FEW = "I need a few more weeks of receipts. Until then this is a simple average."
NOTE_FAILED = "TabPFN did not answer, so this is a simple average for now."

FEATURES = [
    "day_of_week",
    "day_of_month",
    "weekend",
    "days_since_payday",
    "days_until_payday",
    "birthday_today",
    "birthday_next_3",
    "birthdays_next_7",
    "days_until_birthday",
    "hosting_today",
    "guests_today",
    "guests_next_3",
    "days_until_hosting",
    "event_next_3",
    "holiday_today",
    "days_until_holiday",
    "holiday_type",
    "month_index",
    "spent_last_7",
    "spent_last_30",
    "days_since_big_shop",
]


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


# ---------------------------------------------------------------- pay period


def _payday_in(year: int, month: int, payday: int) -> date:
    return date(year, month, min(payday, calendar.monthrange(year, month)[1]))


def _shift_month(year: int, month: int, by: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + by
    return index // 12, index % 12 + 1


def pay_period(day: date, payday: int) -> tuple[date, date]:
    """(last payday <= day, the day before the next payday)."""
    start = _payday_in(day.year, day.month, payday)
    if start > day:
        start = _payday_in(*_shift_month(day.year, day.month, -1), payday)
    end = _payday_in(*_shift_month(start.year, start.month, 1), payday) - timedelta(days=1)
    return start, end


# ---------------------------------------------------------------- feature table


def _recent(series: dict[date, float], first_day: date, freeze: date) -> tuple[float, float, float]:
    """Spent in the 7 and 30 days before `freeze`, and days since the last big shop.

    Only days before `freeze` are read. A window that reaches before the first receipt is
    scaled from the days we have; no history at all gives NaN (TabPFN handles missing values).
    """
    known = [(freeze - timedelta(days=i), series.get(freeze - timedelta(days=i), 0.0)) for i in range(1, 91)]
    known = [(d, v) for d, v in known if d >= first_day]
    if not known:
        return math.nan, math.nan, math.nan
    last_7 = [v for d, v in known if (freeze - d).days <= 7]
    last_30 = [v for d, v in known if (freeze - d).days <= 30]
    average = sum(v for _, v in known) / len(known)
    big = [d for d, v in known if average > 0 and v >= 2 * average]
    return (
        sum(last_7) / len(last_7) * 7,
        sum(last_30) / len(last_30) * 30,
        float(min((freeze - d).days for d in big)) if big else math.nan,
    )


def _unique(events: list[dict]) -> list[dict]:
    """The same event told twice counts once (the later one wins, e.g. a new guest count)."""
    latest = {(e["date"], e.get("kind"), (e.get("title") or "").strip().lower()): e for e in events}
    return list(latest.values())


def _is_hosting(e: dict) -> bool:
    return bool(e.get("hosted")) or e.get("kind") == "hosting"


def _days_until(day: date, dates: list[date]) -> float:
    ahead = [(d - day).days for d in dates if d >= day]
    return float(min(min(ahead), FAR)) if ahead else float(FAR)


def build_rows(
    days: list[date],
    series: dict[date, float],
    events: list[dict],
    payday: int,
    first_day: date,
    freeze: date | None = None,
) -> list[list[float]]:
    """One row per day, columns = FEATURES. The backtest calls this too.

    Recent-spend columns are frozen so that no row ever sees money spent after its freeze date:
    - freeze=None (training rows): each row uses the values at the start of its own pay period.
    - freeze=<date> (future rows): every row uses the values at that date, the forecast's start.
    On payday the two are the same date, so training rows and future rows mean the same thing.
    Everything else (calendar, events, holidays, month) is known in advance for any day.
    """
    events = _unique(events)
    birthdays = sorted(e["date"] for e in events if e.get("kind") == "birthday")
    hosting = [e for e in events if _is_hosting(e)]
    hosting_days = sorted(e["date"] for e in hosting)
    all_days = sorted(e["date"] for e in events)
    frozen: dict[date, tuple[float, float, float]] = {}
    rows = []
    for day in days:
        start, end = pay_period(day, payday)
        anchor = freeze or start
        if anchor not in frozen:
            frozen[anchor] = _recent(series, first_day, anchor)
        last_7, last_30, since_big = frozen[anchor]
        soon = day + timedelta(days=SHOP_WINDOW)
        week = day + timedelta(days=7)
        upcoming = hol.next_holiday(day)
        until_holiday = (upcoming.day - day).days if upcoming else FAR
        rows.append(
            [
                day.weekday(),
                day.day,
                1.0 if day.weekday() >= 5 else 0.0,
                (day - start).days,
                (end - day).days + 1,
                sum(1 for d in birthdays if d == day),
                sum(1 for d in birthdays if day < d <= soon),
                sum(1 for d in birthdays if day <= d <= week),
                _days_until(day, birthdays),
                1.0 if day in hosting_days else 0.0,
                sum(e.get("guests") or 0 for e in hosting if e["date"] == day),
                sum(e.get("guests") or 0 for e in hosting if day < e["date"] <= soon),
                _days_until(day, hosting_days),
                1.0 if any(day < d <= soon for d in all_days) else 0.0,
                1.0 if upcoming and upcoming.day == day else 0.0,
                float(min(until_holiday, FAR)),
                float(hol.KIND_CODES[upcoming.kind]) if upcoming and until_holiday <= SHOP_WINDOW else 0.0,
                float((day.year - 2025) * 12 + day.month - 1),
                last_7,
                last_30,
                since_big,
            ]
        )
    return rows


def training_table(series: dict[date, float], events: list[dict], payday: int, first_day: date, before: date):
    """(days, X, y) for every day from the first receipt up to the day before `before`."""
    days = [first_day + timedelta(days=i) for i in range((before - first_day).days)]
    X = build_rows(days, series, events, payday, first_day)
    y = [series.get(d, 0.0) for d in days]
    return days, X, y


# ---------------------------------------------------------------- TabPFN


def _predict(X_train, y_train, X_future) -> tuple[list[float] | None, list[float], list[float] | None]:
    """One TabPFN fit + predict. Returns (low, likely, high); low/high are None if quantiles fail.

    Raises if TabPFN cannot be used at all (no token, network, quota). Tests and the backtest
    swap this function for a fake one.
    """
    import numpy as np
    from tabpfn_client import TabPFNRegressor, set_access_token

    from app.config import get_config

    token = get_config().tabpfn_token
    if not token:
        raise RuntimeError("TABPFN_TOKEN is not set")
    set_access_token(token)
    model = TabPFNRegressor()
    model.fit(np.asarray(X_train, dtype=float), np.asarray(y_train, dtype=float))
    X = np.asarray(X_future, dtype=float)
    try:
        low, mid, high = model.predict(X, output_type="quantiles", quantiles=QUANTILES)
        return [float(v) for v in low], [float(v) for v in mid], [float(v) for v in high]
    except Exception:
        log.exception("TabPFN quantiles failed, using the median only")
        mid = model.predict(X, output_type="median")
        return None, [float(v) for v in mid], None


@dataclass
class _Base:
    """Everything except the "can I afford" extra. Cached."""

    today: date
    period_start: date
    period_end: date
    budget: float
    days: list[DayForecast]
    method: str
    note: str | None
    reasons: list[tuple[date, str]]  # events and holidays from today to period_end
    birthdays: int
    holidays: list[str]
    seconds: float | None = None  # TabPFN fit + predict


_cache: dict[tuple, _Base] = {}
_CACHE_SIZE = 16


def clear_cache() -> None:
    _cache.clear()


def _load(engine: Engine) -> tuple:
    from app.db import daily_spend, data_fingerprint, get_settings, list_events

    return get_settings(engine), daily_spend(engine), list_events(engine), data_fingerprint(engine)


def _average(series: dict[date, float], first_day: date | None, today: date) -> float:
    if first_day is None or first_day >= today:
        return 0.0
    start = max(first_day, today - timedelta(days=30))
    days = [start + timedelta(days=i) for i in range((today - start).days)]
    return sum(series.get(d, 0.0) for d in days) / len(days)


async def _compute(settings: dict, series: dict[date, float], events: list[dict], today: date) -> _Base:
    budget = (settings.get("budget_cents") or 0) / 100
    payday = settings.get("payday") or 1
    start, end = pay_period(today, payday)
    future = [today + timedelta(days=i) for i in range((end - today).days + 1)]
    first_day = min(series) if series else None
    history = (today - first_day).days if first_day and first_day < today else 0

    low = high = None
    note, method, seconds = None, "average", None
    if history < MIN_DAYS:
        note = NOTE_FEW
        mid = [_average(series, first_day, today)] * len(future)
    else:
        _, X_train, y_train = training_table(series, events, payday, first_day, today)
        X_future = build_rows(future, series, events, payday, first_day, freeze=today)
        try:
            with tracing.tabpfn_span(len(X_train), len(FEATURES), len(X_future)) as span:
                started = time.perf_counter()
                low, mid, high = await asyncio.to_thread(_predict, X_train, y_train, X_future)
                seconds = round(time.perf_counter() - started, 2)
                span.set_data("tabpfn.seconds", seconds)
            log.info("tabpfn fit+predict %.2fs train_rows=%d predict_rows=%d", seconds, len(X_train), len(X_future))
            method = "tabpfn" if low is not None else "tabpfn-median"
        except Exception as exc:
            log.exception("TabPFN failed, using a simple average")
            tracing.capture_exception(exc)
            low = high = None
            note = NOTE_FAILED
            mid = [_average(series, first_day, today)] * len(future)

    predicted = {}
    for i, day in enumerate(future):
        trio = sorted(max(0.0, v[i]) for v in (low, mid, high)) if low is not None else None
        predicted[day] = (trio[0], trio[1], trio[2]) if trio else (None, max(0.0, mid[i]), None)

    days = []
    for i in range((end - start).days + 1):
        day = start + timedelta(days=i)
        if day < today:
            spent = series.get(day, 0.0)
            days.append(DayForecast(day=day, likely=spent, actual=spent))
        else:
            lo, likely, hi = predicted[day]
            actual = series.get(day, 0.0) if day == today else None
            days.append(DayForecast(day=day, likely=likely, low=lo, high=hi, actual=actual))

    upcoming = [e for e in events if today <= e["date"] <= end]
    names = {h.day: h.name for h in hol.holidays_between(today, end)}
    for e in upcoming:
        names.setdefault(e["date"], e["title"])
    birthdays = {(e["date"], e["title"].strip().lower()) for e in upcoming if e.get("kind") == "birthday"}
    return _Base(
        today=today,
        period_start=start,
        period_end=end,
        budget=budget,
        days=days,
        method=method,
        note=note,
        reasons=sorted(names.items()),
        birthdays=len(birthdays),
        holidays=[h.name for h in hol.holidays_between(today, end)],
        seconds=seconds,
    )


def _spent(d: DayForecast, today: date, value: float | None) -> float:
    """Money for one day: actual before today, max(actual, predicted) today, predicted after."""
    if d.day < today:
        return d.actual or 0.0
    if d.day == today:
        return max(d.actual or 0.0, value or 0.0)
    return value or 0.0


def _wall(days: list[DayForecast], today: date, budget: float, pick) -> tuple[float, date | None]:
    total, wall = 0.0, None
    for d in days:
        total += _spent(d, today, pick(d)) + d.extra
        if wall is None and budget > 0 and total > budget:
            wall = d.day
    return total, wall


def _assemble(base: _Base, extra: list[tuple[date, float]]) -> Forecast:
    today = base.today
    added: dict[date, float] = {}
    for day, amount in extra:
        if day > base.period_end:
            continue  # after the next payday: not this period's money
        day = max(day, today)
        added[day] = added.get(day, 0.0) + float(amount)
    days = [replace(d, extra=added.get(d.day, 0.0)) for d in base.days]

    likely_total, wall_day = _wall(days, today, base.budget, lambda d: d.likely)
    low_total = high_total = wall_day_early = None
    if base.method == "tabpfn":
        low_total, _ = _wall(days, today, base.budget, lambda d: d.low)
        high_total, wall_day_early = _wall(days, today, base.budget, lambda d: d.high)

    by_day = {d.day: _spent(d, today, d.likely) for d in base.days}
    biggest, best = None, 0.0
    for day, name in base.reasons:
        window = sum(by_day.get(day - timedelta(days=i), 0.0) for i in range(SHOP_WINDOW + 1))
        if window > best:
            biggest, best = name, window

    spent_so_far = sum(d.actual or 0.0 for d in base.days if d.day <= today)
    return Forecast(
        today=today,
        period_start=base.period_start,
        period_end=base.period_end,
        budget=base.budget,
        spent_so_far=spent_so_far,
        likely_total=likely_total,
        low_total=low_total,
        high_total=high_total,
        wall_day=wall_day,
        wall_day_early=wall_day_early,
        biggest_reason=biggest,
        birthdays=base.birthdays,
        holidays=list(base.holidays),
        days=days,
        method=base.method,
        note=base.note,
        extra=list(extra),
    )


async def run_forecast(engine: Engine, today: date, extra: list[tuple[date, float]] | None = None) -> Forecast:
    """Forecast the rest of the pay period from the ledger and the events.

    extra: money the user is thinking about spending, as (day, amount). It is added on top of
    the prediction for that day; TabPFN itself is not called again (its daily predictions are
    cached per day and per data fingerprint).
    """
    settings, series, events, fingerprint = await asyncio.to_thread(_load, engine)
    key = (id(engine), today, fingerprint)
    base = _cache.get(key)
    if base is None:
        base = await _compute(settings, series, events, today)
        if len(_cache) >= _CACHE_SIZE:
            _cache.pop(next(iter(_cache)))
        _cache[key] = base
    return _assemble(base, extra or [])


# ---------------------------------------------------------------- replies


def _day(d: date) -> str:
    return f"{d:%b} {d.day}"


def _join(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _money(amount: float, currency: str) -> str:
    return format_money(round_to(amount), currency)


def format_forecast(f: Forecast, currency: str = "₺") -> str:
    """The /forecast reply (plain text with emoji)."""
    lines = []
    calendar_parts = []
    if f.birthdays:
        calendar_parts.append(f"🎂 {f.birthdays} birthday{'s' if f.birthdays != 1 else ''}")
    for name in dict.fromkeys(f.holidays):
        kind = next((k for k, v in hol.NAMES.items() if v == name), "")
        calendar_parts.append(f"{hol.EMOJI.get(kind, '📅')} {name}")
    if calendar_parts:
        lines.append(f"{_join(calendar_parts)} before your next payday.")

    likely = f"💸 Likely spending: {_money(f.likely_total, currency)}"
    if f.low_total is not None and f.high_total is not None:
        likely += f" ({_money(f.low_total, currency)}–{_money(f.high_total, currency)})"
    if f.budget <= 0:
        lines.append(likely + ". Send /start to set your budget.")
        return "\n".join(lines + ([f.note] if f.note else []))
    lines.append(likely + f". Your budget: {format_money(f.budget, currency)}.")
    if f.spent_so_far > 0:
        lines.append(f"🧾 Spent so far: {format_money(f.spent_so_far, currency)}.")

    if f.wall_day and f.wall_day <= f.today and f.spent_so_far > f.budget:
        lines.append(f"🧱 You passed your budget on {_day(f.wall_day)}.")
    elif f.wall_day:
        wall = f"🧱 Wall Day: around {_day(f.wall_day)}."
        if f.wall_day_early and f.wall_day_early < f.wall_day:
            wall += f" Maybe as early as {_day(f.wall_day_early)}."
        lines.append(wall)
    elif f.wall_day_early:
        lines.append(f"✅ You likely stay under budget this period. In a bad case, Wall Day is {_day(f.wall_day_early)}.")
    else:
        lines.append("✅ You stay under budget this period.")
    if f.biggest_reason:
        lines.append(f"Biggest reason: {f.biggest_reason}.")
    if f.note:
        lines.append(f.note)
    return "\n".join(lines)


def _article(amount: float) -> str:
    """'An' for amounts read aloud starting with a vowel sound (8, 11, 18, 80-89, 8,000...)."""
    digits = str(int(round(amount)))
    if digits.startswith("8") or (digits[:2] in ("11", "18") and len(digits) % 3 == 2):
        return "An"
    return "A"


def format_afford(base: Forecast, with_extra: Forecast, amount: float, on: date, about: str | None, currency: str = "₺") -> str:
    """One or two lines: can I afford it, and how Wall Day moves."""
    money = format_money(amount, currency)
    what = f"{_article(amount)} {money} {about.strip()}" if about and about.strip() else f"Spending {money}"
    when = _day(on)
    if on > base.period_end:
        return f"{what} on {when} comes after your next payday, so this pay period does not change."
    likely = _money(with_extra.likely_total, currency)
    budget = format_money(base.budget, currency)
    if base.budget <= 0:
        return f"{what} on {when} makes likely spending {likely}. Send /start to set your budget."
    if with_extra.wall_day is None:
        return f"Yes. With {money} on {when} you still stay under budget (likely {likely} of {budget})."
    becomes = f"Likely spending becomes {likely} of your {budget} budget."
    if base.wall_day is None:
        return f"{what} on {when} puts you over budget. Wall Day: around {_day(with_extra.wall_day)}. {becomes}"
    if with_extra.wall_day < base.wall_day:
        return f"{what} on {when} moves Wall Day from {_day(base.wall_day)} to {_day(with_extra.wall_day)}. {becomes}"
    return f"{what} on {when} keeps Wall Day at {_day(with_extra.wall_day)}. {becomes}"
