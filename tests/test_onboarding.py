import asyncio

import pytest

from app import onboarding
from app.db import get_settings


def run(coro):
    return asyncio.run(coro)


def test_start_asks_budget(ctx):
    replies = run(onboarding.handle_start(ctx))
    assert len(replies) == 1
    assert "Ledgerly 🦥" in replies[0]
    assert "What is your monthly budget?" in replies[0]
    assert get_settings(ctx.engine)["step"] == "budget"


def test_full_flow(ctx):
    run(onboarding.handle_start(ctx))
    assert run(onboarding.handle_answer(ctx, "30000")) == ["Which day of the month is payday? (1-28)"]
    assert get_settings(ctx.engine)["budget_cents"] == 3_000_000
    reply = run(onboarding.handle_answer(ctx, "1st"))[0]
    assert reply.startswith("All set. Budget ₺30,000, payday on the 1st.")
    assert "/forecast" in reply and "receipt" in reply
    s = get_settings(ctx.engine)
    assert s["payday"] == 1 and s["step"] is None


def test_not_onboarding_returns_none(ctx):
    assert run(onboarding.handle_answer(ctx, "hello")) is None


@pytest.mark.parametrize("text", ["abc", "0", "-500", ""])
def test_invalid_budget_asks_again(ctx, text):
    run(onboarding.handle_start(ctx))
    reply = run(onboarding.handle_answer(ctx, text))[0]
    assert "monthly budget" in reply
    assert get_settings(ctx.engine)["step"] == "budget"


@pytest.mark.parametrize("text", ["₺30,000", "30k", "30.000"])
def test_budget_formats(ctx, text):
    run(onboarding.handle_start(ctx))
    run(onboarding.handle_answer(ctx, text))
    assert get_settings(ctx.engine)["budget_cents"] == 3_000_000


@pytest.mark.parametrize(("text", "day"), [("1", 1), ("1st", 1), ("the 15th", 15), ("2nd", 2), ("28", 28)])
def test_payday_formats(text, day):
    assert onboarding.parse_payday(text) == day


@pytest.mark.parametrize("text", ["0", "29", "31st", "monday", "1 2"])
def test_invalid_payday(ctx, text):
    run(onboarding.handle_start(ctx))
    run(onboarding.handle_answer(ctx, "30000"))
    reply = run(onboarding.handle_answer(ctx, text))[0]
    assert "1 to 28" in reply
    assert get_settings(ctx.engine)["step"] == "payday"


def test_start_again_changes_budget(ctx):
    for budget in ("30000", "45000"):
        run(onboarding.handle_start(ctx))
        run(onboarding.handle_answer(ctx, budget))
        reply = run(onboarding.handle_answer(ctx, "the 15th"))[0]
    assert reply.startswith("All set. Budget ₺45,000, payday on the 15th.")


@pytest.mark.parametrize(("day", "text"), [(1, "1st"), (2, "2nd"), (3, "3rd"), (11, "11th"), (21, "21st"), (22, "22nd")])
def test_ordinal(day, text):
    assert onboarding.ordinal(day) == text
