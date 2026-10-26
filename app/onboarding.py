"""/start: two questions, budget and payday. Currency and timezone come from env defaults.

Send /start again to change them.
"""

from __future__ import annotations

import re

from app.context import Ctx
from app.db import get_settings, update_settings
from app.money import format_cents, parse_amount, to_cents

WELCOME = "Hi, I'm Ledgerly 🦥 I file your receipts and tell you the day your budget runs out, before it happens."
ASK_BUDGET = "What is your monthly budget? For example: 30000. Not in {currency}? Add your sign, like $3,000 or 3000 EUR."
ASK_BUDGET_AGAIN = "I need a number above zero. What is your monthly budget? For example: 30000, or $3,000."
ASK_PAYDAY = "Which day of the month is payday? (1-28)"
ASK_PAYDAY_AGAIN = "Please send a day from 1 to 28. For example: 1, or the 15th."
NAP_TIP = (
    "Good to know: I live on a free server that naps after 15 quiet minutes. "
    "After a nap, my first answer can take about a minute. Your message is not lost."
)
NEXT_STEPS = (
    "What now:\n"
    "📸 Send me a photo of a receipt.\n"
    "📅 Tell me about family events, like: Arman's birthday is Dec 14.\n"
    "🧱 Send /forecast to see your Wall Day."
)

# The budget answer must be just an amount ("30000", "₺30,000", "$30,000", "30k", "3,000 TL",
# "30000 EUR"), so a sentence such as "Arman's birthday is Dec 14" is never read as a budget of 14.
_UNIT = r"(?:[₺$€£¥₹﷼]|tl|try|usd|eur|gbp|irr|irt|lira|dollars?|euros?|pounds?|toman|rials?)"
_BUDGET = re.compile(rf"^(?P<pre>{_UNIT})?\s*\d[\d.,' ]*\s*k?\s*(?P<post>{_UNIT})?[.!]?$", re.IGNORECASE)
# The sign the bot shows for each way of writing a currency.
_SIGN = {
    "₺": "₺", "tl": "₺", "try": "₺", "lira": "₺",
    "$": "$", "usd": "$", "dollar": "$", "dollars": "$",
    "€": "€", "eur": "€", "euro": "€", "euros": "€",
    "£": "£", "gbp": "£", "pound": "£", "pounds": "£",
    "¥": "¥", "₹": "₹",
    "﷼": "﷼", "irr": "﷼", "irt": "﷼", "rial": "﷼", "rials": "﷼", "toman": "﷼",
}


def currency_in(text: str) -> str | None:
    """The currency sign written in a budget answer ("$3,000" -> "$"), or None if there is none."""
    match = _BUDGET.match(text.strip())
    unit = match and (match.group("pre") or match.group("post"))
    return _SIGN.get(unit.lower()) if unit else None
_DAY = re.compile(r"^(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?(?:\s+of\s+the\s+month)?[.!]?$")


def ordinal(day: int) -> str:
    if 11 <= day % 100 <= 13:
        return f"{day}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def parse_payday(text: str) -> int | None:
    match = _DAY.match(text.strip().lower())
    if not match:
        return None
    day = int(match.group(1))
    return day if 1 <= day <= 28 else None


async def handle_start(ctx: Ctx) -> list[str]:
    update_settings(ctx.engine, step="budget")
    return [f"{WELCOME}\n\n{ASK_BUDGET.format(currency=ctx.cfg.currency)}"]


async def handle_answer(ctx: Ctx, text: str) -> list[str] | None:
    """If onboarding is waiting for an answer, handle it. Else None."""
    step = get_settings(ctx.engine).get("step")
    if step == "budget":
        amount = parse_amount(text) if _BUDGET.match(text.strip()) else None
        if amount is None or amount <= 0:
            return [ASK_BUDGET_AGAIN]
        sign = currency_in(text)
        values = {"budget_cents": to_cents(amount), "step": "payday"}
        if sign:
            values["currency"] = sign
        update_settings(ctx.engine, **values)
        return [ASK_PAYDAY]
    if step == "payday":
        day = parse_payday(text)
        if day is None:
            return [ASK_PAYDAY_AGAIN]
        update_settings(ctx.engine, payday=day, step=None)
        saved = get_settings(ctx.engine)
        budget = format_cents(saved["budget_cents"], saved.get("currency") or ctx.cfg.currency)
        return [f"All set. Budget {budget}, payday on the {ordinal(day)}.\n\n{NEXT_STEPS}\n\n{NAP_TIP}"]
    return None
