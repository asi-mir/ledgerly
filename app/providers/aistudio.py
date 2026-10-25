"""Gemma 4 on Google AI Studio (google-genai SDK, async client)."""

from __future__ import annotations

import asyncio
import logging

from app.config import get_config
from app.gemma import GemmaReply

log = logging.getLogger("ledgerly.aistudio")

TIMEOUT_MS = 90_000
RETRY_DELAY = 2.0  # after a server error or a timeout
RATE_LIMIT_DELAY = 15.0  # after a 429, unless Google says how long to wait
MAX_DELAY = 30.0


_cached: tuple[object, object] | None = None  # (event loop, client)


def _client():
    """One client per event loop (the async client cannot move between loops)."""
    global _cached
    from google import genai
    from google.genai import types

    loop = asyncio.get_running_loop()
    if _cached is None or _cached[0] is not loop:
        client = genai.Client(api_key=get_config().gemini_api_key, http_options=types.HttpOptions(timeout=TIMEOUT_MS))
        _cached = (loop, client)
    return _cached[1]


def build_request(prompt: str, image: bytes | None, mime_type: str) -> tuple[list, object]:
    """The contents and config for one call: image first (if any), then the text."""
    from google.genai import types

    contents: list = []
    if image is not None:
        contents.append(types.Part.from_bytes(data=image, mime_type=mime_type))
    contents.append(prompt)
    config = types.GenerateContentConfig(
        temperature=0,
        thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL),
    )
    return contents, config


def _retryable(exc: Exception) -> bool:
    from google.genai import errors

    if isinstance(exc, errors.APIError):
        return exc.code == 429 or (exc.code or 0) >= 500
    import httpx

    return isinstance(exc, (httpx.TimeoutException, httpx.TransportError, asyncio.TimeoutError))


def _delay(exc: Exception) -> float:
    """Seconds to wait before the one retry. A 429 says how long in its RetryInfo detail."""
    if getattr(exc, "code", None) != 429:
        return RETRY_DELAY
    try:
        for detail in exc.details["error"]["details"]:
            if "retryDelay" in detail:
                return min(MAX_DELAY, float(str(detail["retryDelay"]).rstrip("s")) + 1)
    except (AttributeError, KeyError, TypeError, ValueError):
        pass
    return RATE_LIMIT_DELAY


async def generate(prompt: str, *, image: bytes | None, mime_type: str, model: str) -> GemmaReply:
    contents, config = build_request(prompt, image, mime_type)
    for attempt in (1, 2):
        try:
            response = await _client().aio.models.generate_content(model=model, contents=contents, config=config)
            break
        except Exception as exc:
            if attempt == 2 or not _retryable(exc):
                raise
            log.warning("Gemma call failed (%s), retrying once", type(exc).__name__)
            await asyncio.sleep(_delay(exc))
    usage = getattr(response, "usage_metadata", None)
    return GemmaReply(
        text=response.text or "",
        model=getattr(response, "model_version", None) or model,
        input_tokens=getattr(usage, "prompt_token_count", None),
        output_tokens=getattr(usage, "candidates_token_count", None),
    )
