"""Gemma providers: request shape and token counts, with the network mocked."""

import asyncio
import base64
from types import SimpleNamespace

import httpx
import pytest

from app.providers import aistudio, ollama


def test_aistudio_request_has_image_then_prompt_and_minimal_thinking():
    contents, config = aistudio.build_request("read this", b"img", "image/jpeg")
    assert contents[0].inline_data.data == b"img"
    assert contents[0].inline_data.mime_type == "image/jpeg"
    assert contents[1] == "read this"
    assert config.thinking_config.thinking_level.value == "MINIMAL"
    assert config.temperature == 0
    assert aistudio.build_request("hi", None, "image/jpeg")[0] == ["hi"]


class FakeModels:
    def __init__(self, failures: list[Exception]):
        self.failures = failures
        self.calls = 0

    async def generate_content(self, *, model, contents, config):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        usage = SimpleNamespace(prompt_token_count=1200, candidates_token_count=150)
        return SimpleNamespace(text='{"total": 1}', model_version=model, usage_metadata=usage)


def _fake_client(monkeypatch, failures):
    models = FakeModels(failures)
    client = SimpleNamespace(aio=SimpleNamespace(models=models))
    monkeypatch.setattr(aistudio, "_client", lambda: client)
    monkeypatch.setattr(aistudio, "RETRY_DELAY", 0)
    monkeypatch.setattr(aistudio, "RATE_LIMIT_DELAY", 0)
    monkeypatch.setattr(aistudio, "MAX_DELAY", 0)
    return models


def test_aistudio_tokens_and_one_retry_on_server_error(monkeypatch):
    from google.genai import errors

    models = _fake_client(monkeypatch, [errors.ServerError(503, {"error": {"message": "busy"}})])
    reply = asyncio.run(aistudio.generate("p", image=b"i", mime_type="image/jpeg", model="gemma-4-26b-a4b-it"))
    assert models.calls == 2
    assert (reply.text, reply.model, reply.input_tokens, reply.output_tokens) == (
        '{"total": 1}', "gemma-4-26b-a4b-it", 1200, 150)


def test_aistudio_does_not_retry_client_errors(monkeypatch):
    from google.genai import errors

    models = _fake_client(monkeypatch, [errors.ClientError(400, {"error": {"message": "bad"}})])
    with pytest.raises(errors.ClientError):
        asyncio.run(aistudio.generate("p", image=None, mime_type="image/jpeg", model="m"))
    assert models.calls == 1


def test_aistudio_gives_up_after_second_failure(monkeypatch):
    from google.genai import errors

    failures = [errors.ClientError(429, {"error": {"message": "slow down"}}), httpx.ReadTimeout("slow")]
    models = _fake_client(monkeypatch, failures)
    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(aistudio.generate("p", image=None, mime_type="image/jpeg", model="m"))
    assert models.calls == 2


def test_ollama_payload_is_openai_style_with_data_url():
    payload = ollama.build_payload("read this", b"img", "image/jpeg", "gemma4:e4b")
    assert payload["model"] == "gemma4:e4b" and payload["stream"] is False
    text, image = payload["messages"][0]["content"]
    assert text == {"type": "text", "text": "read this"}
    assert image["image_url"]["url"] == "data:image/jpeg;base64," + base64.b64encode(b"img").decode()


def test_ollama_generate_posts_to_chat_completions(monkeypatch):
    from app.config import get_config

    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/")
    get_config.cache_clear()
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "model": "gemma4:e4b",
                "choices": [{"message": {"role": "assistant", "content": '{"total": 1}'}}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 80},
            },
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(ollama.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler)))
    reply = asyncio.run(ollama.generate("p", image=b"i", mime_type="image/jpeg", model="gemma4:e4b"))
    assert seen["url"] == "http://localhost:11434/v1/chat/completions"
    assert (reply.text, reply.input_tokens, reply.output_tokens) == ('{"total": 1}', 900, 80)
    monkeypatch.undo()
    get_config.cache_clear()


def test_aistudio_waits_as_long_as_a_429_asks():
    from google.genai import errors

    detail = {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "12s"}
    rate_limited = errors.ClientError(429, {"error": {"message": "quota", "details": [detail]}})
    assert aistudio._delay(rate_limited) == 13
    assert aistudio._delay(errors.ClientError(429, {"error": {"message": "quota"}})) == aistudio.RATE_LIMIT_DELAY
    assert aistudio._delay(errors.ServerError(500, {"error": {"message": "x"}})) == aistudio.RETRY_DELAY
