"""Gemma on a local Ollama, through its OpenAI-compatible API. Receipts never leave your laptop."""

from __future__ import annotations

import base64

import httpx

from app.config import get_config
from app.gemma import GemmaReply

TIMEOUT = httpx.Timeout(300.0, connect=10.0)  # a laptop can be slow on a big image


def build_payload(prompt: str, image: bytes | None, mime_type: str, model: str) -> dict:
    content: list[dict] = [{"type": "text", "text": prompt}]
    if image is not None:
        data_url = f"data:{mime_type};base64,{base64.b64encode(image).decode()}"
        content.append({"type": "image_url", "image_url": {"url": data_url}})
    return {"model": model, "messages": [{"role": "user", "content": content}], "temperature": 0, "stream": False}


async def generate(prompt: str, *, image: bytes | None, mime_type: str, model: str) -> GemmaReply:
    url = get_config().ollama_base_url.rstrip("/") + "/v1/chat/completions"
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(url, json=build_payload(prompt, image, mime_type, model))
        r.raise_for_status()
    data = r.json()
    usage = data.get("usage") or {}
    return GemmaReply(
        text=data["choices"][0]["message"].get("content") or "",
        model=data.get("model") or model,
        input_tokens=usage.get("prompt_tokens"),
        output_tokens=usage.get("completion_tokens"),
    )
