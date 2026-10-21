"""Optional OpenTelemetry tracing for ai-provider-service.

Traces are exported to any OTLP/HTTP collector — in the Wolfini stack that is a
self-hosted Arize Phoenix on oracle-vm (`/opt/phoenix-data`, loopback 6006).

Tracing is **opt-in**: everything is a no-op unless `OTEL_EXPORTER_OTLP_ENDPOINT`
is set, so tests and deployments without a collector behave exactly as before.

Environment:
  OTEL_EXPORTER_OTLP_ENDPOINT   base URL or full traces URL
                                (e.g. http://phoenix:6006 or http://phoenix:6006/v1/traces)
  OTEL_SERVICE_NAME             resource service.name (default: ai-provider)
  OTEL_PYTHON_FLASK_EXCLUDED_URLS  Flask URL regexes excluded from spans
                                (default: health — avoids a span per health probe)

Usage (in the dispatcher):

    with llm_call_span(provider_id, model, user_id=..., origin_app=...) as span:
        result = client.create_message(...)
        record_llm_usage(span, result.get('usage') or {}, model=..., latency_ms=...)
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

DEFAULT_SERVICE_NAME = 'ai-provider'
DEFAULT_EXCLUDED_URLS = 'health'
DEFAULT_CAPTURE_MAX_CHARS = 4000

# Module-level tracer handle; None means tracing is disabled (no-op helpers).
_state: dict[str, Any] = {'tracer': None}


def normalize_endpoint(endpoint: str) -> str:
    """Return a full OTLP/HTTP traces URL.

    Accepts both a collector base URL (``http://phoenix:6006``) and an already
    complete traces URL (``http://phoenix:6006/v1/traces``), because the OTLP
    HTTP exporter uses an explicitly passed endpoint verbatim (it does not
    append ``/v1/traces`` itself).
    """
    cleaned = (endpoint or '').strip().rstrip('/')
    if not cleaned:
        return ''
    if cleaned.endswith('/v1/traces'):
        return cleaned
    return f'{cleaned}/v1/traces'


def init_tracing(app=None):
    """Configure the tracer provider and (optionally) instrument Flask.

    Returns the tracer, or None when tracing is disabled/unavailable. Never
    raises: a tracing problem must not take the API down.
    """
    endpoint = normalize_endpoint(os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT', ''))
    if not endpoint:
        logger.info('tracing disabled (OTEL_EXPORTER_OTLP_ENDPOINT not set)')
        return None

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning('OpenTelemetry packages not installed — tracing disabled')
        return None

    service_name = (os.getenv('OTEL_SERVICE_NAME') or DEFAULT_SERVICE_NAME).strip()
    try:
        provider = TracerProvider(resource=Resource.create({'service.name': service_name}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(provider)
        tracer = trace.get_tracer('ai-provider-service')
    except Exception:
        logger.warning('tracing setup failed — continuing without tracing', exc_info=True)
        return None

    _state['tracer'] = tracer

    if app is not None:
        excluded = os.getenv('OTEL_PYTHON_FLASK_EXCLUDED_URLS', DEFAULT_EXCLUDED_URLS)
        try:
            from opentelemetry.instrumentation.flask import FlaskInstrumentor

            FlaskInstrumentor().instrument_app(app, excluded_urls=excluded or None)
        except Exception:
            logger.warning('Flask auto-instrumentation failed — LLM spans still active',
                           exc_info=True)

    logger.info('tracing enabled -> %s (service=%s)', endpoint, service_name)
    return tracer


@contextmanager
def llm_call_span(provider_id: str, model: str, user_id: Optional[str] = None,
                  origin_app: Optional[str] = None) -> Iterator[Any]:
    """Span around one provider call; yields None when tracing is disabled.

    ``gen_ai.*`` attributes follow the OpenTelemetry GenAI semantic conventions
    so Phoenix-style UIs can group by provider and model; Wolfini-specific
    fields use the ``wolfini.*`` namespace.
    """
    tracer = _state.get('tracer')
    if tracer is None:
        yield None
        return

    try:
        from opentelemetry.trace import Status, StatusCode
    except Exception:  # pragma: no cover - opentelemetry is a hard dependency here
        yield None
        return

    with tracer.start_as_current_span(f'chat {provider_id}/{model}') as span:
        span.set_attribute('gen_ai.operation.name', 'chat')
        span.set_attribute('gen_ai.system', str(provider_id))
        span.set_attribute('gen_ai.request.model', str(model))
        if user_id:
            span.set_attribute('wolfini.user_id', str(user_id))
        if origin_app:
            span.set_attribute('wolfini.origin_app', str(origin_app))
        try:
            yield span
        except Exception as exc:
            span.set_attribute('error.type', type(exc).__name__)
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)[:200]))
            raise


def record_llm_usage(span, usage: Optional[dict], *, model: Optional[str] = None,
                     latency_ms: Optional[int] = None,
                     cost_usd: Optional[float] = None) -> None:
    """Attach token/latency/cost attributes to an LLM span (no-op without span)."""
    if span is None:
        return
    usage = usage or {}
    try:
        if model:
            span.set_attribute('gen_ai.response.model', str(model))
        input_tokens = usage.get('input_tokens')
        output_tokens = usage.get('output_tokens')
        if input_tokens is not None:
            span.set_attribute('gen_ai.usage.input_tokens', int(input_tokens))
        if output_tokens is not None:
            span.set_attribute('gen_ai.usage.output_tokens', int(output_tokens))
        if latency_ms is not None:
            span.set_attribute('wolfini.latency_ms', int(latency_ms))
        reported_cost = cost_usd if cost_usd is not None else usage.get('cost_usd')
        if reported_cost is not None:
            span.set_attribute('wolfini.cost_usd', float(reported_cost))
    except Exception:
        logger.debug('failed to set LLM span attributes', exc_info=True)



def capture_prompts_enabled() -> bool:
    """True when OTEL_CAPTURE_PROMPTS explicitly enables prompt/response capture.

    Off by default: prompts contain user content, and storing them in a tracing
    backend is a deliberate privacy decision (memory audit notes are a separate,
    MEMORY_ENABLED-gated store).
    """
    raw = os.getenv('OTEL_CAPTURE_PROMPTS', 'false')
    return raw.strip().lower() in ('1', 'true', 'yes', 'on')


def _truncate(text: str, limit: int) -> str:
    if text is None:
        return ''
    text = str(text)
    if limit <= 0 or len(text) <= limit:
        return text
    return f'{text[:limit]}…[truncated {len(text) - limit} chars]'


def record_llm_io(span, messages, response_text, *, max_chars: Optional[int] = None) -> None:
    """Attach prompt/response text to an LLM span (only with OTEL_CAPTURE_PROMPTS).

    Uses the OpenInference attribute names Phoenix renders as Input/Output
    (`input.value`, `input.mime_type`, `output.value`, `output.mime_type`).
    Both sides are truncated to OTEL_CAPTURE_MAX_CHARS (default 4000) so a
    web-search prompt with ~180 documents cannot bloat the trace store.
    """
    if span is None or not capture_prompts_enabled():
        return
    try:
        limit = max_chars if max_chars is not None else int(
            os.getenv('OTEL_CAPTURE_MAX_CHARS') or DEFAULT_CAPTURE_MAX_CHARS
        )
    except ValueError:
        limit = DEFAULT_CAPTURE_MAX_CHARS

    try:
        prompt_json = json.dumps(messages, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        prompt_json = str(messages)

    try:
        span.set_attribute('input.mime_type', 'application/json')
        span.set_attribute('input.value', _truncate(prompt_json, limit))
        span.set_attribute('output.mime_type', 'text/plain')
        span.set_attribute('output.value', _truncate(response_text, limit))
    except Exception:
        logger.debug('failed to set LLM input/output attributes', exc_info=True)

