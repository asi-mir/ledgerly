"""/start: two questions, budget and payday. Currency and timezone come from env defaults.

Send /start again to change them.
"""

from __future__ import annotations

import re

from app.context import Ctx
from app.db import get_settings, update_settings
from app.money import format_cents, parse_amount, to_cents

WELCOME = "Hi, I'm Ledgerly 🦥 I file your receipts and tell you the day your budget runs out, before it happens."
ASK_BUDGET = "What is your monthly budget? For example: 30000"
ASK_BUDGET_AGAIN = "I need a number above zero. What is your monthly budget? For example: 30000"
ASK_PAYDAY = "Which day of the month is payday? (1-28)"
ASK_PAYDAY_AGAIN = "Please send a day from 1 to 28. For example: 1, or the 15th."
NEXT_STEPS = (
    "What now:\n"
    "📸 Send me a photo of a receipt.\n"
    "📅 Tell me about family events, like: Arman's birthday is Dec 14.\n"
    "🧱 Send /forecast to see your Wall Day."
)

# The budget answer must be just an amount ("30000", "₺30,000", "30k"), so a sentence such as
# "Arman's birthday is Dec 14" is never read as a budget of 14.
_BUDGET = re.compile(r"^(?:₺|tl)?\s*\d[\d.,' ]*\s*(?:k)?\s*(?:₺|tl|try|lira)?[.!]?$", re.IGNORECASE)
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
    return [f"{WELCOME}\n\n{ASK_BUDGET}"]


async def handle_answer(ctx: Ctx, text: str) -> list[str] | None:
    """If onboarding is waiting for an answer, handle it. Else None."""
    step = get_settings(ctx.engine).get("step")
    if step == "budget":
        amount = parse_amount(text) if _BUDGET.match(text.strip()) else None
        if amount is None or amount <= 0:
            return [ASK_BUDGET_AGAIN]
        update_settings(ctx.engine, budget_cents=to_cents(amount), step="payday")
        return [ASK_PAYDAY]
    if step == "payday":
        day = parse_payday(text)
        if day is None:
            return [ASK_PAYDAY_AGAIN]
        update_settings(ctx.engine, payday=day, step=None)
        budget = format_cents(get_settings(ctx.engine)["budget_cents"], ctx.cfg.currency)
        return [f"All set. Budget {budget}, payday on the {ordinal(day)}.\n\n{NEXT_STEPS}"]
    return None
