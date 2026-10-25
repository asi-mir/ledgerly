"""Settings from environment variables. Values live in .env locally and in Render."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo


def _ids(raw: str) -> frozenset[int]:
    return frozenset(int(part) for part in raw.replace(" ", "").split(",") if part)


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    webhook_secret: str
    allowed_ids: frozenset[int]
    currency: str
    timezone: str
    gemma_provider: str
    gemini_api_key: str
    gemma_model: str
    ollama_base_url: str
    ollama_model: str
    tabpfn_token: str
    database_url: str
    sentry_dsn: str
    sentry_environment: str
    demo_today: date | None
    external_url: str


@lru_cache(maxsize=1)
def get_config() -> Config:
    env = os.environ.get
    demo = env("DEMO_TODAY", "").strip()
    return Config(
        telegram_bot_token=env("TELEGRAM_BOT_TOKEN", ""),
        webhook_secret=env("TELEGRAM_WEBHOOK_SECRET", ""),
        allowed_ids=_ids(env("ALLOWED_TELEGRAM_IDS", "")),
        currency=env("CURRENCY", "₺") or "₺",
        timezone=env("TIMEZONE", "Europe/Istanbul") or "Europe/Istanbul",
        gemma_provider=(env("GEMMA_PROVIDER", "aistudio") or "aistudio").lower(),
        gemini_api_key=env("GEMINI_API_KEY", ""),
        gemma_model=env("GEMMA_MODEL", "gemma-4-31b-it") or "gemma-4-31b-it",
        ollama_base_url=env("OLLAMA_BASE_URL", "http://localhost:11434"),
        ollama_model=env("OLLAMA_MODEL", "gemma4:e4b") or "gemma4:e4b",
        tabpfn_token=env("TABPFN_TOKEN", ""),
        database_url=env("DATABASE_URL", "sqlite:///data/private/ledger.db"),
        sentry_dsn=env("SENTRY_DSN", ""),
        sentry_environment=env("SENTRY_ENVIRONMENT", "local") or "local",
        demo_today=date.fromisoformat(demo) if demo else None,
        external_url=env("RENDER_EXTERNAL_URL", "").rstrip("/"),
    )


def today(cfg: Config | None = None) -> date:
    """The bot's 'today'. A demo copy can pin it with DEMO_TODAY."""
    cfg = cfg or get_config()
    if cfg.demo_today:
        return cfg.demo_today
    return datetime.now(ZoneInfo(cfg.timezone)).date()
