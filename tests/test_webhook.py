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
