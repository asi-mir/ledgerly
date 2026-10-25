"""Chat: one JSON-intent Gemma prompt; code routes to add_event or forecast(extra). STUB."""

from __future__ import annotations

from app.context import Ctx


async def handle_forecast_command(ctx: Ctx) -> list[str]:
    raise NotImplementedError


async def handle_text(ctx: Ctx, text: str) -> list[str]:
    raise NotImplementedError
