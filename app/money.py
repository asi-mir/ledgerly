"""Money helpers. Amounts are stored as integer cents; the bot shows them in English style (₺1,234.50)."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_NUMBER = re.compile(r"-?\d[\d.,' ]*")


def parse_amount(text: str | int | float | None) -> Decimal | None:
    """Read an amount the way people and receipts write it.

    Turkish receipts write 1.234,56 (dot = thousands, comma = decimals). People in chat may
    write 1,234.56, 30000, 30.000 or 30k. A dot or comma followed by exactly 3 digits at the
    end is a thousands separator; followed by 1-2 digits it is the decimal mark.
    """
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return Decimal(str(text)).quantize(Decimal("0.01"), ROUND_HALF_UP)
    raw = str(text).strip().lower().replace("₺", "").replace("tl", "").replace("try", "")
    for dash in ("\u2212", "\u2013", "\u2014"):  # minus sign, en dash, em dash
        raw = raw.replace(dash, "-")
    if re.match(r"^\s*\d[\d.,' ]*-\s*$", raw):  # discount printed as "12,50-"
        raw = "-" + raw.strip().rstrip("-")
    multiplier = Decimal(1)
    if re.search(r"\d\s*k\b", raw):
        multiplier = Decimal(1000)
        raw = re.sub(r"(\d)\s*k\b", r"\1", raw)
    match = _NUMBER.search(raw)
    if not match:
        return None
    s = match.group(0).strip().replace(" ", "").replace("'", "")
    negative = s.startswith("-")
    s = s.lstrip("-")
    last_dot, last_comma = s.rfind("."), s.rfind(",")
    if last_dot >= 0 and last_comma >= 0:
        decimal_mark = "." if last_dot > last_comma else ","
        thousands = "," if decimal_mark == "." else "."
        s = s.replace(thousands, "").replace(decimal_mark, ".")
    elif last_dot >= 0 or last_comma >= 0:
        mark = "." if last_dot >= 0 else ","
        head, _, tail = s.rpartition(mark)
        if s.count(mark) > 1 or len(tail) == 3:
            s = s.replace(mark, "")  # thousands separators only
        else:
            s = head.replace(mark, "") + "." + tail
    try:
        value = Decimal(s) * multiplier
    except InvalidOperation:
        return None
    if negative:
        value = -value
    return value.quantize(Decimal("0.01"), ROUND_HALF_UP)


def to_cents(amount: Decimal | float | int) -> int:
    return int((Decimal(str(amount)) * 100).quantize(Decimal(1), ROUND_HALF_UP))


def format_money(amount: Decimal | float | int, currency: str = "₺", decimals: bool | None = None) -> str:
    """₺30,000 for whole amounts, ₺1,234.50 otherwise (or force with decimals=True/False)."""
    value = Decimal(str(amount)).quantize(Decimal("0.01"), ROUND_HALF_UP)
    whole = value == value.to_integral_value()
    show = (not whole) if decimals is None else decimals
    text = f"{abs(value):,.2f}" if show else f"{abs(value):,.0f}"
    return f"-{currency}{text}" if value < 0 else f"{currency}{text}"


def format_cents(cents: int, currency: str = "₺", decimals: bool | None = None) -> str:
    return format_money(Decimal(cents) / 100, currency, decimals)


def round_to(amount: float, step: int = 100) -> int:
    """Forecasts are estimates: show them rounded (default to the nearest 100)."""
    return int(Decimal(str(amount / step)).quantize(Decimal(1), ROUND_HALF_UP)) * step
