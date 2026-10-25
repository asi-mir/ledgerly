"""Receipt photos: Gemma reads, code checks the sum, the bot asks for a missing date. STUB."""

from __future__ import annotations

from app.context import Ctx


async def handle_photo(ctx: Ctx, message: dict) -> list[str]:
    """A Telegram message with a photo. Returns the reply texts."""
    raise NotImplementedError


async def handle_date_reply(ctx: Ctx, text: str) -> list[str] | None:
    """If this chat is waiting for a receipt date, use the text as the answer. Else None."""
    return None
