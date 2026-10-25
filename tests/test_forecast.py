"""Forecast tests with a fake predictor (offline). The real TabPFN check is in test_demo.py."""

import asyncio
import math
from datetime import date, timedelta

import pytest
from sqlalchemy import insert

from app import forecast as fc
from app.db import events, receipts, update_settings

DEC1 = date(2026, 12, 1)


@pytest.fixture(autouse=True)
def fresh_cache():
    fc.clear_cache()
    yield
    fc.clear_cache()


def add_days(engine, first: date, last: date, amount: float = 500.0) -> None:
    rows = []
    day = first
    while day <= last:
        rows.append({"date": day, "merchant": "Test Shop", "total_cents": int(amount * 100), "status": "filed", "source": "demo"})
        day += timedelta(days=1)
    with engine.begin() as conn:
        conn.execute(insert(receipts), rows)


def add_event(engine, day: date, title: str, kind: str, guests: int = 0, hosted: bool = False) -> None:
    with engine.begin() as conn:
        conn.execute(insert(events).values(date=day, title=title, kind=kind, guests=guests, hosted=hosted, source="chat"))


class FakePredictor:
    """Stands in for TabPFN: a fixed amount per day, optionally from the feature row."""

    def __init__(self, per_day=1200.0, quantiles=True, fail=False):
        self.per_day, self.quantiles, self.fail, self.calls = per_day, quantiles, fail, 0
        self.shapes = None

    def __call__(self, X_train, y_train, X_future):
        self.calls += 1
        self.shapes = (len(X_train), len(X_train[0]), len(X_future))
        if self.fail:
            raise RuntimeError("TabPFN is down")
        mid = [self.per_day(row) if callable(self.per_day) else self.per_day for row in X_future]
        if not self.quantiles:
            return None, mid, None
        return [m * 0.8 for m in mid], mid, [m * 1.3 for m in mid]


def run(engine, today=DEC1, extra=None):
    return asyncio.run(fc.run_forecast(engine, today, extra))


@pytest.fixture
def household(engine):
    update_settings(engine, budget_cents=3_000_000, payday=1)
    add_days(engine, date(2026, 9, 1), date(2026, 11, 30))
    return engine


# ---------------------------------------------------------------- pay period


def test_pay_period():
    assert fc.pay_period(DEC1, 1) == (date(2026, 12, 1), date(2026, 12, 31))
    assert fc.pay_period(date(2026, 12, 10), 15) == (date(2026, 11, 15), date(2026, 12, 14))
    assert fc.pay_period(date(2026, 12, 15), 15) == (date(2026, 12, 15), date(2027, 1, 14))
    assert fc.pay_period(date(2027, 1, 5), 10) == (date(2026, 12, 10), date(2027, 1, 9))
    assert fc.pay_period(date(2027, 2, 28), 28) == (date(2027, 2, 28), date(2027, 3, 27))


# ---------------------------------------------------------------- features


def col(name):
    return fc.FEATURES.index(name)


def test_event_and_holiday_columns():
    evs = [
        {"date": date(2026, 12, 14), "title": "Arman's birthday", "kind": "birthday", "guests": 0, "hosted": False},
        {"date": date(2026, 12, 21), "title": "Yalda Night", "kind": "hosting", "guests": 12, "hosted": True},
    ]
    days = [date(2026, 12, 13), date(2026, 12, 14), date(2026, 12, 19), date(2026, 12, 21)]
    rows = fc.build_rows(days, {}, evs, 1, date(2026, 9, 1), freeze=DEC1)
    dec13, dec14, dec19, dec21 = rows
    assert dec13[col("birthday_next_3")] == 1 and dec13[col("birthday_today")] == 0
    assert dec13[col("days_until_birthday")] == 1
    assert dec14[col("birthday_today")] == 1 and dec14[col("birthdays_next_7")] == 1
    assert dec19[col("guests_next_3")] == 12 and dec19[col("days_until_hosting")] == 2
    assert dec19[col("holiday_type")] == 1 and dec19[col("days_until_holiday")] == 2
    assert dec21[col("hosting_today")] == 1 and dec21[col("guests_today")] == 12
    assert dec21[col("holiday_today")] == 1
    assert dec14[col("days_since_payday")] == 13 and dec14[col("days_until_payday")] == 18
    assert dec19[col("weekend")] == 1 and dec19[col("day_of_week")] == 5
    assert len(dec13) == len(fc.FEATURES)


def test_future_rows_never_see_spending_after_the_freeze_date():
    series = {date(2026, 11, 1) + timedelta(days=i): 400.0 for i in range(30)}
    days = [DEC1 + timedelta(days=i) for i in range(31)]
    before = fc.build_rows(days, series, [], 1, date(2026, 11, 1), freeze=DEC1)
    leaked = dict(series)
    for day in days:
        leaked[day] = 99_999.0  # the future: must not change any row
    after = fc.build_rows(days, leaked, [], 1, date(2026, 11, 1), freeze=DEC1)
    assert before == after
    assert before[0][col("spent_last_7")] == pytest.approx(2800)
    assert before[-1][col("spent_last_30")] == before[0][col("spent_last_30")]  # frozen for every future day


def test_training_rows_are_frozen_at_their_own_pay_period_start():
    first = date(2026, 9, 1)
    series = {first + timedelta(days=i): 300.0 for i in range(91)}
    _, X, _ = fc.training_table(series, [], 1, first, DEC1)
    changed = dict(series)
    for i in range(30):
        changed[date(2026, 11, 1) + timedelta(days=i)] = 5000.0  # November spending changes
    _, X2, _ = fc.training_table(changed, [], 1, first, DEC1)
    november = [i for i in range(len(X)) if (first + timedelta(days=i)).month == 11]
    for i in november:  # November rows only know October
        assert X[i][col("spent_last_30")] == X2[i][col("spent_last_30")]
    assert all(math.isnan(X[i][col("spent_last_7")]) for i in range(30))  # the first period has no history


# ---------------------------------------------------------------- forecast


def test_wall_day_is_the_first_day_past_the_budget(household):
    fake = FakePredictor(per_day=1200.0)
    fc._predict, real = fake, fc._predict
    try:
        f = run(household)
    finally:
        fc._predict = real
    assert f.method == "tabpfn"
    assert (f.period_start, f.period_end) == (DEC1, date(2026, 12, 31))
    assert f.likely_total == pytest.approx(31 * 1200)
    assert f.wall_day == date(2026, 12, 26)  # 25 days = exactly 30,000, not past it yet
    assert f.wall_day_early == date(2026, 12, 20)  # 1,560 a day
    assert f.low_total == pytest.approx(31 * 960)
    assert fake.shapes == (91, len(fc.FEATURES), 31)


def test_today_counts_max_of_actual_and_likely(household):
    add_days(household, DEC1, DEC1, amount=29_000)
    fake = FakePredictor(per_day=1200.0)
    fc._predict, real = fake, fc._predict
    try:
        f = run(household)
    finally:
        fc._predict = real
    assert f.spent_so_far == pytest.approx(29_000)
    assert f.likely_total == pytest.approx(29_000 + 30 * 1200)
    assert f.wall_day == date(2026, 12, 2)


def test_extra_spending_uses_the_cache_and_moves_wall_day(household):
    fake = FakePredictor(per_day=1000.0)
    fc._predict, real = fake, fc._predict
    try:
        base = run(household)
        again = run(household)
        gift = run(household, extra=[(date(2026, 12, 14), 5000)])
    finally:
        fc._predict = real
    assert fake.calls == 1
    assert base.wall_day == date(2026, 12, 31) and again.wall_day == base.wall_day
    assert gift.wall_day == date(2026, 12, 26)
    assert gift.likely_total == pytest.approx(base.likely_total + 5000)
    assert gift.extra == [(date(2026, 12, 14), 5000)]
    text = fc.format_afford(base, gift, 5000, date(2026, 12, 14), "gift for Arman")
    assert text == ("A ₺5,000 gift for Arman on Dec 14 moves Wall Day from Dec 31 to Dec 26. "
                    "Likely spending becomes ₺36,000 of your ₺30,000 budget.")


def test_new_receipt_invalidates_the_cache(household):
    fake = FakePredictor(per_day=500.0)
    fc._predict, real = fake, fc._predict
    try:
        run(household)
        add_days(household, date(2026, 11, 30), date(2026, 11, 30), amount=100)
        run(household)
    finally:
        fc._predict = real
    assert fake.calls == 2


def test_biggest_reason_is_the_event_with_the_most_spending_before_it(household):
    add_event(household, date(2026, 12, 14), "Arman's birthday", "birthday")
    add_event(household, date(2026, 12, 21), "Yalda Night", "hosting", guests=12, hosted=True)
    add_event(household, date(2026, 12, 9), "Sara's birthday", "birthday")

    def per_day(row):
        return 400 + 300 * row[col("guests_next_3")] + 2000 * row[col("birthday_next_3")]

    fake = FakePredictor(per_day=per_day)
    fc._predict, real = fake, fc._predict
    try:
        f = run(household)
    finally:
        fc._predict = real
    assert f.biggest_reason == "Yalda Night"
    assert f.birthdays == 2
    assert f.holidays == ["Yalda Night"]


def test_quantiles_fail_median_only(household):
    fc._predict, real = FakePredictor(per_day=1200.0, quantiles=False), fc._predict
    try:
        f = run(household)
    finally:
        fc._predict = real
    assert f.method == "tabpfn-median"
    assert f.low_total is None and f.high_total is None and f.wall_day_early is None
    assert f.wall_day == date(2026, 12, 26)
    assert "(" not in fc.format_forecast(f).splitlines()[0]


def test_tabpfn_error_falls_back_to_the_average(household):
    fc._predict, real = FakePredictor(fail=True), fc._predict
    try:
        f = run(household)
    finally:
        fc._predict = real
    assert f.method == "average" and f.note == fc.NOTE_FAILED
    assert f.likely_total == pytest.approx(31 * 500)
    assert f.wall_day is None


def test_no_token_falls_back_to_the_average(household, monkeypatch):
    from app.config import get_config

    monkeypatch.setenv("TABPFN_TOKEN", "")
    get_config.cache_clear()
    f = run(household)
    get_config.cache_clear()
    assert f.method == "average" and f.note == fc.NOTE_FAILED


def test_fewer_than_30_days_uses_a_simple_average(engine):
    update_settings(engine, budget_cents=3_000_000, payday=1)
    add_days(engine, date(2026, 11, 15), date(2026, 11, 30), amount=600)
    fake = FakePredictor()
    fc._predict, real = fake, fc._predict
    try:
        f = run(engine)
    finally:
        fc._predict = real
    assert fake.calls == 0
    assert f.method == "average" and f.note == fc.NOTE_FEW
    assert f.likely_total == pytest.approx(31 * 600)
    assert fc.NOTE_FEW in fc.format_forecast(f)


def test_empty_ledger_does_not_crash(engine):
    f = run(engine)
    assert f.likely_total == 0 and f.wall_day is None
    assert "Send /start" in fc.format_forecast(f)


# ---------------------------------------------------------------- replies


def make(**kw):
    values = dict(
        today=DEC1, period_start=DEC1, period_end=date(2026, 12, 31), budget=30_000, spent_so_far=0,
        likely_total=38_040, low_total=33_020, high_total=44_260, wall_day=date(2026, 12, 20),
        wall_day_early=date(2026, 12, 17), biggest_reason="Yalda Night", birthdays=4, holidays=["Yalda Night"],
    )
    values.update(kw)
    return fc.Forecast(**values)


def test_format_forecast_hero():
    assert fc.format_forecast(make()) == (
        "🎂 4 birthdays and 🍉 Yalda Night before your next payday.\n"
        "💸 Likely spending: ₺38,000 (₺33,000–₺44,300). Your budget: ₺30,000.\n"
        "🧱 Wall Day: around Dec 20. Maybe as early as Dec 17.\n"
        "Biggest reason: Yalda Night."
    )


def test_format_forecast_under_budget():
    text = fc.format_forecast(make(likely_total=24_000, low_total=20_000, high_total=28_000, wall_day=None,
                                   wall_day_early=None, birthdays=1, holidays=[], biggest_reason=None))
    assert text == (
        "🎂 1 birthday before your next payday.\n"
        "💸 Likely spending: ₺24,000 (₺20,000–₺28,000). Your budget: ₺30,000.\n"
        "✅ You stay under budget this period."
    )


def test_format_forecast_already_over():
    f = make(today=date(2026, 12, 25), spent_so_far=31_000, wall_day=date(2026, 12, 22), wall_day_early=date(2026, 12, 20))
    assert "🧱 You passed your budget on Dec 22." in fc.format_forecast(f)


def test_format_afford_variants():
    base = make(likely_total=26_000, wall_day=None)
    ok = make(likely_total=27_000, wall_day=None)
    assert fc.format_afford(base, ok, 1000, date(2026, 12, 14), None) == (
        "Yes. With ₺1,000 on Dec 14 you still stay under budget (likely ₺27,000 of ₺30,000)."
    )
    over = make(likely_total=34_000, wall_day=date(2026, 12, 28))
    assert fc.format_afford(base, over, 8000, date(2026, 12, 14), "phone") == (
        "An ₺8,000 phone on Dec 14 puts you over budget. Wall Day: around Dec 28. "
        "Likely spending becomes ₺34,000 of your ₺30,000 budget."
    )
    same = make(likely_total=38_500)
    assert fc.format_afford(make(), same, 500, date(2026, 12, 28), "cake").startswith(
        "A ₺500 cake on Dec 28 keeps Wall Day at Dec 20."
    )
    assert "after your next payday" in fc.format_afford(make(), make(), 500, date(2027, 1, 3), "cake")


def test_an_event_told_twice_counts_once():
    yalda = {"date": date(2026, 12, 21), "title": "Yalda Night", "kind": "hosting", "guests": 10, "hosted": True}
    again = dict(yalda, guests=12, title="yalda night ")
    (row,) = fc.build_rows([date(2026, 12, 19)], {}, [yalda, again], 1, date(2026, 9, 1), freeze=DEC1)
    assert row[col("guests_next_3")] == 12
