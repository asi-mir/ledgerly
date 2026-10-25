"""The demo household (synthetic) and the automated check from the spec (M3).

Offline: last December passes ₺30,000 on Dec 19, the seed is idempotent, the data is made up.
Live (LEDGERLY_LIVE=1, real TabPFN): on Dec 1 the real forecast gives a believable Wall Day.
"""

import asyncio
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, insert, select

from app import demo
from app import forecast as fc
from app.db import daily_spend, events, get_settings, line_items, receipts, update_settings
from tests.helpers import live

ROOT = Path(__file__).resolve().parent.parent

# Exactly what the chat agent stores for "Arman's birthday is Dec 14. We're hosting Yalda for 12 people."
CHAT_EVENTS = [
    {"date": date(2026, 12, 14), "title": "Arman's birthday", "kind": "birthday", "guests": 0, "hosted": False, "source": "chat"},
    {"date": date(2026, 12, 21), "title": "Yalda Night", "kind": "hosting", "guests": 12, "hosted": True, "source": "chat"},
]


def count(engine, table, *where):
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(table).where(*where)).scalar_one()


def test_last_december_passes_the_budget_on_dec_19(engine):
    demo.seed_demo(engine)
    spend = daily_spend(engine)
    total, passed = 0.0, None
    for day in sorted(d for d in spend if (d.year, d.month) == (2025, 12)):
        total += spend[day]
        if passed is None and total > 30_000:
            passed = day
    assert passed == date(2025, 12, 19)
    assert total > 30_000


def test_normal_months_stay_under_budget():
    receipts_, _ = demo.generate()
    months = defaultdict(int)
    for r in receipts_:
        months[(r.day.year, r.day.month)] += r.total_cents
    assert len(months) == 12
    for month, cents in months.items():
        if month != (2025, 12):
            assert cents < demo.BUDGET_CENTS, month


def test_every_receipt_adds_up_and_is_filed(engine):
    demo.seed_demo(engine)
    with engine.connect() as conn:
        rows = conn.execute(
            select(receipts.c.id, receipts.c.total_cents, func.sum(line_items.c.amount_cents))
            .join(line_items, line_items.c.receipt_id == receipts.c.id)
            .group_by(receipts.c.id, receipts.c.total_cents)
        ).all()
        statuses = set(conn.execute(select(receipts.c.status, receipts.c.source).distinct()).all())
    assert len(rows) == count(engine, receipts)
    assert all(total == items for _, total, items in rows)
    assert statuses == {("filed", "demo")}


def test_watermelon_on_dec_19():
    receipts_, _ = demo.generate()
    items = [item for r in receipts_ if r.day == date(2025, 12, 19) for item, _, _ in r.items]
    assert "KARPUZ" in items


def test_this_december_events():
    _, evs = demo.generate()
    dec = [(e["title"], e["kind"]) for e in evs if (e["date"].year, e["date"].month) == (2026, 12)]
    assert sorted(dec) == [
        ("Dinner at Aunt Shirin's", "dinner"),
        ("Kian's birthday", "birthday"),
        ("Mina's birthday", "birthday"),
        ("Sara's birthday", "birthday"),
    ]  # Arman's birthday and Yalda come from chat in the demo
    last = [e for e in evs if (e["date"].year, e["date"].month) == (2025, 12)]
    assert sum(e["kind"] == "birthday" for e in last) == 4
    yalda = next(e for e in last if e["title"] == "Yalda Night")
    assert (yalda["date"], yalda["guests"], yalda["hosted"]) == (date(2025, 12, 21), 12, True)


def test_seed_is_idempotent_and_keeps_chat_rows(engine):
    with engine.begin() as conn:
        conn.execute(insert(events), CHAT_EVENTS)
        conn.execute(insert(receipts).values(date=date(2026, 10, 20), merchant="Chat Shop", total_cents=1000, status="filed", source="telegram"))
    assert demo.seed_demo(engine) is True
    n_receipts, n_items, n_events = count(engine, receipts), count(engine, line_items), count(engine, events)
    started = time.perf_counter()
    assert demo.seed_demo(engine) is False  # same data: nothing written
    assert time.perf_counter() - started < 2
    assert (count(engine, receipts), count(engine, line_items), count(engine, events)) == (n_receipts, n_items, n_events)
    assert count(engine, events, events.c.source == "chat") == 2
    assert count(engine, receipts, receipts.c.source == "telegram") == 1


def test_changed_generator_replaces_only_demo_rows(engine, monkeypatch):
    with engine.begin() as conn:
        conn.execute(insert(events), CHAT_EVENTS)
    demo.seed_demo(engine)
    full, full_events = demo.generate()
    monkeypatch.setattr(demo, "generate", lambda: (full[:10], full_events[:3]))
    assert demo.seed_demo(engine) is True
    assert count(engine, receipts, receipts.c.source == "demo") == 10
    assert count(engine, events, events.c.source == "demo") == 3
    assert count(engine, events, events.c.source == "chat") == 2
    with engine.connect() as conn:  # no line items left behind from the old demo receipts
        orphans = conn.execute(
            select(func.count()).select_from(line_items).where(line_items.c.receipt_id.not_in(select(receipts.c.id)))
        ).scalar_one()
    assert orphans == 0


def test_budget_and_payday_only_when_not_set(engine):
    demo.seed_demo(engine)
    assert (get_settings(engine)["budget_cents"], get_settings(engine)["payday"]) == (3_000_000, 1)
    update_settings(engine, budget_cents=2_000_000, payday=15)
    demo.seed_demo(engine)
    assert (get_settings(engine)["budget_cents"], get_settings(engine)["payday"]) == (2_000_000, 15)


def test_only_made_up_data():
    receipts_, evs = demo.generate()
    assert {r.merchant for r in receipts_} <= demo.MERCHANTS
    assert min(r.day for r in receipts_) == date(2025, 12, 1) and max(r.day for r in receipts_) == date(2026, 11, 30)
    assert all(e["source"] == "demo" for e in evs)
    assert demo.generate()[0] == receipts_  # fixed seed


def test_sample_csvs_match_the_generator():
    for name, text in demo.csv_texts().items():
        assert (ROOT / "samples" / name).read_text(encoding="utf-8") == text, f"run scripts/make_demo_data.py ({name})"


@pytest.mark.live
@live
def test_demo_forecast_on_dec_1_with_real_tabpfn(engine):
    """The automated check: tell the bot about Arman and Yalda, then /forecast on Dec 1."""
    fc.clear_cache()
    demo.seed_demo(engine)
    with engine.begin() as conn:
        conn.execute(insert(events), CHAT_EVENTS)
    started = time.perf_counter()
    f = asyncio.run(fc.run_forecast(engine, date(2026, 12, 1)))
    seconds = time.perf_counter() - started
    print(f"\nTabPFN fit + predict: {seconds:.1f} s ({f.method})")
    print(fc.format_forecast(f))
    assert f.method in ("tabpfn", "tabpfn-median")
    assert date(2026, 12, 18) <= f.wall_day <= date(2026, 12, 21)
    assert f.likely_total > 30_000
    assert "Yalda" in f.biggest_reason
    assert f.birthdays == 4

    started = time.perf_counter()
    gift = asyncio.run(fc.run_forecast(engine, date(2026, 12, 1), extra=[(date(2026, 12, 14), 5000)]))
    assert time.perf_counter() - started < 1  # cached: TabPFN is not called again
    print(fc.format_afford(f, gift, 5000, date(2026, 12, 14), "gift for Arman"))
    assert gift.wall_day < f.wall_day
    fc.clear_cache()
