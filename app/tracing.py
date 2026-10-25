"""Sentry agent tracing. One transaction per Telegram update; spans for Gemma, tools and TabPFN.

No prompts, no images and no personal data go to Sentry. Empty SENTRY_DSN = tracing off,
and every helper below becomes a no-op. A Sentry problem must never break the bot.
"""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager

log = logging.getLogger("ledgerly.tracing")

_enabled = False

# Telegram puts the bot token in the URL path (/bot<token>/sendMessage). Never let it leave.
_TOKEN = re.compile(r"bot\d+:[A-Za-z0-9_-]+")
# Only our own "telegram …" transactions are sent. Render pings /health every few seconds, and
# the raw webhook request is already covered by the "telegram …" trace.


def scrub(value):
    if isinstance(value, str):
        return _TOKEN.sub("bot[Filtered]", value)
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def init_sentry(dsn: str, environment: str) -> bool:
    global _enabled
    if not dsn:
        return False
    try:
        import sentry_sdk

        from sentry_sdk.integrations.google_genai import GoogleGenAIIntegration

        sentry_sdk.init(
            dsn=dsn,
            # Our own gen_ai.chat span records model, seconds and tokens; the automatic one
            # would count every Gemma call twice.
            disabled_integrations=[GoogleGenAIIntegration()],
            before_send=lambda event, _hint: scrub(event),
            before_breadcrumb=lambda crumb, _hint: scrub(crumb),
            environment=environment,
            traces_sample_rate=1.0,
            send_default_pii=False,
            include_local_variables=False,
            max_breadcrumbs=20,
            shutdown_timeout=2,
            before_send_transaction=_log_transaction,
        )
        _enabled = True
    except Exception:  # pragma: no cover - never let Sentry stop the bot
        log.exception("sentry init failed")
        _enabled = False
    return _enabled


def _seconds(item: dict) -> float:
    from datetime import datetime

    def as_dt(value):
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value)
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value

    return (as_dt(item.get("timestamp")) - as_dt(item.get("start_timestamp"))).total_seconds()


def _log_transaction(event: dict, _hint: dict) -> dict | None:
    """Drop health checks, scrub tokens, and log the trace id and step timings (no content)."""
    if not str(event.get("transaction", "")).startswith("telegram"):
        return None  # health checks, bare HTTP requests, 404s: not a message, not a trace
    event = scrub(event)
    try:
        total = _seconds(event)
        steps = []
        for span in event.get("spans", []):
            data = span.get("data") or {}
            step = f"{span.get('op')} {_seconds(span):.2f}s"
            if "gen_ai.usage.total_tokens" in data:
                step += f" tokens={data['gen_ai.usage.input_tokens']}+{data['gen_ai.usage.output_tokens']}"
            steps.append(step)
        trace_id = (event.get("contexts", {}).get("trace") or {}).get("trace_id")
        log.info("sentry trace=%s event=%s name=%r total=%.2fs steps=%s", trace_id, event.get("event_id"), event.get("transaction"), total, "; ".join(steps))
    except Exception:  # pragma: no cover
        log.exception("sentry log failed")
    return event


class _NoSpan:
    def set_data(self, *_args, **_kwargs) -> None:
        pass

    def set_tag(self, *_args, **_kwargs) -> None:
        pass

    def set_status(self, *_args, **_kwargs) -> None:
        pass


@contextmanager
def _span(op: str, name: str, data: dict | None = None, transaction: bool = False):
    if not _enabled:
        yield _NoSpan()
        return
    import sentry_sdk

    starter = sentry_sdk.start_transaction if transaction else sentry_sdk.start_span
    with starter(op=op, name=name) as span:
        for key, value in (data or {}).items():
            span.set_data(key, value)
        yield span


def update_transaction(kind: str):
    """One trace per Telegram update. kind: photo | text | command name."""
    return _span("telegram.update", f"telegram {kind}", {"telegram.update_kind": kind}, transaction=True)


def agent_span(agent: str = "ledgerly"):
    return _span(
        "gen_ai.invoke_agent",
        f"invoke_agent {agent}",
        {"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": agent, "gen_ai.system": "gemma"},
    )


def gemma_span(model: str, purpose: str):
    """Wrap one Gemma call. Afterwards call set_gemma_usage(span, reply)."""
    return _span(
        "gen_ai.chat",
        f"chat {model}",
        {
            "gen_ai.operation.name": "chat",
            "gen_ai.system": "gemma",
            "gen_ai.request.model": model,
            "ledgerly.purpose": purpose,
        },
    )


def set_gemma_usage(span, input_tokens: int | None, output_tokens: int | None, model: str | None = None) -> None:
    if input_tokens is not None:
        span.set_data("gen_ai.usage.input_tokens", input_tokens)
    if output_tokens is not None:
        span.set_data("gen_ai.usage.output_tokens", output_tokens)
    if input_tokens is not None and output_tokens is not None:
        span.set_data("gen_ai.usage.total_tokens", input_tokens + output_tokens)
    if model:
        span.set_data("gen_ai.response.model", model)


def tool_span(tool: str):
    return _span(
        "gen_ai.execute_tool",
        f"execute_tool {tool}",
        {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": tool},
    )


def tabpfn_span(rows: int, columns: int, predict_rows: int):
    return _span(
        "tabpfn.predict",
        "TabPFN fit + predict",
        {"tabpfn.train_rows": rows, "tabpfn.columns": columns, "tabpfn.predict_rows": predict_rows},
    )


def capture_message(message: str, level: str = "warning", **tags) -> None:
    if not _enabled:
        return
    try:
        import sentry_sdk

        with sentry_sdk.new_scope() as scope:
            for key, value in tags.items():
                scope.set_tag(key, value)
            sentry_sdk.capture_message(message, level=level)
    except Exception:  # pragma: no cover
        log.exception("sentry capture failed")


def capture_exception(exc: BaseException) -> None:
    if not _enabled:
        return
    try:
        import sentry_sdk

        sentry_sdk.capture_exception(exc)
    except Exception:  # pragma: no cover
        log.exception("sentry capture failed")
