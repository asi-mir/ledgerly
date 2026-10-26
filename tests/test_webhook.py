import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture
def client(monkeypatch):
    handled = []

    async def fake_process(message):
        handled.append(message)

    monkeypatch.setattr(main, "process_message", fake_process)
    main.seen = main.SeenUpdates()
    with TestClient(main.app) as c:
        c.handled = handled
        yield c


def update(update_id, user_id=111, text="hi"):
    return {"update_id": update_id, "message": {"message_id": 1, "from": {"id": user_id}, "chat": {"id": user_id}, "text": text}}


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_rejects_missing_or_wrong_secret(client):
    assert client.post("/webhook", json=update(1)).status_code == 401
    r = client.post("/webhook", json=update(1), headers={"X-Telegram-Bot-Api-Secret-Token": "nope"})
    assert r.status_code == 401
    assert client.handled == []


def test_accepts_owner_once(client):
    h = {"X-Telegram-Bot-Api-Secret-Token": "test-secret"}
    assert client.post("/webhook", json=update(5), headers=h).status_code == 200
    assert client.post("/webhook", json=update(5), headers=h).status_code == 200  # Telegram resend
    assert len(client.handled) == 1


def test_owner_lock_ignores_strangers(client):
    h = {"X-Telegram-Bot-Api-Secret-Token": "test-secret"}
    assert client.post("/webhook", json=update(7, user_id=999), headers=h).status_code == 200
    assert client.handled == []


def test_postgres_url_rewrite():
    from app.db import normalize_url

    assert normalize_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_url("sqlite:///x.db") == "sqlite:///x.db"


def test_sorry_note_once_after_a_nap(monkeypatch):
    import asyncio
    import time

    sent = []

    async def fake_send(chat_id, text):
        sent.append(text)

    async def fake_action(chat_id, action="typing"):
        return {}

    async def fake_route(ctx, message, kind):
        return ["ok"]

    monkeypatch.setattr(main.telegram, "send_message", fake_send)
    monkeypatch.setattr(main.telegram, "send_chat_action", fake_action)
    monkeypatch.setattr(main, "route", fake_route)
    monkeypatch.setattr(main, "_nap_note_sent", False)
    old = {"chat": {"id": 111}, "from": {"id": 111}, "text": "hi", "date": int(time.time()) - 70}
    asyncio.run(main.process_message(old))
    asyncio.run(main.process_message({**old, "date": int(time.time()) - 65}))
    fresh = {**old, "date": int(time.time())}
    asyncio.run(main.process_message(fresh))
    assert sent == [main.NAP_NOTE, "ok", "ok", "ok"]


def test_old_settings_table_gets_the_currency_column(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    from app import db

    url = f"sqlite:///{tmp_path / 'old.db'}"
    old = create_engine(url)
    with old.begin() as conn:
        conn.execute(text("CREATE TABLE settings (id INTEGER PRIMARY KEY, budget_cents BIGINT, payday INTEGER, step VARCHAR(20))"))
        conn.execute(text("INSERT INTO settings (id, budget_cents, payday) VALUES (1, 3000000, 1)"))
    old.dispose()
    engine = db.get_engine(url)
    assert "currency" in {c["name"] for c in inspect(engine).get_columns("settings")}
    assert db.get_settings(engine)["budget_cents"] == 3000000
