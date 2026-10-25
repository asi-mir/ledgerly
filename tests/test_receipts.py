"""Receipts: Gemma is mocked; the money check, duplicates and date asking are real code."""

import asyncio
import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select

from app import db, receipts
from app.gemma import GemmaReply
from tests.helpers import live

TODAY = date(2026, 12, 1)
SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "receipts"

GOOD = {
    "merchant": "Örnek Market",
    "date": "2026-11-28",
    "items": [
        {"name": "Karpuz", "amount": 250, "category": "Fruit and Vegs"},
        {"name": "Süt 1 L", "amount": 120.0, "category": "Groceries"},
        {"name": "Birthday cake", "amount": 1430, "category": "Gifts"},
    ],
    "total": 1800,
}


def run(coro):
    return asyncio.run(coro)


def photo_message(unique="u1", file_id="f1"):
    return {
        "chat": {"id": 111},
        "from": {"id": 111},
        "photo": [
            {"file_id": "small", "file_unique_id": "small-u", "width": 90, "height": 90, "file_size": 1000},
            {"file_id": file_id, "file_unique_id": unique, "width": 800, "height": 1200, "file_size": 90000},
        ],
    }


@pytest.fixture
def fake(monkeypatch):
    """Mock Telegram and Gemma. Gemma answers from fake.replies, in order."""

    class Fake:
        replies: list[str] = []
        prompts: list[str] = []
        sent: list[str] = []
        image = b"jpeg-bytes-1"
        downloads = 0

    async def ask(prompt, image=None, mime_type="image/jpeg", *, purpose):
        assert purpose == "receipt" and image == Fake.image
        Fake.prompts.append(prompt)
        return GemmaReply(text=Fake.replies.pop(0), model="gemma-test")

    async def send(chat_id, text):
        Fake.sent.append(text)
        return {"ok": True}

    async def get_bytes(file_id):
        Fake.downloads += 1
        assert file_id != "small"  # the largest PhotoSize is used
        return Fake.image

    monkeypatch.setattr(receipts, "ask_gemma", ask)
    monkeypatch.setattr(receipts.telegram, "send_message", send)
    monkeypatch.setattr(receipts.telegram, "get_file_bytes", get_bytes)
    return Fake


def rows(engine, table):
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(select(table)).mappings()]


# ---------- reading Gemma's output ----------


def test_turkish_number_strings_are_read_right():
    read = receipts.parse_read(
        'Here it is: {"merchant": "X", "date": null, "items": [{"name": "Pasta", "amount": "1.234,56", '
        '"category": "gifts"}, {"name": "Mum", "amount": "15,44", "category": "Candles"}], "total": "1.250,00"} done'
    )
    assert str(read.items[0].amount) == "1234.56"  # never 1.23
    assert read.items[0].category == "Gifts"
    assert read.items[1].category == "Additional expenses"  # unknown category -> fallback
    assert read.adds_up and read.date is None


def test_bad_json_raises():
    with pytest.raises(ValueError):
        receipts.parse_read("I cannot read this photo.")
    with pytest.raises(ValueError):
        receipts.parse_read('{"merchant": "X", "items": [], "total": 5}')


# ---------- the money check ----------


def test_filed_receipt_adds_up(ctx, fake):
    fake.replies = [json.dumps(GOOD)]
    replies = run(receipts.handle_photo(ctx, photo_message()))
    assert fake.sent == ["Got it 🦥 Reading your receipt…"]
    assert replies == [
        "🧾 Örnek Market · 28 Nov 2026\n• Karpuz ₺250\n• Süt 1 L ₺120\n• Birthday cake ₺1,430\nTotal: ₺1,800 ✅ adds up"
    ]
    (r,) = rows(ctx.engine, db.receipts)
    assert (r["status"], r["date"], r["total_cents"], r["file_id"], r["file_unique_id"]) == (
        "filed", date(2026, 11, 28), 180000, "f1", "u1")
    assert len(r["sha256"]) == 64
    assert [i["amount_cents"] for i in rows(ctx.engine, db.line_items)] == [25000, 12000, 143000]
    assert db.daily_spend(ctx.engine) == {date(2026, 11, 28): 1800.0}


def test_sum_off_then_retry_fixes_it(ctx, fake):
    wrong = {**GOOD, "items": [{**GOOD["items"][0]}, {**GOOD["items"][1]}, {"name": "Birthday cake", "amount": 1.43}]}
    fake.replies = [json.dumps(wrong), json.dumps(GOOD)]
    replies = run(receipts.handle_photo(ctx, photo_message()))
    assert len(fake.prompts) == 2
    assert "add up to 371.43 but the total is 1800" in fake.prompts[1]
    assert replies[0].endswith("✅ adds up")
    assert rows(ctx.engine, db.receipts)[0]["status"] == "filed"


def test_bad_json_then_retry(ctx, fake):
    fake.replies = ["Sorry, here is the receipt: merchant Örnek", json.dumps(GOOD)]
    run(receipts.handle_photo(ctx, photo_message()))
    assert "not one valid JSON object" in fake.prompts[1]
    assert rows(ctx.engine, db.receipts)[0]["status"] == "filed"


def test_still_off_after_retry_is_needs_check(ctx, fake, monkeypatch):
    captured = []
    monkeypatch.setattr(receipts.tracing, "capture_message", lambda msg, **kw: captured.append(msg))
    wrong = {**GOOD, "total": 1850}
    fake.replies = [json.dumps(wrong), json.dumps(wrong)]
    replies = run(receipts.handle_photo(ctx, photo_message()))
    assert replies[0].endswith("Total: ₺1,850 ⚠️ needs check: the items add up to ₺1,800 (₺50 off)\n" + receipts.NOT_COUNTED)
    assert rows(ctx.engine, db.receipts)[0]["status"] == "needs_check"
    assert captured == ["Receipt does not add up"]


def test_never_valid_json_is_not_filed(ctx, fake):
    fake.replies = ["no", "still no"]
    replies = run(receipts.handle_photo(ctx, photo_message()))
    assert replies == [receipts.UNREADABLE]
    assert rows(ctx.engine, db.receipts) == []


def test_long_receipt_shows_eight_items():
    items = [(f"Item {n}", 1000) for n in range(11)]
    lines = receipts._summary("Shop", date(2026, 11, 28), items, 11000, "₺")
    assert lines[8] == "• Item 7 ₺10" and lines[9] == "…and 3 more"


# ---------- duplicates ----------


def test_duplicate_by_file_unique_id(ctx, fake):
    fake.replies = [json.dumps(GOOD)]
    run(receipts.handle_photo(ctx, photo_message()))
    assert run(receipts.handle_photo(ctx, photo_message())) == ["You already sent me this one 🦥"]
    assert fake.downloads == 1  # caught before the download


def test_duplicate_by_sha256(ctx, fake):
    fake.replies = [json.dumps(GOOD)]
    run(receipts.handle_photo(ctx, photo_message("u1", "f1")))
    # Same bytes, new Telegram ids (for example sent again as a new upload).
    assert run(receipts.handle_photo(ctx, photo_message("u2", "f2"))) == ["You already sent me this one 🦥"]
    assert len(rows(ctx.engine, db.receipts)) == 1


# ---------- dates ----------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("yesterday", date(2026, 11, 30)),
        ("Yesterday.", date(2026, 11, 30)),
        ("today", date(2026, 12, 1)),
        ("3 days ago", date(2026, 11, 28)),
        ("28.11", date(2026, 11, 28)),
        ("28.11.2026", date(2026, 11, 28)),
        ("28/11", date(2026, 11, 28)),
        ("Nov 28", date(2026, 11, 28)),
        ("28 November", date(2026, 11, 28)),
        ("2026-11-28", date(2026, 11, 28)),
        ("Arman's birthday is Dec 14", None),
        ("We're hosting Yalda for 12 people", None),
        ("31.02", None),
        ("hello", None),
    ],
)
def test_parse_date_answer(text, expected):
    assert receipts.parse_date_answer(text, TODAY) == expected


@pytest.mark.parametrize("raw", [None, "", "2026-12-05", "2025-01-01", "not a date"])
def test_unusable_receipt_dates(raw):
    assert receipts.receipt_date(raw, TODAY) is None


def test_receipt_date_accepts_turkish_style():
    assert receipts.receipt_date("28.11.2026", TODAY) == date(2026, 11, 28)


def test_missing_date_asks_then_reply_files_it(ctx, fake):
    fake.replies = [json.dumps({**GOOD, "date": None})]
    replies = run(receipts.handle_photo(ctx, photo_message()))
    assert replies[0].startswith("🧾 Örnek Market\n• Karpuz ₺250")
    assert replies[0].endswith('Total: ₺1,800 ✅ adds up\n\nWhat date is this? Reply "yesterday" or "28.11".')
    assert rows(ctx.engine, db.receipts)[0]["status"] == "asking"
    assert rows(ctx.engine, db.pending)[0]["kind"] == "date"
    assert db.daily_spend(ctx.engine) == {}  # not counted until it has a date

    # Chat that is not a date goes on to the chat agent; the question stays open.
    assert run(receipts.handle_date_reply(ctx, "Arman's birthday is Dec 14")) is None
    assert len(rows(ctx.engine, db.pending)) == 1

    assert run(receipts.handle_date_reply(ctx, "yesterday")) == ["Thanks. Filed under 30 Nov 2026 ✅"]
    r = rows(ctx.engine, db.receipts)[0]
    assert (r["status"], r["date"]) == ("filed", date(2026, 11, 30))
    assert rows(ctx.engine, db.pending) == []
    assert run(receipts.handle_date_reply(ctx, "yesterday")) is None


def test_future_date_on_receipt_asks(ctx, fake):
    fake.replies = [json.dumps({**GOOD, "date": "2026-12-28"})]
    replies = run(receipts.handle_photo(ctx, photo_message()))
    assert "What date is this?" in replies[0]
    assert run(receipts.handle_date_reply(ctx, "28.11")) == ["Thanks. Filed under 28 Nov 2026 ✅"]


def test_date_reply_on_receipt_that_does_not_add_up(ctx, fake):
    wrong = {**GOOD, "date": None, "total": 1850}
    fake.replies = [json.dumps(wrong), json.dumps(wrong)]
    run(receipts.handle_photo(ctx, photo_message()))
    reply = run(receipts.handle_date_reply(ctx, "28.11"))
    assert reply == ["Thanks. Dated 28 Nov 2026. ⚠️ needs check: the items are ₺50 off the total.\n" + receipts.NOT_COUNTED]
    assert rows(ctx.engine, db.receipts)[0]["status"] == "needs_check"


def test_no_pending_date_returns_none(ctx):
    assert run(receipts.handle_date_reply(ctx, "yesterday")) is None


def test_prompt_has_the_rules():
    prompt = receipts.receipt_prompt()
    for rule in ("1.234,56", "KDV", "2 X 12,50", "null", "Gifts", "ONE JSON object"):
        assert rule in prompt
    for category in receipts.CATEGORIES:
        assert f"`{category}`" in prompt


# ---------- live ----------


@pytest.mark.live
@live
def test_live_gemma_reads_sample_market_receipt():
    from app.config import get_config

    get_config.cache_clear()
    read = run(receipts.read_receipt((SAMPLES / "receipt-market.jpg").read_bytes()))
    assert read is not None and read.adds_up
    assert str(read.total) == "1800.00"
    assert read.date == "2026-11-28"


def test_two_undated_receipts_are_both_asked_about(ctx, fake):
    undated = {**GOOD, "date": None}
    fake.replies = [json.dumps(undated)]
    run(receipts.handle_photo(ctx, photo_message(unique="a", file_id="fa")))
    fake.image = b"jpeg-bytes-2"
    fake.replies = [json.dumps({**undated, "merchant": "Örnek Manav"})]
    run(receipts.handle_photo(ctx, photo_message(unique="b", file_id="fb")))
    first = run(receipts.handle_date_reply(ctx, "yesterday"))[0]
    assert first.startswith("Thanks. Filed under 30 Nov 2026 ✅")
    assert "One more: 🧾 Örnek Market" in first and "What date is this?" in first
    second = run(receipts.handle_date_reply(ctx, "28.11"))[0]
    assert second == "Thanks. Filed under 28 Nov 2026 ✅"


def test_a_yearless_date_after_today_is_asked_again(ctx, fake):
    fake.replies = [json.dumps({**GOOD, "date": None})]
    run(receipts.handle_photo(ctx, photo_message()))
    assert run(receipts.handle_date_reply(ctx, "2.12"))[0].startswith("That date is in the future.")


def test_needs_check_is_not_counted(ctx, fake):
    from app.db import daily_spend

    wrong = {**GOOD, "total": 1850}
    fake.replies = [json.dumps(wrong), json.dumps(wrong)]
    run(receipts.handle_photo(ctx, photo_message()))
    assert daily_spend(ctx.engine) == {}


def test_download_error_never_shows_the_token(monkeypatch):
    import httpx

    from app import telegram

    async def fake_call(method, **payload):
        return {"ok": True, "result": {"file_path": "photos/x.jpg"}}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return httpx.Response(404, request=httpx.Request("GET", url))

    monkeypatch.setattr(telegram, "call", fake_call)
    monkeypatch.setattr(telegram.httpx, "AsyncClient", FakeClient)
    with pytest.raises(RuntimeError) as err:
        run(telegram.get_file_bytes("f1"))
    assert "test-token" not in str(err.value) and "404" in str(err.value)
