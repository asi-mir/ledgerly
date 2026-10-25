import os

# Tests never touch real services: no Telegram token, no Sentry, a throwaway SQLite file.
os.environ.update(
    {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_WEBHOOK_SECRET": "test-secret",
        "ALLOWED_TELEGRAM_IDS": "111",
        "SENTRY_DSN": "",
        "GEMINI_API_KEY": "",
        "TABPFN_TOKEN": os.environ.get("TABPFN_TOKEN_FOR_TESTS", ""),
        "RENDER_EXTERNAL_URL": "",
        "DEMO_TODAY": "",
        "DATABASE_URL": "sqlite:///:memory:",
    }
)
