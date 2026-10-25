"""Gemma on a local Ollama (OpenAI-compatible API). STUB: implemented in the receipts work."""

from __future__ import annotations

from app.gemma import GemmaReply


async def generate(prompt: str, *, image: bytes | None, mime_type: str, model: str) -> GemmaReply:
    raise NotImplementedError
