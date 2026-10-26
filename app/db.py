"""Storage: one SQLAlchemy Core module. Postgres on Render, SQLite locally and in tests.

The ledger (receipts + line_items) is the truth. Daily totals and forecasts are always
rebuilt from it, never stored. Money is stored as integer kuruş/cents.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    func,
    select,
)
from sqlalchemy.engine import Engine

metadata = MetaData()

# One row (id=1): the household's setup from /start.
settings = Table(
    "settings",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("budget_cents", BigInteger, nullable=True),
    Column("payday", Integer, nullable=True),  # day of month, 1-28
    Column("step", String(20), nullable=True),  # onboarding step waiting for an answer
    Column("currency", String(8), nullable=True),  # learned from the budget answer; None = CURRENCY env
)

receipts = Table(
    "receipts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chat_id", BigInteger, nullable=True),
    Column("file_id", String(255), nullable=True),  # Telegram keeps the image
    Column("file_unique_id", String(128), nullable=True, index=True),
    Column("sha256", String(64), nullable=True, index=True),
    Column("date", Date, nullable=True),
    Column("merchant", String(255), nullable=False, default=""),
    Column("total_cents", BigInteger, nullable=False, default=0),
    Column("status", String(20), nullable=False),  # filed | needs_check | asking
    Column("source", String(20), nullable=False, default="telegram"),  # telegram | demo
    Column("created_at", DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)),
)

line_items = Table(
    "line_items",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("receipt_id", Integer, ForeignKey("receipts.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("item", String(255), nullable=False),
    Column("amount_cents", BigInteger, nullable=False),
    Column("category", String(64), nullable=False),
)

events = Table(
    "events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("date", Date, nullable=False),
    Column("title", String(255), nullable=False),
    Column("kind", String(20), nullable=False),  # birthday | hosting | dinner | other
    Column("guests", Integer, nullable=False, default=0),
    Column("hosted", Boolean, nullable=False, default=False),
    Column("source", String(20), nullable=False, default="chat"),  # chat | demo
)

# What the bot is waiting for in a chat (for example the date of a receipt).
pending = Table(
    "pending",
    metadata,
    Column("chat_id", BigInteger, primary_key=True),
    Column("kind", String(20), nullable=False),  # date
    Column("receipt_id", Integer, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)),
)

# Only receipts whose items add up count (AGENTS.md rule 2). "needs_check" and "asking" are kept aside.
COUNTED_STATUSES = ("filed",)

_engines: dict[str, Engine] = {}


def normalize_url(url: str) -> str:
    """Render gives postgres://; SQLAlchemy + psycopg 3 wants postgresql+psycopg://."""
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def get_engine(url: str | None = None) -> Engine:
    if url is None:
        from app.config import get_config

        url = get_config().database_url
    url = normalize_url(url)
    if url not in _engines:
        if url.startswith("sqlite:///") and not url.startswith("sqlite:///:memory:"):
            Path(url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
        if url.startswith("sqlite:///:memory:"):
            from sqlalchemy.pool import StaticPool

            engine = create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
        else:
            engine = create_engine(url, pool_pre_ping=True)
        metadata.create_all(engine)
        _add_missing_columns(engine)
        _engines[url] = engine
    return _engines[url]


def _add_missing_columns(engine: Engine) -> None:
    """create_all never changes an existing table. Add columns that came later (no other migrations)."""
    from sqlalchemy import inspect, text

    have = {c["name"] for c in inspect(engine).get_columns("settings")}
    if "currency" not in have:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE settings ADD COLUMN currency VARCHAR(8)"))


def get_settings(engine: Engine) -> dict:
    with engine.begin() as conn:
        row = conn.execute(select(settings).where(settings.c.id == 1)).mappings().first()
        if row is None:
            conn.execute(settings.insert().values(id=1))
            return {"id": 1, "budget_cents": None, "payday": None, "step": None, "currency": None}
        return dict(row)


def update_settings(engine: Engine, **values) -> None:
    get_settings(engine)
    with engine.begin() as conn:
        conn.execute(settings.update().where(settings.c.id == 1).values(**values))


def daily_spend(engine: Engine) -> dict[date, float]:
    """Spending per day, rebuilt from the ledger. Receipts still waiting for a date are left out."""
    query = (
        select(receipts.c.date, func.sum(receipts.c.total_cents))
        .where(receipts.c.status.in_(COUNTED_STATUSES), receipts.c.date.is_not(None))
        .group_by(receipts.c.date)
    )
    with engine.connect() as conn:
        return {row[0]: row[1] / 100 for row in conn.execute(query)}


def list_events(engine: Engine) -> list[dict]:
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(select(events).order_by(events.c.date)).mappings()]


def data_fingerprint(engine: Engine) -> tuple:
    """Changes whenever the ledger or the events change. Used as a forecast cache key."""
    with engine.connect() as conn:
        r = conn.execute(
            select(func.count(receipts.c.id), func.max(receipts.c.id), func.coalesce(func.sum(receipts.c.total_cents), 0))
            .where(receipts.c.status.in_(COUNTED_STATUSES))
        ).one()
        e = tuple(conn.execute(select(events.c.id, events.c.date, events.c.kind, events.c.guests, events.c.hosted)).all())
        s = conn.execute(select(settings.c.budget_cents, settings.c.payday).where(settings.c.id == 1)).first()
    return (tuple(r), hash(e), tuple(s) if s else None)
