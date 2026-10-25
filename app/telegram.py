"""A thin Telegram Bot API client (httpx)."""

from __future__ import annotations

import logging

import httpx

from app.config import get_config

log = logging.getLogger("ledgerly.telegram")

API = "https://api.telegram.org"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def _url(method: str) -> str:
    return f"{API}/bot{get_config().telegram_bot_token}/{method}"


async def call(method: str, **payload) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(_url(method), json=payload)
    data = r.json()
    if not data.get("ok"):
        log.warning("telegram %s failed: %s", method, data.get("description"))
    return data


async def send_message(chat_id: int, text: str) -> dict:
    log.info("reply chat=%s text=%r", chat_id, text[:600])
    return await call("sendMessage", chat_id=chat_id, text=text, disable_web_page_preview=True)


async def send_chat_action(chat_id: int, action: str = "typing") -> dict:
    return await call("sendChatAction", chat_id=chat_id, action=action)


async def send_photo(chat_id: int, photo: bytes, caption: str | None = None, filename: str = "photo.png") -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        data = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption
        r = await client.post(_url("sendPhoto"), data=data, files={"photo": (filename, photo)})
    return r.json()


async def get_file_bytes(file_id: str) -> bytes:
    info = await call("getFile", file_id=file_id)
    path = info["result"]["file_path"]
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(f"{API}/file/bot{get_config().telegram_bot_token}/{path}")
        r.raise_for_status()
        return r.content


async def set_webhook(url: str, secret: str) -> dict:
    return await call("setWebhook", url=url, secret_token=secret, allowed_updates=["message"])
