"""Forecast chart (PNG): money spent so far, the likely path with its low-high range, the budget
line and Wall Day. Drawn with Pillow so the server stays small."""

from __future__ import annotations

import io

from app.forecast import Forecast
from app.money import format_money, round_to

W, H = 1200, 760
LEFT, RIGHT, TOP, BOTTOM = 130, 50, 120, 110
INK = (34, 34, 34)
MUTED = (120, 120, 120)
GRID = (230, 230, 230)
SPENT = (40, 40, 40)
LIKELY = (31, 111, 186)
BAND = (205, 224, 243)
BUDGET = (200, 60, 50)


def _font(size: int):
    from PIL import ImageFont

    return ImageFont.load_default(size=size)  # same look on every machine


_CODES = {"₺": "TL", "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR", "﷼": "IRR"}


def _money(value: float) -> str:
    from app.config import get_config

    currency = get_config().currency
    if currency in _CODES:  # Pillow's built-in font has no glyph for these signs
        return format_money(round_to(value), currency="") + " " + _CODES[currency]
    return format_money(round_to(value), currency)


def render_chart(f: Forecast) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    days = f.days
    if not days:
        return b""

    # Cumulative paths. Days up to today use actual spending; later days use the forecast.
    spent, likely, low, high = [], [], [], []
    run_l = run_lo = run_hi = 0.0
    for day in days:
        if day.actual is not None and day.day < f.today:
            run_l += day.actual
            run_lo += day.actual
            run_hi += day.actual
            spent.append(run_l)
        else:
            base = max(day.actual or 0.0, day.likely)
            run_l += base + day.extra
            run_lo += max(day.actual or 0.0, day.low if day.low is not None else day.likely) + day.extra
            run_hi += max(day.actual or 0.0, day.high if day.high is not None else day.likely) + day.extra
        likely.append(run_l)
        low.append(run_lo)
        high.append(run_hi)

    top_value = max(max(high), f.budget) * 1.12
    n = len(days)

    def x(i: float) -> float:
        return LEFT + (W - LEFT - RIGHT) * (i / max(n - 1, 1))

    def y(v: float) -> float:
        return H - BOTTOM - (H - TOP - BOTTOM) * (v / top_value)

    # Title
    title = f"{days[0].day:%b %-d} to {days[-1].day:%b %-d}: likely spending vs budget"
    d.text((LEFT, 36), title, fill=INK, font=_font(34))
    from app.config import get_config

    demo = "Made-up demo household. " if get_config().demo_today else ""
    d.text((LEFT, 80), f"{demo}Today is {f.today:%b %-d}.", fill=MUTED, font=_font(22))

    # Grid and y labels
    step = max(5000, round_to(top_value / 5, 5000))
    v = 0
    while v <= top_value:
        d.line([(LEFT, y(v)), (W - RIGHT, y(v))], fill=GRID, width=1)
        d.text((LEFT - 12, y(v)), _money(v), fill=MUTED, font=_font(20), anchor="rm")
        v += step
    for i, day in enumerate(days):
        if day.day.day in (1, 7, 14, 21, 28) or i == n - 1:
            d.text((x(i), H - BOTTOM + 14), f"{day.day:%b %-d}", fill=MUTED, font=_font(20), anchor="mt")

    # Low-high band and likely path (from today on)
    start = next((i for i, day in enumerate(days) if day.day >= f.today), 0)
    band = [(x(i), y(high[i])) for i in range(start, n)] + [(x(i), y(low[i])) for i in range(n - 1, start - 1, -1)]
    if len(band) >= 3 and f.low_total is not None:
        d.polygon(band, fill=BAND)
    d.line([(x(i), y(likely[i])) for i in range(max(start - 1, 0), n)], fill=LIKELY, width=5)
    if start > 0:
        d.line([(x(i), y(likely[i])) for i in range(0, start)], fill=SPENT, width=5)

    # Budget line
    d.line([(LEFT, y(f.budget)), (W - RIGHT, y(f.budget))], fill=BUDGET, width=3)
    d.text((W - RIGHT, y(f.budget) - 10), f"Budget {_money(f.budget)}", fill=BUDGET, font=_font(22), anchor="rb")

    # Wall Day
    if f.wall_day:
        i = next(i for i, day in enumerate(days) if day.day == f.wall_day)
        d.line([(x(i), TOP), (x(i), H - BOTTOM)], fill=BUDGET, width=2)
        d.ellipse([x(i) - 9, y(f.budget) - 9, x(i) + 9, y(f.budget) + 9], fill=BUDGET)
        d.text((x(i) - 12, TOP + 6), f"Wall Day: {f.wall_day:%b %-d}", fill=BUDGET, font=_font(26), anchor="ra")

    # Legend
    ly = H - 44
    d.line([(LEFT, ly), (LEFT + 40, ly)], fill=LIKELY, width=5)
    d.text((LEFT + 50, ly), "likely", fill=INK, font=_font(20), anchor="lm")
    if f.low_total is not None:
        d.rectangle([LEFT + 140, ly - 9, LEFT + 180, ly + 9], fill=BAND)
        d.text((LEFT + 190, ly), "low to high", fill=INK, font=_font(20), anchor="lm")

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()
