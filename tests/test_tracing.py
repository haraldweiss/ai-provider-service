"""Tests for the optional OpenTelemetry tracing helpers (observability/tracing.py).

Covers the two contracts that matter in production:
  1. Tracing is a strict no-op unless OTEL_EXPORTER_OTLP_ENDPOINT is set —
     a collector outage or a missing env var must never affect /chat.
  2. With a tracer configured, LLM spans carry the GenAI attributes Phoenix
     groups by (provider, model, tokens, cost) and errors are marked.
"""

import pytest


def test_normalize_endpoint_accepts_base_and_full_urls():
    from observability.tracing import normalize_endpoint

    assert normalize_endpoint('http://phoenix:6006') == 'http://phoenix:6006/v1/traces'
    assert normalize_endpoint('http://phoenix:6006/') == 'http://phoenix:6006/v1/traces'
    assert normalize_endpoint('https://host/otel/v1/traces') == 'https://host/otel/v1/traces'
    assert normalize_endpoint('') == ''
    assert normalize_endpoint('   ') == ''


def test_init_tracing_is_noop_without_endpoint(monkeypatch):
    import observability.tracing as tracing

    monkeypatch.delenv('OTEL_EXPORTER_OTLP_ENDPOINT', raising=False)
    monkeypatch.setattr(tracing, '_state', {'tracer': 'sentinel'})

    assert tracing.init_tracing() is None
    assert tracing._state == {'tracer': 'sentinel'}


def test_init_tracing_survives_missing_packages(monkeypatch):
    """A missing OTel install must log and return None, not raise."""
    import builtins
    import observability.tracing as tracing

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith('opentelemetry'):
            raise ImportError('simulated missing package')
        return real_import(name, *args, **kwargs)

    monkeypatch.setenv('OTEL_EXPORTER_OTLP_ENDPOINT', 'http://phoenix:6006')
    monkeypatch.setattr(builtins, '__import__', fake_import)
    monkeypatch.setattr(tracing, '_state', {'tracer': None})

    assert tracing.init_tracing() is None


def test_span_helpers_are_noops_without_tracer(monkeypatch):
    import observability.tracing as tracing

    monkeypatch.setattr(tracing, '_state', {'tracer': None})

    with tracing.llm_call_span('ollama', 'oracle-llama3.2:3b') as span:
        assert span is None
    tracing.record_llm_usage(None, {'input_tokens': 5})  # must not raise
    tracing.record_llm_usage(None, None)


@pytest.fixture
def span_exporter(monkeypatch):
    """Wire the module to an in-memory tracer and return its span exporter."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    import observability.tracing as tracing

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(tracing, '_state', {'tracer': provider.get_tracer('test')})
    return exporter


def test_span_records_genai_attributes(span_exporter):
    import observability.tracing as tracing

    with tracing.llm_call_span('ollama', 'oracle-llama3.2:3b',
                               user_id='u1', origin_app='open-webui') as span:
        tracing.record_llm_usage(
            span,
            {'input_tokens': 11, 'output_tokens': 3, 'cost_usd': 0.0},
            model='oracle-llama3.2:3b',
            latency_ms=42,
        )

    (recorded,) = span_exporter.get_finished_spans()
    attrs = dict(recorded.attributes)
    assert recorded.name == 'chat ollama/oracle-llama3.2:3b'
    assert attrs['gen_ai.operation.name'] == 'chat'
    assert attrs['gen_ai.system'] == 'ollama'
    assert attrs['gen_ai.request.model'] == 'oracle-llama3.2:3b'
    assert attrs['gen_ai.response.model'] == 'oracle-llama3.2:3b'
    assert attrs['gen_ai.usage.input_tokens'] == 11
    assert attrs['gen_ai.usage.output_tokens'] == 3
    assert attrs['wolfini.latency_ms'] == 42
    assert attrs['wolfini.user_id'] == 'u1'
    assert attrs['wolfini.origin_app'] == 'open-webui'


def test_span_marks_failures_and_reraises(span_exporter):
    import observability.tracing as tracing

    with pytest.raises(RuntimeError, match='boom'):
        with tracing.llm_call_span('ollama', 'broken-model') as span:
            assert span is not None
            raise RuntimeError('boom')

    (recorded,) = span_exporter.get_finished_spans()
    assert recorded.status.status_code.name == 'ERROR'
    assert dict(recorded.attributes)['error.type'] == 'RuntimeError'


def test_prompt_capture_is_off_by_default(span_exporter, monkeypatch):
    import observability.tracing as tracing

    monkeypatch.delenv('OTEL_CAPTURE_PROMPTS', raising=False)

    with tracing.llm_call_span('ollama', 'm') as span:
        tracing.record_llm_io(span, [{'role': 'user', 'content': 'SECRET PROMPT'}],
                              'SECRET ANSWER')

    (recorded,) = span_exporter.get_finished_spans()
    attrs = dict(recorded.attributes)
    assert 'input.value' not in attrs
    assert 'output.value' not in attrs


def test_prompt_capture_records_and_truncates(span_exporter, monkeypatch):
    import observability.tracing as tracing

    monkeypatch.setenv('OTEL_CAPTURE_PROMPTS', 'true')
    monkeypatch.setenv('OTEL_CAPTURE_MAX_CHARS', '20')

    long_prompt = 'x' * 50
    with tracing.llm_call_span('ollama', 'm') as span:
        tracing.record_llm_io(span, [{'role': 'user', 'content': long_prompt}],
                              'y' * 50)

    (recorded,) = span_exporter.get_finished_spans()
    attrs = dict(recorded.attributes)
    assert attrs['input.mime_type'] == 'application/json'
    assert attrs['output.mime_type'] == 'text/plain'
    # prompt is JSON-serialised, so it starts with '[' — the content must still be there
    assert attrs['input.value'].startswith('[')
    assert len(attrs['input.value']) < 60          # capped at 20 chars + marker
    assert 'truncated' in attrs['input.value']
    assert 'truncated' in attrs['output.value']
    assert attrs['output.value'].startswith('y')

