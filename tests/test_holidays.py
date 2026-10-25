from datetime import date, timedelta

from app.holidays import HOLIDAYS, find_holiday, holiday_on, holidays_between, next_holiday


def test_yalda_is_the_evening_of_dec_21():
    yalda = [h.day for h in HOLIDAYS if h.kind == "yalda"]
    assert yalda == [date(2025, 12, 21), date(2026, 12, 21), date(2027, 12, 21)]


def test_four_holidays_each_year_2025_to_2027():
    for year in (2025, 2026, 2027):
        kinds = sorted(h.kind for h in HOLIDAYS if h.day.year == year)
        assert kinds == ["chaharshanbe_suri", "nowruz", "sizdah_bedar", "yalda"]


def test_nowruz_and_sizdah_bedar():
    assert [h.day for h in HOLIDAYS if h.kind == "nowruz"] == [date(2025, 3, 21), date(2026, 3, 21), date(2027, 3, 21)]
    for h in HOLIDAYS:
        if h.kind == "sizdah_bedar":
            assert h.day == next(n.day for n in HOLIDAYS if n.kind == "nowruz" and n.day.year == h.day.year) + timedelta(days=12)


def test_chaharshanbe_suri_is_the_last_tuesday_evening_before_nowruz():
    for h in HOLIDAYS:
        if h.kind == "chaharshanbe_suri":
            nowruz = next(n.day for n in HOLIDAYS if n.kind == "nowruz" and n.day.year == h.day.year)
            assert h.day.weekday() == 1  # Tuesday evening, the eve of the last Wednesday
            assert 1 <= (nowruz - h.day).days <= 7


def test_holidays_between_is_inclusive_and_sorted():
    found = holidays_between(date(2026, 3, 17), date(2026, 4, 2))
    assert [h.name for h in found] == ["Chaharshanbe Suri", "Nowruz", "Sizdah Bedar"]
    assert holidays_between(date(2026, 5, 1), date(2026, 11, 30)) == []


def test_find_holiday_by_name():
    assert find_holiday("yalda", date(2026, 12, 1)).day == date(2026, 12, 21)
    assert find_holiday("Yalda Night", date(2026, 12, 22)).day == date(2027, 12, 21)
    assert find_holiday("nowruz", date(2026, 3, 22)).day == date(2027, 3, 21)
    assert find_holiday("christmas", date(2026, 1, 1)) is None


def test_holiday_on_and_next_holiday():
    assert holiday_on(date(2026, 12, 21)).name == "Yalda Night"
    assert holiday_on(date(2026, 12, 20)) is None
    assert next_holiday(date(2026, 12, 1)).day == date(2026, 12, 21)
    assert next_holiday(date(2028, 1, 1)) is None
