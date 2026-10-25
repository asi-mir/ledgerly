from app import tracing


def test_bot_token_is_scrubbed_everywhere():
    event = {
        "transaction": "telegram text",
        "spans": [{"description": "POST https://api.telegram.org/bot123456:AAE-x_y/sendMessage", "data": {"url": "https://api.telegram.org/bot123456:AAE-x_y/getFile"}}],
    }
    clean = tracing.scrub(event)
    text = str(clean)
    assert "AAE-x_y" not in text and "bot[Filtered]/sendMessage" in text


def test_health_checks_and_webhook_requests_are_not_sent():
    assert tracing._log_transaction({"transaction": "/health"}, {}) is None
    assert tracing._log_transaction({"transaction": "/webhook"}, {}) is None
    kept = tracing._log_transaction({"transaction": "telegram /forecast", "timestamp": 2.0, "start_timestamp": 1.0, "spans": []}, {})
    assert kept["transaction"] == "telegram /forecast"
