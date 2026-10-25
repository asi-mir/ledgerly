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


def test_request_headers_and_reply_breadcrumbs_never_reach_sentry():
    event = {"request": {"headers": {"X-Telegram-Bot-Api-Secret-Token": "s3cret"}, "url": "https://x/webhook"}}
    assert "s3cret" not in str(tracing._clean(event))
    assert tracing._breadcrumb({"category": "ledgerly.telegram", "message": "reply chat=1 text='🧾 ...'"}, {}) is None
    assert tracing._breadcrumb({"category": "httpx", "message": "GET https://api.telegram.org/bot1:AAA/x"}, {})["message"].count("AAA") == 0


def test_log_lines_and_tracebacks_are_scrubbed():
    import logging

    from app.main import _ScrubToken

    try:
        raise RuntimeError("for url 'https://api.telegram.org/file/bot123:ABC_def/x'")
    except RuntimeError:
        import sys

        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "url %s", ("https://api.telegram.org/bot123:ABC_def/y",), sys.exc_info())
    _ScrubToken().filter(record)
    assert "ABC_def" not in record.getMessage() and "ABC_def" not in record.exc_text
