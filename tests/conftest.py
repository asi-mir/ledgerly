import os
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LIVE = os.environ.get("LEDGERLY_LIVE") == "1"


def _env_file_value(name: str) -> str:
    env = ROOT / ".env"
    if not env.exists():
        return ""
    for line in env.read_text().splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


# Tests never touch real services unless LEDGERLY_LIVE=1 (then real Gemma/TabPFN keys are used,
# but never Telegram or Sentry).
os.environ.update(
    {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_WEBHOOK_SECRET": "test-secret",
        "ALLOWED_TELEGRAM_IDS": "111",
        "SENTRY_DSN": "",
        "GEMMA_PROVIDER": "aistudio",
        "GEMINI_API_KEY": _env_file_value("GEMINI_API_KEY") if LIVE else "",
        "TABPFN_TOKEN": _env_file_value("TABPFN_TOKEN") if LIVE else "",
        "RENDER_EXTERNAL_URL": "",
        "DEMO_TODAY": "",
        "DATABASE_URL": "sqlite:///:memory:",
        "CURRENCY": "₺",  # the tests use the demo household's currency
    }
)


@pytest.fixture
def engine():
    """A fresh, empty in-memory database for one test."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from app.db import metadata

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def ctx(engine):
    from app.config import get_config
    from app.context import Ctx

    get_config.cache_clear()
    return Ctx(chat_id=111, user_id=111, today=date(2026, 12, 1), engine=engine, cfg=get_config())
