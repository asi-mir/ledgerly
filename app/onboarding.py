"""/start: two questions, budget and payday. STUB."""

from __future__ import annotations

from app.context import Ctx


async def handle_start(ctx: Ctx) -> list[str]:
    raise NotImplementedError


async def handle_answer(ctx: Ctx, text: str) -> list[str] | None:
    """If onboarding is waiting for an answer, handle it. Else None."""
    return None
