"""FastAPI webhook. Answers Telegram at once with 200, then does the work in the background."""

from __future__ import annotations

import asyncio
import dataclasses
import hmac
import logging
import time
from collections import OrderedDict
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Request, Response

from app import telegram, tracing
from app.config import get_config, today
from app.context import Ctx
from app.db import get_engine, get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ledgerly")
# httpx logs every request URL at INFO, and Telegram URLs contain the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


class _ScrubToken(logging.Filter):
    """Last line of defence: remove a bot token from any log line or traceback."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        record.msg, record.args = tracing.scrub(record.getMessage()), None
        if record.exc_text:
            record.exc_text = tracing.scrub(record.exc_text)
        record.exc_info = None if record.exc_text else record.exc_info
        return True


for _handler in logging.getLogger().handlers:
    _handler.addFilter(_ScrubToken())

WEBHOOK_PATH = "/webhook"
ERROR_REPLY = "Sorry, something went wrong on my side 🦥 Please try again in a minute."
NAP_NOTE = "Sorry for the wait 🦥 I was napping. My free server sleeps after 15 quiet minutes, and waking up takes about a minute."
LATE_SECONDS = 30  # a message older than this when it reaches us waited for the server to wake up
_nap_note_sent = False  # once per wake-up is enough


class SeenUpdates:
    """Telegram may send the same update twice (for example while the bot wakes up)."""

    def __init__(self, size: int = 2000) -> None:
        self.size = size
        self._ids: OrderedDict[int, None] = OrderedDict()

    def check_and_add(self, update_id: int) -> bool:
        """True if this update was seen before."""
        if update_id in self._ids:
            return True
        self._ids[update_id] = None
        if len(self._ids) > self.size:
            self._ids.popitem(last=False)
        return False


seen = SeenUpdates()


async def _register_webhook() -> None:
    cfg = get_config()
    if not (cfg.external_url and cfg.telegram_bot_token and cfg.webhook_secret):
        return
    try:
        result = await telegram.set_webhook(cfg.external_url + WEBHOOK_PATH, cfg.webhook_secret)
        log.info("setWebhook ok=%s", result.get("ok"))
    except Exception:
        log.exception("setWebhook failed")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    cfg = get_config()
    tracing.init_sentry(cfg.sentry_dsn, cfg.sentry_environment)
    engine = get_engine()
    if cfg.demo_today:
        from app.demo import seed_demo

        await asyncio.to_thread(seed_demo, engine)
    asyncio.create_task(_register_webhook())
    asyncio.create_task(_warm_up(engine, today(cfg)))
    yield


async def _warm_up(engine, day) -> None:
    """Load the heavy libraries and the first forecast in the background after boot.

    On Render's free CPU, the first TabPFN forecast took 34 s, much of it importing
    pandas, scikit-learn and the TabPFN client. After this, /forecast answers from the cache.
    """
    try:
        await asyncio.to_thread(_import_heavy)
        from app import forecast

        await forecast.warm(engine, day)
    except Exception:
        log.exception("warm-up failed")


def _import_heavy() -> None:
    import google.genai  # noqa: F401
    import numpy  # noqa: F401
    import tabpfn_client  # noqa: F401


app = FastAPI(title="Ledgerly", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "today": today().isoformat()}


@app.post(WEBHOOK_PATH)
async def webhook(request: Request, background: BackgroundTasks) -> Response:
    cfg = get_config()
    header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not cfg.webhook_secret or not hmac.compare_digest(header.encode(), cfg.webhook_secret.encode()):
        return Response(status_code=401)
    update = await request.json()
    update_id = update.get("update_id")
    if update_id is None or seen.check_and_add(update_id):
        return Response(status_code=200)
    message = update.get("message") or {}
    sender = (message.get("from") or {}).get("id")
    if sender not in cfg.allowed_ids:
        return Response(status_code=200)  # owner lock: strangers get no answer
    background.add_task(process_message, message)
    return Response(status_code=200)


def _kind(message: dict) -> str:
    if message.get("photo"):
        return "photo"
    text = (message.get("text") or "").strip()
    if text.startswith("/"):
        return text.split()[0].split("@")[0]
    return "text"


async def process_message(message: dict) -> None:
    global _nap_note_sent
    cfg = get_config()
    chat_id = message["chat"]["id"]
    engine = get_engine()
    currency = (await asyncio.to_thread(get_settings, engine)).get("currency")
    if currency:  # learned from the budget answer in /start
        cfg = dataclasses.replace(cfg, currency=currency)
    ctx = Ctx(chat_id=chat_id, user_id=message["from"]["id"], today=today(cfg), engine=engine, cfg=cfg)
    kind = _kind(message)
    if not _nap_note_sent and time.time() - message.get("date", time.time()) > LATE_SECONDS:
        _nap_note_sent = True
        await telegram.send_message(chat_id, NAP_NOTE)
    with tracing.update_transaction(kind) as tx:
        try:
            await telegram.send_chat_action(chat_id)  # "typing…" while Gemma and TabPFN work
        except Exception:
            log.warning("sendChatAction failed")
        try:
            replies = await route(ctx, message, kind)
        except Exception as exc:
            log.exception("update failed")
            tracing.capture_exception(exc)
            tx.set_status("internal_error")
            replies = [ERROR_REPLY]
        for reply in replies:
            if isinstance(reply, bytes):  # a PNG, e.g. the forecast chart
                await telegram.send_photo(chat_id, reply, filename="forecast.png")
            else:
                await telegram.send_message(chat_id, reply)
    if kind in ("photo", "text"):
        # A new receipt or event changes the forecast: compute it now, so /forecast is quick.
        from app import forecast

        asyncio.create_task(forecast.warm(ctx.engine, ctx.today))


async def route(ctx: Ctx, message: dict, kind: str) -> list[str | bytes]:
    from app import agent, onboarding, receipts

    if kind == "photo":
        return await receipts.handle_photo(ctx, message)
    text = (message.get("text") or "").strip()
    if not text:
        return ["Send me a receipt photo, or tell me about a family event 🦥"]
    if kind == "/start":
        return await onboarding.handle_start(ctx)
    if kind == "/forecast":
        return await agent.handle_forecast_command(ctx)
    answer = await onboarding.handle_answer(ctx, text)
    if answer is not None:
        return answer
    answer = await receipts.handle_date_reply(ctx, text)
    if answer is not None:
        return answer
    return await agent.handle_text(ctx, text)
