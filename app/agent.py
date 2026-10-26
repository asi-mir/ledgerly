"""Chat: one JSON-intent Gemma prompt; code routes to add_event or forecast(extra).

Gemma only reads the message. Every number and date the bot says comes from code:
the ledger, the events table, the hardcoded holidays and the forecast.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError, field_validator
from sqlalchemy import func, select

from app import tracing
from app.context import Ctx
from app.db import events, get_settings, list_events
from app.gemma import ask_gemma, extract_json
from app.money import format_money, parse_amount

log = logging.getLogger("ledgerly.agent")

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "chat_agent.md"
RETRY_NOTE = "\n\nYour last reply was not one valid JSON object of the shape above. Reply with ONLY that JSON object."

NEED_START = "Send /start first so I know your budget."
HOLIDAY_KEYS = ("yalda", "nowruz", "chaharshanbe", "sizdah")
KIND_EMOJI = {"birthday": "🎂", "hosting": "🏠", "dinner": "🍽️", "other": "📅"}
# Words that do not name a specific event, so they never link a question to an event.
GENERIC_WORDS = {
    "a", "an", "the", "for", "of", "to", "at", "my", "our", "his", "her", "s",
    "gift", "present", "birthday", "dinner", "party", "night", "hosting", "family", "day",
}


def help_text(currency: str = "₺") -> str:
    return (
        "I'm Ledgerly 🦥 Here is what I can do:\n"
        "📸 Send me a receipt photo, and I file every line.\n"
        "📅 Tell me about family events, like: Arman's birthday is Dec 14.\n"
        "🧱 Send /forecast to see your Wall Day.\n"
        f"💸 Ask me: Can I afford a {format_money(5000, currency)} gift for Arman?"
    )


def parse_day(value) -> date | None:
    """YYYY-MM-DD from the model. Anything unreadable counts as no date, so the bot asks."""
    if value in (None, "", "null"):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


class EventIn(BaseModel):
    title: str
    kind: str = "other"
    date: dt.date | None = None
    guests: int | None = None
    hosted: bool = False

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, value):
        value = str(value or "other").lower()
        return value if value in KIND_EMOJI else "other"

    @field_validator("date", mode="before")
    @classmethod
    def _date(cls, value):
        return parse_day(value)

    @field_validator("guests", mode="before")
    @classmethod
    def _guests(cls, value):
        try:
            return int(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    @field_validator("hosted", mode="before")
    @classmethod
    def _hosted(cls, value):
        return bool(value) if value is not None else False


class Intent(BaseModel):
    intent: Literal["add_event", "afford", "forecast", "help"]
    events: list[EventIn] = []
    amount: float | str | None = None
    spend_date: dt.date | None = None
    about: str | None = None

    @field_validator("events", mode="before")
    @classmethod
    def _events(cls, value):
        return value or []

    @field_validator("spend_date", mode="before")
    @classmethod
    def _spend_date(cls, value):
        return parse_day(value)


def upcoming_events(ctx: Ctx) -> list[dict]:
    return [e for e in list_events(ctx.engine) if e["date"] >= ctx.today]


def build_prompt(ctx: Ctx, text: str, known: list[dict]) -> str:
    lines = [f"- {e['title']} ({e['kind']}): {e['date'].isoformat()}" for e in known] or ["- none"]
    template = PROMPT_PATH.read_text(encoding="utf-8")
    return (
        template.replace("{today}", f"{ctx.today:%A}, {ctx.today.isoformat()}")
        .replace("{events}", "\n".join(lines))
        .replace("{message}", text.strip()[:1000])
    )


async def read_intent(ctx: Ctx, text: str, known: list[dict]) -> Intent | None:
    """Ask Gemma once, retry once on a bad reply. None if both replies are unusable."""
    prompt = build_prompt(ctx, text, known)
    for attempt in range(2):
        reply = await ask_gemma(prompt if attempt == 0 else prompt + RETRY_NOTE, purpose="chat")
        try:
            return Intent.model_validate(extract_json(reply.text))
        except (ValueError, ValidationError) as exc:
            log.warning("chat reply not usable (attempt %d): %s", attempt + 1, type(exc).__name__)
    tracing.capture_message("chat intent unreadable after retry", level="warning")
    return None


async def handle_text(ctx: Ctx, text: str) -> list[str]:
    with tracing.agent_span("ledgerly") as span:
        known = upcoming_events(ctx)
        intent = await read_intent(ctx, text, known)
        name = intent.intent if intent else "help"
        span.set_data("ledgerly.intent", name)
        if name == "add_event":
            with tracing.tool_span("add_event"):
                return [add_events(ctx, intent.events)]
        if name == "afford":
            return await afford(ctx, intent, known, text)
        if name == "forecast":
            return await handle_forecast_command(ctx)
        return [help_text(ctx.cfg.currency)]


# ---------- add_event ----------


def short_date(day: date, today: date) -> str:
    text = f"{day:%b} {day.day}"
    return text if day.year == today.year else f"{text}, {day.year}"


def resolve_event(ev: EventIn, today: date) -> tuple[str, date | None, str]:
    """Title, date and emoji. Holiday dates come from app.holidays, never from the model."""
    title, day, emoji = ev.title.strip() or "Event", ev.date, KIND_EMOJI[ev.kind]
    lowered = title.lower()
    for key in HOLIDAY_KEYS:
        if key in lowered:
            from app.holidays import find_holiday

            holiday = find_holiday(key, today)
            if holiday is not None:
                title, day = holiday.name, holiday.day
            if key == "yalda":
                title, emoji = "Yalda Night", "🍉"
            break
    if day is not None and ev.kind == "birthday" and day < today:
        try:
            day = day.replace(year=today.year + (1 if day.replace(year=today.year) < today else 0))
        except ValueError:  # Feb 29
            day = day.replace(year=today.year + 1, day=28)
    return title, day, emoji


SAME_EVENT_DAYS = 60  # the same title told again within this many days is the same event


def save_event(ctx: Ctx, title: str, kind: str, day: date, guests: int, hosted: bool) -> None:
    """Insert, or update the same UPCOMING event told again (same kind and date, or same title
    within 60 days). Past events are history for the forecast and are never changed."""
    values = {"title": title, "kind": kind, "date": day, "guests": guests, "hosted": hosted}
    near = (events.c.date >= day - dt.timedelta(days=SAME_EVENT_DAYS)) & (events.c.date <= day + dt.timedelta(days=SAME_EVENT_DAYS))
    with ctx.engine.begin() as conn:
        row = conn.execute(
            select(events.c.id)
            .where(events.c.date >= ctx.today)
            .where(((events.c.kind == kind) & (events.c.date == day)) | ((func.lower(events.c.title) == title.lower()) & near))
            .order_by(events.c.id)
        ).first()
        if row is None:
            conn.execute(events.insert().values(**values, source="chat"))
        else:
            conn.execute(events.update().where(events.c.id == row[0]).values(**values))


def add_events(ctx: Ctx, items: list[EventIn]) -> str:
    saved, asks = [], []
    for ev in items:
        title, day, emoji = resolve_event(ev, ctx.today)
        if day is None:
            asks.append(f"What date is {title}? Tell me like: Arman's birthday is Dec 14.")
            continue
        guests = ev.guests or 0
        save_event(ctx, title, ev.kind, day, guests, ev.hosted)
        detail = ""
        if ev.hosted:
            detail = f", hosting {guests} guests" if guests else ", hosting"
        elif guests:
            detail = f", {guests} people"
        saved.append(f"{emoji} {title}{detail}: {short_date(day, ctx.today)}")
    if not saved and not asks:
        return help_text(ctx.cfg.currency)
    parts = []
    if saved:
        parts.append("Got it 📅\n" + "\n".join(saved) + "\nSend /forecast to see your Wall Day.")
    parts.extend(asks)
    return "\n\n".join(parts)


# ---------- afford + forecast ----------


def _words(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower().replace("'s", " "))
    return {w for w in words if w not in GENERIC_WORDS and len(w) > 1}


def link_event(about: str | None, known: list[dict]) -> dict | None:
    """The known upcoming event the question is about: same title, else a shared name word."""
    if not about:
        return None
    for e in known:
        if e["title"].lower() == about.strip().lower():
            return e
    words = _words(about)
    for e in known:
        if words & _words(e["title"]):
            return e
    return None


# "Can I afford a ₺5,000 gift for Arman?" -> "gift for Arman" (only used for the wording of the reply)
_THING = re.compile(
    r"afford\s+(?:a|an|the)?\s*(?:[₺$€]\s*)?\d[\d.,]*\s*(?:k\b)?\s*(?:₺|tl\b|lira\b|try\b)?\s*(?P<thing>[^?.!]*)",
    re.IGNORECASE,
)


def thing_from_text(text: str) -> str | None:
    match = _THING.search(text or "")
    thing = match.group("thing").strip() if match else ""
    return thing or None


async def afford(ctx: Ctx, intent: Intent, known: list[dict], text: str = "") -> list[str]:
    from app.forecast import format_afford, run_forecast

    if not get_settings(ctx.engine).get("budget_cents"):
        return [NEED_START]
    amount = parse_amount(intent.amount)
    if amount is None or amount <= 0:
        example = format_money(5000, ctx.cfg.currency)
        return [f"How much is it? Ask me with the amount, like: Can I afford a {example} gift?"]
    event = link_event(intent.about, known)
    on = intent.spend_date or (event["date"] if event else ctx.today)
    if on < ctx.today:
        on = ctx.today
    # Wording only: the user's own phrase ("gift for Arman"), else the model's word for the thing.
    about = thing_from_text(text) or (None if event else re.sub(r"^(a|an|the)\s+", "", intent.about or "", flags=re.I) or None)
    with tracing.tool_span("forecast"):
        base = await run_forecast(ctx.engine, ctx.today)
        with_extra = await run_forecast(ctx.engine, ctx.today, extra=[(on, float(amount))])
    return [format_afford(base, with_extra, float(amount), on, about, ctx.cfg.currency)]


async def handle_forecast_command(ctx: Ctx) -> list[str | bytes]:
    from app.forecast import format_forecast, run_forecast

    if not get_settings(ctx.engine).get("budget_cents"):
        return [NEED_START]
    with tracing.tool_span("forecast"):
        result = await run_forecast(ctx.engine, ctx.today)
    replies: list[str | bytes] = [format_forecast(result, ctx.cfg.currency)]
    try:
        from app.chart import render_chart

        chart = render_chart(result, ctx.cfg.currency)
        if chart:
            replies.append(chart)
    except Exception:  # the chart is a bonus; the text reply must still go out
        log.exception("chart failed")
    return replies
