"""OpenAI-compatible API endpoint for Pi and other OpenAI-compatible clients.

Endpoints:
  GET  /v1/models              — list available models
  POST /v1/chat/completions    — streaming chat completion (SSE)
"""

import json
import logging
import time
import uuid
from flask import Blueprint, jsonify, request, Response, stream_with_context
from api.auth import require_token
# from api.gate import require_provider_access
from api.provider_visibility import availability_hint, hidden_key_provider_rows
from api.validation import parse_max_tokens
from dispatcher import (
    dispatch, _extract_response_text, _load_config, ProviderRequestError,
    ProviderUnavailableError,
)
from providers import PROVIDER_REGISTRY, get_client
from flask import g
import health_tracker

logger = logging.getLogger(__name__)

openai_bp = Blueprint('openai', __name__)


def _parse_model(model: str) -> tuple[str, str, str | None]:
    """Split 'zai/glm-4-flash' → ('zai', 'glm-4-flash', origin_app)."""
    if '/' in model:
        prefix, name = model.split('/', 1)
        if prefix == 'wolfinichat':
            return 'ollama', name, 'chat.wolfinisoftware.de'
        return prefix, name, None
    return 'claude', model, None


def _principal_user_id() -> str:
    """Extract user_id from g.principal.

    For SERVICE_TOKEN (credential='service'), a missing user_id defaults to
    ADMIN_USER_ID — the server's own token sees all providers. This is needed
    for GET requests like /v1/models where no body user_id can be asserted.

    For user tokens (credential='user_token'), a missing user_id is a real
    error and raises ValueError -> 401.
    """
    principal = getattr(g, 'principal', None)
    if principal is not None:
        uid = getattr(principal, 'user_id', None)
        if uid:
            return uid
        if getattr(principal, 'credential', '') == 'service':
            from config import Config
            return Config.ADMIN_USER_ID
    raise ValueError('principal user_id is missing')


def _model_id(provider_id: str, model_name: str) -> str:
    return f'{provider_id}/{model_name}'


def _available_model_rows(user_id: str) -> list[dict]:
    now = int(time.time())
    rows = []
    seen = set()
    for provider_id in PROVIDER_REGISTRY:
        cfg = _load_config(user_id, provider_id)
        if cfg is None:
            continue
        if not health_tracker.is_healthy(provider_id):
            logger.info('Skipping unhealthy provider %s in /v1/models', provider_id)
            continue
        try:
            models = get_client(provider_id, cfg).get_models()
        except Exception as e:
            logger.info('Skipping unavailable provider %s in /v1/models: %s',
                        provider_id, type(e).__name__)
            continue
        for model_name in models:
            if not model_name:
                continue
            mid = _model_id(provider_id, str(model_name))
            if mid in seen:
                continue
            seen.add(mid)
            rows.append({
                'id': mid,
                'object': 'model',
                'created': now,
                'owned_by': provider_id,
            })
    return rows


def _openai_stream_chunk(
    model: str, content: str = '', index: int = 0,
    finish_reason: str | None = None, tool_calls: list[dict] | None = None,
):
    """Build an OpenAI-style SSE chunk."""
    delta = {}
    if content or (finish_reason is None and not tool_calls):
        delta = {'role': 'assistant', 'content': content}
    if tool_calls:
        delta['tool_calls'] = tool_calls
    choice: dict = {'index': index, 'delta': delta, 'finish_reason': finish_reason}
    return {
        'id': f'chatcmpl-{uuid.uuid4().hex[:12]}',
        'object': 'chat.completion.chunk',
        'created': int(time.time()),
        'model': model,
        'choices': [choice],
    }


def _openai_finish_reason(result_data: dict) -> str:
    reason = result_data.get('stop_reason') or result_data.get('done_reason')
    if reason in ('length', 'max_tokens'):
        return 'length'
    if reason in ('tool_use', 'tool_calls'):
        return 'tool_calls'
    if reason == 'content_filter':
        return 'content_filter'
    return 'stop'


def _tool_arguments(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value or {})


def _openai_tool_calls(result_data: dict) -> list[dict]:
    calls = []
    for call in result_data.get('tool_calls') or []:
        if not isinstance(call, dict):
            continue
        name = call.get('name') or ''
        if not name:
            continue
        calls.append({
            'id': call.get('id') or f'call_{len(calls)}',
            'type': 'function',
            'function': {
                'name': name,
                'arguments': _tool_arguments(call.get('input', {})),
            },
        })
    return calls


def _openai_tool_call_deltas(tool_calls: list[dict]) -> list[dict]:
    deltas = []
    for index, call in enumerate(tool_calls):
        item = dict(call)
        item['index'] = index
        deltas.append(item)
    return deltas


def _content_part_text(part) -> str:
    if isinstance(part, str):
        return part
    if isinstance(part, dict):
        text = part.get('text')
        if isinstance(text, str):
            return text
    return ''


def _normalize_message_content(content) -> str:
    if isinstance(content, str):
        return content
    if content is None:
        return ''
    if isinstance(content, list):
        return '\n'.join(
            text for text in (_content_part_text(part) for part in content) if text
        )
    return str(content)


def _normalize_messages(messages: list) -> list:
    normalized = []
    for message in messages:
        if not isinstance(message, dict):
            normalized.append(message)
            continue
        item = dict(message)
        item['content'] = _normalize_message_content(item.get('content'))
        normalized.append(item)
    return normalized


def _omlx_request_metadata(body: dict) -> dict:
    """Return oMLX-safe request diagnostics without retaining content values."""
    messages = body.get('messages') if isinstance(body.get('messages'), list) else []
    return {
        'request_keys': sorted(str(key) for key in body),
        'message_count': len(messages),
        'message_roles': [str(message.get('role', '')) if isinstance(message, dict) else type(message).__name__
                          for message in messages],
        'message_content_types': [type(message.get('content')).__name__ if isinstance(message, dict)
                                  else type(message).__name__ for message in messages],
        'message_content_lengths': [len(message.get('content')) if isinstance(message, dict)
                                    and hasattr(message.get('content'), '__len__') else None for message in messages],
        'stream': body.get('stream') is True,
        'max_tokens_type': type(body.get('max_tokens')).__name__,
        'tool_count': len(body.get('tools')) if isinstance(body.get('tools'), list) else 0,
    }


# ─── Endpoints ────────────────────────────────────────────────────────────


@openai_bp.get('/v1/models')
@require_token
def list_models():
    """Return list of available models in OpenAI format."""
    try:
        user_id = _principal_user_id()
    except ValueError:
        return jsonify({'error': {'message': 'authenticated principal has no user_id',
                                   'type': 'invalid_request'}}), 401
    hidden = hidden_key_provider_rows(user_id)
    return jsonify({
        'object': 'list',
        'data': _available_model_rows(user_id),
        'hidden_providers': hidden,
        'availability_hint': availability_hint(hidden),
    })


@openai_bp.post('/v1/chat/completions')
@require_token
# @require_provider_access('provider')
def chat_completions():
    """OpenAI-compatible chat completion (streaming + non-streaming).

    Model format:  provider/model_name
    e.g. "zai/glm-4-flash", "ollama/qwen3.6:latest"
    """
    body = request.get_json(silent=True) or {}
    model = body.get('model', '')
    raw_messages = body.get('messages')
    stream = body.get('stream', False)
    tools = body.get('tools') if isinstance(body.get('tools'), list) else None

    if not model:
        return jsonify({'error': {'message': 'model is required', 'type': 'invalid_request'}}), 400
    if raw_messages is not None and not isinstance(raw_messages, list):
        return jsonify({'error': {'message': 'messages must be a list',
                                  'type': 'invalid_request'}}), 400
    messages = _normalize_messages(raw_messages or [])
    if not messages:
        return jsonify({'error': {'message': 'messages is required', 'type': 'invalid_request'}}), 400
    try:
        max_tokens = parse_max_tokens(body.get('max_tokens'), default=4096)
    except ValueError as e:
        return jsonify({'error': {'message': str(e), 'type': 'invalid_request'}}), 400

    provider_id, model_name, origin_app = _parse_model(model)

    if provider_id not in PROVIDER_REGISTRY:
        return jsonify({
            'error': {'message': f'Unknown provider: {provider_id}', 'type': 'invalid_request'},
        }), 400

    try:
        user_id = _principal_user_id()
    except ValueError:
        return jsonify({'error': {'message': 'authenticated principal has no user_id',
                                   'type': 'invalid_request'}}), 401

    try:
        result = dispatch(
            user_id=user_id,
            provider_id=provider_id,
            model=model_name,
            messages=messages,
            max_tokens=max_tokens,
            origin_app=origin_app,
            tools=tools,
        )
    except ProviderRequestError as e:
        if e.provider_id == 'omlx':
            logger.warning('oMLX rejected request metadata: %s', _omlx_request_metadata(body))
        return jsonify({
            'error': {'message': str(e), 'type': 'invalid_request'},
        }), e.status_code
    except ProviderUnavailableError as e:
        logger.warning(f'v1/chat/completions provider unavailable: {e}')
        return jsonify({
            'error': {'message': str(e), 'type': 'service_unavailable'},
        }), 503
    except Exception as e:
        logger.exception(f'v1/chat/completions dispatch failed: {e}')
        return jsonify({
            'error': {'message': str(e), 'type': 'server_error'},
        }), 500

    result_data = result.get('result', {})
    text = _extract_response_text(result_data)
    finish_reason = _openai_finish_reason(result_data)
    tool_calls = _openai_tool_calls(result_data)

    usage = result_data.get('usage', {})
    openai_usage = {
        'prompt_tokens': usage.get('input_tokens', 0) or 0,
        'completion_tokens': usage.get('output_tokens', 0) or 0,
        'total_tokens': (usage.get('input_tokens', 0) or 0) + (usage.get('output_tokens', 0) or 0),
    }

    if stream:
        def generate():
            # Role chunk (empty content, signals start)
            chunk = _openai_stream_chunk(model, '')
            yield f'data: {json.dumps(chunk)}\n\n'

            # Content/tool chunk (full response — backend is sync)
            if text:
                content_chunk = _openai_stream_chunk(model, text)
                yield f'data: {json.dumps(content_chunk)}\n\n'
            if tool_calls:
                tool_chunk = _openai_stream_chunk(
                    model, tool_calls=_openai_tool_call_deltas(tool_calls),
                )
                yield f'data: {json.dumps(tool_chunk)}\n\n'

            # Final chunk with usage
            finish = _openai_stream_chunk(model, '', finish_reason=finish_reason)
            finish['usage'] = openai_usage
            yield f'data: {json.dumps(finish)}\n\n'

            yield 'data: [DONE]\n\n'

        return Response(
            stream_with_context(generate()),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
            },
        )

    message = {'role': 'assistant', 'content': text}
    if tool_calls:
        message['tool_calls'] = tool_calls

    # Non-streaming response
    return jsonify({
        'id': f'chatcmpl-{uuid.uuid4().hex[:12]}',
        'object': 'chat.completion',
        'created': int(time.time()),
        'model': model,
        'choices': [{
            'index': 0,
            'message': message,
            'finish_reason': finish_reason,
        }],
        'usage': openai_usage,
    })
