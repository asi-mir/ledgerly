"""Gemma, the only language model in Ledgerly. Google AI Studio by default, or a local Ollama.

Every call goes through ask_gemma(), which picks the provider from GEMMA_PROVIDER and records
one Sentry span (model, seconds, tokens; never the prompt or the image).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from app import tracing
from app.config import get_config


@dataclass
class GemmaReply:
    text: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    seconds: float = 0.0


async def ask_gemma(prompt: str, image: bytes | None = None, mime_type: str = "image/jpeg", *, purpose: str) -> GemmaReply:
    """One Gemma call. purpose: 'receipt' or 'chat' (shows in the trace)."""
    cfg = get_config()
    if cfg.gemma_provider == "ollama":
        from app.providers import ollama as provider

        model = cfg.ollama_model
    else:
        from app.providers import aistudio as provider

        model = cfg.gemma_model
    with tracing.gemma_span(model, purpose) as span:
        started = time.perf_counter()
        reply = await provider.generate(prompt, image=image, mime_type=mime_type, model=model)
        reply.seconds = round(time.perf_counter() - started, 3)
        tracing.set_gemma_usage(span, reply.input_tokens, reply.output_tokens, reply.model)
    return reply


def extract_json(text: str) -> dict:
    """Take the first complete {...} object from the model's text. Raises ValueError if none."""
    start = text.find("{")
    while start != -1:
        depth, in_string, escape = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
            elif ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(value, dict):
                        return value
                    break
        start = text.find("{", start + 1)
    raise ValueError("no JSON object in model reply")
