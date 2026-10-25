"""Chat agent: Gemma is faked offline; the live test calls real Gemma on the demo sentences."""

import asyncio
import json
import statistics
import time
from datetime import date

import pytest

from app import agent, forecast, holidays
from sqlalchemy import select

from app.db import events, list_events, update_settings
from app.gemma import GemmaReply
from tests.helpers import live

ARMAN = "Arman's birthday is Dec 14. We're hosting Yalda for 12 people."
GIFT = "Can I afford a ₺5,000 gift for Arman?"
MONTH = "how much will I spend this month?"
YALDA_DAY = date(2026, 12, 21)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def gemma(monkeypatch):
    """Queue of fake Gemma replies (dicts become JSON). Records each prompt."""
    state = {"replies": [], "prompts": []}

    async def fake(prompt, image=None, mime_type="image/jpeg", *, purpose):
        assert purpose == "chat"
        state["prompts"].append(prompt)
        reply = state["replies"].pop(0)
        return GemmaReply(text=reply if isinstance(reply, str) else json.dumps(reply), model="fake")

    monkeypatch.setattr(agent, "ask_gemma", fake)
    return state


@pytest.fixture
def yalda(monkeypatch):
    def fake_find(name, after):
        if "yalda" in name.lower() and after <= YALDA_DAY:
            return holidays.Holiday(day=YALDA_DAY, name="Yalda Night", kind="yalda")
        return None

    monkeypatch.setattr(holidays, "find_holiday", fake_find)


@pytest.fixture
def fake_forecast(monkeypatch):
    calls = []

    async def fake_run(engine, today, extra=None):
        calls.append({"today": today, "extra": extra})
        return {"extra": extra}

    monkeypatch.setattr(forecast, "run_forecast", fake_run)
    monkeypatch.setattr(forecast, "format_forecast", lambda f, currency="₺": f"FORECAST {f['extra']}")
    monkeypatch.setattr(
        forecast,
        "format_afford",
        lambda base, with_extra, amount, on, about, currency="₺": f"AFFORD {amount} {on} {about} {with_extra['extra']}",
    )
    return calls


def intent(name, events=None, amount=None, spend_date=None, about=None):
    return {"intent": name, "events": events or [], "amount": amount, "spend_date": spend_date, "about": about}


def arman_and_yalda():
    return intent(
        "add_event",
        [
            {"title": "Arman's birthday", "kind": "birthday", "date": "2026-12-14", "guests": None, "hosted": False},
            {"title": "Yalda", "kind": "hosting", "date": None, "guests": 12, "hosted": True},
        ],
    )


def test_add_event_confirms_and_stores(ctx, gemma, yalda):
    gemma["replies"].append(arman_and_yalda())
    reply = run(agent.handle_text(ctx, ARMAN))[0]
    assert reply == (
        "Got it 📅\n"
        "🎂 Arman's birthday: Dec 14\n"
        "🍉 Yalda Night, hosting 12 guests: Dec 21\n"
        "Send /forecast to see your Wall Day."
    )
    rows = list_events(ctx.engine)
    assert [(r["title"], r["date"], r["kind"], r["guests"], r["hosted"], r["source"]) for r in rows] == [
        ("Arman's birthday", date(2026, 12, 14), "birthday", 0, False, "chat"),
        ("Yalda Night", YALDA_DAY, "hosting", 12, True, "chat"),
    ]


def test_yalda_date_comes_from_holidays_not_model(ctx, gemma, yalda):
    gemma["replies"].append(
        intent("add_event", [{"title": "Yalda", "kind": "hosting", "date": "2026-12-25", "guests": 12, "hosted": True}])
    )
    run(agent.handle_text(ctx, "Yalda at ours, 12 people"))
    assert list_events(ctx.engine)[0]["date"] == YALDA_DAY


def test_same_sentence_twice_does_not_duplicate(ctx, gemma, yalda):
    gemma["replies"] += [arman_and_yalda(), arman_and_yalda()]
    run(agent.handle_text(ctx, ARMAN))
    run(agent.handle_text(ctx, ARMAN))
    assert len(list_events(ctx.engine)) == 2


def test_dedupe_updates_guests(ctx, gemma, yalda):
    gemma["replies"] += [
        arman_and_yalda(),
        intent("add_event", [{"title": "Yalda Night", "kind": "hosting", "date": None, "guests": 15, "hosted": True}]),
    ]
    run(agent.handle_text(ctx, ARMAN))
    reply = run(agent.handle_text(ctx, "Actually 15 people for Yalda"))[0]
    assert "hosting 15 guests: Dec 21" in reply
    rows = [r for r in list_events(ctx.engine) if r["title"] == "Yalda Night"]
    assert len(rows) == 1 and rows[0]["guests"] == 15


def test_event_without_date_is_not_stored(ctx, gemma):
    gemma["replies"].append(
        intent("add_event", [{"title": "Sara's birthday", "kind": "birthday", "date": None, "guests": None, "hosted": False}])
    )
    reply = run(agent.handle_text(ctx, "Sara's birthday is coming"))[0]
    assert reply == "What date is Sara's birthday? Tell me like: Arman's birthday is Dec 14."
    assert list_events(ctx.engine) == []


def test_bad_model_date_counts_as_no_date(ctx, gemma):
    gemma["replies"].append(
        intent("add_event", [{"title": "Family dinner", "kind": "dinner", "date": "sometime", "guests": 6, "hosted": False}])
    )
    reply = run(agent.handle_text(ctx, "family dinner soon"))[0]
    assert reply.startswith("What date is Family dinner?")
    assert list_events(ctx.engine) == []


def test_past_birthday_moves_to_next_year(ctx, gemma):
    gemma["replies"].append(
        intent("add_event", [{"title": "Sara's birthday", "kind": "birthday", "date": "2026-03-02", "guests": None, "hosted": False}])
    )
    reply = run(agent.handle_text(ctx, "Sara's birthday is March 2"))[0]
    assert "🎂 Sara's birthday: Mar 2, 2027" in reply
    assert list_events(ctx.engine)[0]["date"] == date(2027, 3, 2)


def test_retry_once_on_bad_json(ctx, gemma, fake_forecast):
    update_settings(ctx.engine, budget_cents=3_000_000, payday=1)
    gemma["replies"] += ["Sure! Here you go", intent("forecast")]
    assert run(agent.handle_text(ctx, MONTH)) == ["FORECAST None"]
    assert len(gemma["prompts"]) == 2
    assert "ONLY that JSON object" in gemma["prompts"][1]


def test_two_bad_replies_give_help(ctx, gemma):
    gemma["replies"] += ["no json", {"intent": "dance"}]
    reply = run(agent.handle_text(ctx, "hmm"))[0]
    assert reply == agent.help_text()
    assert len(gemma["prompts"]) == 2


def test_help_is_fixed_text(ctx, gemma):
    gemma["replies"].append(intent("help"))
    assert run(agent.handle_text(ctx, "hi")) == [agent.help_text()]


def test_forecast_intent(ctx, gemma, fake_forecast):
    update_settings(ctx.engine, budget_cents=3_000_000, payday=1)
    gemma["replies"].append(intent("forecast"))
    assert run(agent.handle_text(ctx, MONTH)) == ["FORECAST None"]
    assert fake_forecast == [{"today": date(2026, 12, 1), "extra": None}]


def test_forecast_needs_budget(ctx, fake_forecast):
    assert run(agent.handle_forecast_command(ctx)) == ["Send /start first so I know your budget."]
    assert fake_forecast == []


def add_arman(ctx):
    with ctx.engine.begin() as conn:
        conn.execute(events.insert().values(date=date(2026, 12, 14), title="Arman's birthday", kind="birthday"))


def test_afford_links_known_event(ctx, gemma, fake_forecast):
    update_settings(ctx.engine, budget_cents=3_000_000, payday=1)
    add_arman(ctx)
    gemma["replies"].append(intent("afford", amount=5000, about="Arman"))
    reply = run(agent.handle_text(ctx, GIFT))[0]
    assert reply == "AFFORD 5000.0 2026-12-14 gift for Arman [(datetime.date(2026, 12, 14), 5000.0)]"
    assert [c["extra"] for c in fake_forecast] == [None, [(date(2026, 12, 14), 5000.0)]]
    assert "Arman's birthday (birthday): 2026-12-14" in gemma["prompts"][0]


def test_afford_amount_as_text_and_spend_date(ctx, gemma, fake_forecast):
    update_settings(ctx.engine, budget_cents=3_000_000, payday=1)
    gemma["replies"].append(intent("afford", amount="₺2,500", spend_date="2026-12-10", about="a new phone"))
    reply = run(agent.handle_text(ctx, "Can I buy a ₺2,500 phone on Dec 10?"))[0]
    assert reply.startswith("AFFORD 2500.0 2026-12-10 new phone")


def test_afford_defaults_to_today(ctx, gemma, fake_forecast):
    update_settings(ctx.engine, budget_cents=3_000_000, payday=1)
    gemma["replies"].append(intent("afford", amount=1000))
    reply = run(agent.handle_text(ctx, "Can I afford 1000?"))[0]
    assert reply.startswith("AFFORD 1000.0 2026-12-01 None")


def test_afford_without_amount_asks(ctx, gemma, fake_forecast):
    update_settings(ctx.engine, budget_cents=3_000_000, payday=1)
    gemma["replies"].append(intent("afford", about="a gift"))
    reply = run(agent.handle_text(ctx, "Can I afford a gift?"))[0]
    assert reply.startswith("How much is it?")
    assert fake_forecast == []


def test_afford_needs_budget(ctx, gemma, fake_forecast):
    gemma["replies"].append(intent("afford", amount=5000))
    assert run(agent.handle_text(ctx, GIFT)) == ["Send /start first so I know your budget."]


def test_link_event_ignores_generic_words():
    known = [{"title": "Sara's birthday", "date": date(2026, 12, 5)}, {"title": "Yalda Night", "date": YALDA_DAY}]
    assert agent.link_event("a birthday gift", known) is None
    assert agent.link_event("Yalda", known)["date"] == YALDA_DAY
    assert agent.link_event("sara's birthday", known)["title"] == "Sara's birthday"


def test_route_from_main(ctx, gemma, yalda):
    from app import main

    gemma["replies"].append(arman_and_yalda())
    replies = run(main.route(ctx, {"text": ARMAN}, "text"))
    assert replies[0].startswith("Got it 📅")


# ---------- live: real Gemma on the demo sentences ----------

LIVE_CASES = [
    (ARMAN, False),
    (GIFT, True),
    (MONTH, True),
]


def check_case(text, result):
    if text == ARMAN:
        assert result.intent == "add_event"
        by_kind = {e.kind: e for e in result.events}
        assert by_kind["birthday"].date == date(2026, 12, 14)
        assert "yalda" in by_kind["hosting"].title.lower()
        assert by_kind["hosting"].guests == 12 and by_kind["hosting"].hosted
    elif text == GIFT:
        assert result.intent == "afford"
        assert float(result.amount) == 5000
        assert "arman" in (result.about or "").lower()
    else:
        assert result.intent == "forecast"


@live
@pytest.mark.parametrize(("text", "arman_known"), LIVE_CASES)
def test_live_demo_sentences(ctx, text, arman_known):
    if arman_known:
        add_arman(ctx)
    known = agent.upcoming_events(ctx)

    async def three_times():
        out = []
        for _ in range(3):
            started = time.perf_counter()
            out.append((await agent.read_intent(ctx, text, known), time.perf_counter() - started))
        return out

    seconds = []
    for result, took in run(three_times()):
        assert result is not None
        check_case(text, result)
        seconds.append(took)
    print(f"{ctx.cfg.gemma_model} median {statistics.median(seconds):.2f}s for {text!r}")


def test_yalda_uses_real_holiday_table(ctx, gemma):
    gemma["replies"].append(arman_and_yalda())
    reply = run(agent.handle_text(ctx, ARMAN))[0]
    assert "🍉 Yalda Night, hosting 12 guests: Dec 21" in reply


def test_thing_from_text_for_the_afford_reply():
    assert agent.thing_from_text("Can I afford a ₺5,000 gift for Arman?") == "gift for Arman"
    assert agent.thing_from_text("can i afford 2000 TL flowers") == "flowers"
    assert agent.thing_from_text("Can I afford a 5k cake?") == "cake"
    assert agent.thing_from_text("is it ok to spend money") is None


def test_telling_a_birthday_again_never_moves_last_years_event(ctx, gemma):
    with ctx.engine.begin() as conn:
        conn.execute(events.insert().values(date=date(2025, 12, 14), title="Arman's birthday", kind="birthday", source="demo"))
    agent.save_event(ctx, "Arman's birthday", "birthday", date(2026, 12, 14), 0, False)
    agent.save_event(ctx, "Arman's birthday", "birthday", date(2026, 12, 14), 0, False)
    with ctx.engine.connect() as conn:
        rows = conn.execute(select(events.c.date, events.c.source).order_by(events.c.date)).all()
    assert [tuple(r) for r in rows] == [(date(2025, 12, 14), "demo"), (date(2026, 12, 14), "chat")]
