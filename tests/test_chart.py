from datetime import date, timedelta

from app.chart import render_chart
from app.forecast import DayForecast, Forecast


def test_chart_is_a_png_with_wall_day():
    start = date(2026, 12, 1)
    days = [DayForecast(day=start + timedelta(days=i), likely=1200.0, low=800.0, high=1800.0) for i in range(31)]
    f = Forecast(
        today=start, period_start=start, period_end=date(2026, 12, 31), budget=30000.0, spent_so_far=0.0,
        likely_total=37200.0, low_total=24800.0, high_total=55800.0, wall_day=date(2026, 12, 25),
        wall_day_early=date(2026, 12, 17), biggest_reason="Yalda Night", birthdays=4, days=days,
    )
    png = render_chart(f)
    assert png.startswith(b"\x89PNG")
    assert len(png) > 5000


def test_chart_without_days_is_empty():
    f = Forecast(
        today=date(2026, 12, 1), period_start=date(2026, 12, 1), period_end=date(2026, 12, 31), budget=1.0,
        spent_so_far=0.0, likely_total=0.0, low_total=None, high_total=None, wall_day=None, wall_day_early=None,
        biggest_reason=None, birthdays=0,
    )
    assert render_chart(f) == b""


def test_chart_money_label_follows_the_currency():
    from app import chart

    assert chart._money(30000, "$") == "$30,000"
    assert chart._money(30000, "€") == "30,000 EUR"
    assert chart._money(30000, "₺") == "30,000 TL"
