"""Receipt photos: Gemma reads, code checks the sum, the bot asks for a missing date.

The image is never stored: only Telegram's file ids and the SHA-256 of the bytes.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import delete, insert, select, update

from app import db, telegram, tracing
from app.context import Ctx
from app.gemma import ask_gemma, extract_json
from app.money import format_cents, parse_amount, to_cents

log = logging.getLogger("ledgerly.receipts")

RULES_PATH = Path(__file__).resolve().parent.parent / "prompts" / "receipt_rules.md"
CATEGORIES = (
    "Groceries", "Fruit and Vegs", "Meat/Chicken/Fish", "Bread/Water", "Electrical bill", "Water bill",
    "Gas bill", "Internet/Phone", "Dues", "Entertainment/Restaurant", "Clothes", "Transport", "Gifts",
    "Additional expenses", "rent",
)
FALLBACK_CATEGORY = "Additional expenses"
TOLERANCE = Decimal("0.01")
MAX_AGE_DAYS = 400
MAX_ITEM_LINES = 8

GOT_IT = "Got it 🦥 Reading your receipt…"
DUPLICATE = "You already sent me this one 🦥"
NOT_COUNTED = "I keep it aside: it does not count until it adds up. A sharper photo usually fixes it."
UNREADABLE = "Sorry, I could not read this receipt 🦥 Please send a sharper photo."
DATE_QUESTION = 'What date is this? Reply "yesterday" or "28.11".'


# ---------- what Gemma returns ----------


def _amount(value) -> Decimal:
    if isinstance(value, str):
        parsed = parse_amount(value)  # "1.234,56" -> 1234.56
        if parsed is None:
            raise ValueError(f"not an amount: {value!r}")
        return parsed
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("amount must be a number")
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise ValueError("bad amount") from exc


class Item(BaseModel):
    name: str = Field(min_length=1)
    amount: Decimal
    category: str = FALLBACK_CATEGORY

    @field_validator("amount", mode="before")
    @classmethod
    def _parse_amount(cls, v):
        return _amount(v)

    @field_validator("category", mode="before")
    @classmethod
    def _known_category(cls, v):
        text = str(v or "").strip()
        for name in CATEGORIES:
            if text.lower() == name.lower():
                return name
        return FALLBACK_CATEGORY


class ReceiptRead(BaseModel):
    merchant: str = ""
    date: str | None = None
    items: list[Item] = Field(min_length=1)
    total: Decimal

    @field_validator("total", mode="before")
    @classmethod
    def _parse_total(cls, v):
        return _amount(v)

    @field_validator("merchant", mode="before")
    @classmethod
    def _merchant(cls, v):
        return str(v or "").strip()

    @field_validator("date", mode="before")
    @classmethod
    def _date(cls, v):
        return str(v).strip() if v not in (None, "", "null") else None

    @property
    def items_sum(self) -> Decimal:
        return sum((item.amount for item in self.items), Decimal(0))

    @property
    def adds_up(self) -> bool:
        return abs(self.items_sum - self.total) <= TOLERANCE


@lru_cache(maxsize=1)
def receipt_prompt() -> str:
    return RULES_PATH.read_text(encoding="utf-8")


def parse_read(text: str) -> ReceiptRead:
    """Gemma's text -> ReceiptRead. Raises ValueError (no JSON) or ValidationError."""
    return ReceiptRead.model_validate(extract_json(text))


async def read_receipt(image: bytes, mime_type: str = "image/jpeg") -> ReceiptRead | None:
    """Ask Gemma, check in code, retry once with what was wrong. None if it never gave valid JSON."""
    prompt = receipt_prompt()
    best: ReceiptRead | None = None
    for attempt in (1, 2):
        reply = await ask_gemma(prompt, image, mime_type, purpose="receipt")
        try:
            read = parse_read(reply.text)
        except (ValueError, ValidationError) as exc:
            log.info("receipt read %d: bad JSON (%s)", attempt, type(exc).__name__)
            problem = "it was not one valid JSON object with merchant, date, items and total"
        else:
            if read.adds_up:
                return read
            best = read
            problem = (
                f"the items add up to {read.items_sum} but the total is {read.total}. "
                "Read every line again: multipliers (2 X 12,50), discounts, and the Turkish number style 1.234,56"
            )
        prompt = (
            receipt_prompt()
            + f"\n\n## Your last answer was wrong\n\nYour last answer for this photo was wrong: {problem}. "
            "Read the photo again and reply with ONE corrected JSON object."
        )
    return best


# ---------- dates ----------

_MONTH_NAMES = ("january", "february", "march", "april", "may", "june", "july", "august", "september",
                "october", "november", "december")
_MONTHS = {name: i for i, full in enumerate(_MONTH_NAMES, start=1) for name in (full, full[:3])} | {"sept": 9}
_NUMERIC = re.compile(r"^(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2}|\d{4}))?$")
_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_DAYS_AGO = re.compile(r"^(\d{1,3}) days? ago$")
_MONTH_DAY = re.compile(r"^([a-z]{3,9})\.? (\d{1,2})(?:st|nd|rd|th)?,? ?(\d{4})?$")
_DAY_MONTH = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)? ([a-z]{3,9})\.?,? ?(\d{4})?$")


def _make(year: int | None, month: int, day: int, today: date) -> date | None:
    try:
        if year is None:
            return date(today.year, month, day)  # after today -> "That date is in the future"
        if year < 100:
            year += 2000
        return date(year, month, day)
    except ValueError:
        return None


def parse_date_answer(text: str, today: date) -> date | None:
    """A short date answer ('yesterday', '28.11', 'Nov 28', '2026-11-28'). None if the text is not one."""
    t = " ".join(text.strip().lower().rstrip(".!").split())
    if t == "today":
        return today
    if t == "yesterday":
        return today - timedelta(days=1)
    if m := _DAYS_AGO.match(t):
        return today - timedelta(days=int(m.group(1)))
    if m := _ISO.match(t):
        return _make(int(m.group(1)), int(m.group(2)), int(m.group(3)), today)
    if m := _NUMERIC.match(t):
        return _make(int(m.group(3)) if m.group(3) else None, int(m.group(2)), int(m.group(1)), today)
    for pattern, month_group, day_group in ((_MONTH_DAY, 1, 2), (_DAY_MONTH, 2, 1)):
        if m := pattern.match(t):
            month = _MONTHS.get(m.group(month_group))
            if month:
                return _make(int(m.group(3)) if m.group(3) else None, month, int(m.group(day_group)), today)
    return None


def receipt_date(raw: str | None, today: date) -> date | None:
    """The date Gemma read, if it is a real, plausible date. Else None (then the bot asks)."""
    if not raw:
        return None
    try:
        found = date.fromisoformat(raw)
    except ValueError:
        m = re.match(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})$", raw)
        found = _make(int(m.group(3)), int(m.group(2)), int(m.group(1)), today) if m else None
    return found if found and _plausible(found, today) else None


def _plausible(day: date, today: date) -> bool:
    return today - timedelta(days=MAX_AGE_DAYS) <= day <= today


def show_date(day: date) -> str:
    return f"{day.day} {day:%b %Y}"


# ---------- replies ----------


def _summary(merchant: str, day: date | None, items: list[tuple[str, int]], total_cents: int, currency: str) -> list[str]:
    head = f"🧾 {merchant or 'Receipt'}" + (f" · {show_date(day)}" if day else "")
    lines = [head] + [f"• {name} {format_cents(cents, currency)}" for name, cents in items[:MAX_ITEM_LINES]]
    if len(items) > MAX_ITEM_LINES:
        lines.append(f"…and {len(items) - MAX_ITEM_LINES} more")
    items_cents = sum(cents for _, cents in items)
    if abs(items_cents - total_cents) <= 1:
        lines.append(f"Total: {format_cents(total_cents, currency)} ✅ adds up")
    else:
        off = format_cents(abs(items_cents - total_cents), currency)
        lines.append(
            f"Total: {format_cents(total_cents, currency)} ⚠️ needs check: "
            f"the items add up to {format_cents(items_cents, currency)} ({off} off)"
        )
        lines.append(NOT_COUNTED)
    return lines


# ---------- storage ----------


def _is_duplicate(ctx: Ctx, column, value: str) -> bool:
    with ctx.engine.connect() as conn:
        return conn.execute(select(db.receipts.c.id).where(column == value).limit(1)).first() is not None


def _file_receipt(ctx: Ctx, photo: dict, sha: str, read: ReceiptRead, day: date | None, status: str) -> int | None:
    """Store the receipt. None if the same photo was filed meanwhile (two copies sent at once)."""
    with tracing.tool_span("file_receipt") as span, ctx.engine.begin() as conn:
        same = db.receipts.c.sha256 == sha
        if photo.get("file_unique_id"):
            same = same | (db.receipts.c.file_unique_id == photo["file_unique_id"])
        if conn.execute(select(db.receipts.c.id).where(same).limit(1)).first() is not None:
            return None
        receipt_id = conn.execute(
            insert(db.receipts).values(
                chat_id=ctx.chat_id,
                file_id=photo.get("file_id"),
                file_unique_id=photo.get("file_unique_id"),
                sha256=sha,
                date=day,
                merchant=read.merchant[:255],
                total_cents=to_cents(read.total),
                status=status,
                source="telegram",
            )
        ).inserted_primary_key[0]
        conn.execute(
            insert(db.line_items),
            [
                {"receipt_id": receipt_id, "item": i.name[:255], "amount_cents": to_cents(i.amount), "category": i.category}
                for i in read.items
            ],
        )
        if status == "asking":
            conn.execute(delete(db.pending).where(db.pending.c.chat_id == ctx.chat_id))
            conn.execute(insert(db.pending).values(chat_id=ctx.chat_id, kind="date", receipt_id=receipt_id))
        span.set_data("ledgerly.status", status)
        span.set_data("ledgerly.items", len(read.items))
    return receipt_id


def _next_asking(ctx: Ctx, done_id: int) -> str:
    """If another receipt of this chat still waits for a date, make it the pending one and ask."""
    with ctx.engine.begin() as conn:
        row = conn.execute(
            select(db.receipts.c.id, db.receipts.c.merchant, db.receipts.c.total_cents)
            .where(db.receipts.c.chat_id == ctx.chat_id, db.receipts.c.status == "asking", db.receipts.c.id != done_id)
            .order_by(db.receipts.c.id.desc())
            .limit(1)
        ).first()
        if row is None:
            return ""
        conn.execute(delete(db.pending).where(db.pending.c.chat_id == ctx.chat_id))
        conn.execute(insert(db.pending).values(chat_id=ctx.chat_id, kind="date", receipt_id=row.id))
    return f"\n\nOne more: 🧾 {row.merchant or 'Receipt'}, {format_cents(row.total_cents, ctx.cfg.currency)}. {DATE_QUESTION}"


# ---------- handlers ----------


async def handle_photo(ctx: Ctx, message: dict) -> list[str]:
    """A Telegram message with a photo. Returns the reply texts."""
    photo = max(message["photo"], key=lambda p: (p.get("file_size") or 0, p.get("width", 0) * p.get("height", 0)))
    if photo.get("file_unique_id") and _is_duplicate(ctx, db.receipts.c.file_unique_id, photo["file_unique_id"]):
        return [DUPLICATE]
    await telegram.send_message(ctx.chat_id, GOT_IT)
    image = await telegram.get_file_bytes(photo["file_id"])
    sha = hashlib.sha256(image).hexdigest()
    if _is_duplicate(ctx, db.receipts.c.sha256, sha):
        return [DUPLICATE]

    read = await read_receipt(image)
    if read is None:
        tracing.capture_message("Receipt could not be read")
        return [UNREADABLE]

    day = receipt_date(read.date, ctx.today)
    if not read.adds_up:
        tracing.capture_message("Receipt does not add up")
    status = "asking" if day is None else ("filed" if read.adds_up else "needs_check")
    if _file_receipt(ctx, photo, sha, read, day, status) is None:
        return [DUPLICATE]

    items = [(i.name, to_cents(i.amount)) for i in read.items]
    lines = _summary(read.merchant, day, items, to_cents(read.total), ctx.cfg.currency)
    if status == "asking":
        lines += ["", DATE_QUESTION]
    return ["\n".join(lines)]


async def handle_date_reply(ctx: Ctx, text: str) -> list[str] | None:
    """If this chat is waiting for a receipt date and the text is a date, use it. Else None."""
    with ctx.engine.connect() as conn:
        waiting = conn.execute(
            select(db.pending).where(db.pending.c.chat_id == ctx.chat_id, db.pending.c.kind == "date")
        ).mappings().first()
    if waiting is None:
        return None
    day = parse_date_answer(text, ctx.today)
    if day is None:
        return None
    if day > ctx.today:
        return [f"That date is in the future. {DATE_QUESTION}"]
    if not _plausible(day, ctx.today):
        return [f"That is more than a year ago. {DATE_QUESTION}"]

    with tracing.tool_span("file_receipt"), ctx.engine.begin() as conn:
        receipt = conn.execute(
            select(db.receipts.c.total_cents).where(db.receipts.c.id == waiting["receipt_id"])
        ).first()
        conn.execute(delete(db.pending).where(db.pending.c.chat_id == ctx.chat_id))
        if receipt is None:
            return None
        items_cents = conn.execute(
            select(db.line_items.c.amount_cents).where(db.line_items.c.receipt_id == waiting["receipt_id"])
        ).scalars().all()
        off_cents = abs(sum(items_cents) - receipt.total_cents)
        status = "filed" if off_cents <= 1 else "needs_check"
        conn.execute(
            update(db.receipts).where(db.receipts.c.id == waiting["receipt_id"]).values(date=day, status=status)
        )
    nxt = _next_asking(ctx, waiting["receipt_id"])
    if status == "filed":
        return [f"Thanks. Filed under {show_date(day)} ✅" + nxt]
    return [f"Thanks. Dated {show_date(day)}. ⚠️ needs check: the items are {format_cents(off_cents, ctx.cfg.currency)} off the total.\n{NOT_COUNTED}" + nxt]
