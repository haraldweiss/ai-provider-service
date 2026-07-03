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
from dispatcher import dispatch, _extract_response_text, _load_config
from providers import PROVIDER_REGISTRY, get_client
from flask import g

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


def _principal_user_id(default: str = 'pi-agent') -> str:
    principal = getattr(g, 'principal', None)
    if principal and isinstance(principal, object) and hasattr(principal, 'user_id'):
        return principal.user_id or default
    return default


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


def _openai_stream_chunk(model: str, content: str, index: int = 0,
                         finish_reason: str | None = None):
    """Build an OpenAI-style SSE chunk."""
    delta = {'role': 'assistant', 'content': content} if content else {}
    choice: dict = {'index': index, 'delta': delta}
    if finish_reason:
        choice['finish_reason'] = finish_reason
    return {
        'id': f'chatcmpl-{uuid.uuid4().hex[:12]}',
        'object': 'chat.completion.chunk',
        'created': int(time.time()),
        'model': model,
        'choices': [choice],
    }


# ─── Endpoints ────────────────────────────────────────────────────────────


@openai_bp.get('/v1/models')
@require_token
def list_models():
    """Return list of available models in OpenAI format."""
    return jsonify({'object': 'list', 'data': _available_model_rows(_principal_user_id())})


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
    messages = body.get('messages', [])
    stream = body.get('stream', False)
    max_tokens = int(body.get('max_tokens', 4096))

    if not model:
        return jsonify({'error': {'message': 'model is required', 'type': 'invalid_request'}}), 400
    if not messages:
        return jsonify({'error': {'message': 'messages is required', 'type': 'invalid_request'}}), 400

    provider_id, model_name, origin_app = _parse_model(model)

    if provider_id not in PROVIDER_REGISTRY:
        return jsonify({
            'error': {'message': f'Unknown provider: {provider_id}', 'type': 'invalid_request'},
        }), 400

    user_id = _principal_user_id()

    try:
        result = dispatch(
            user_id=user_id,
            provider_id=provider_id,
            model=model_name,
            messages=messages,
            max_tokens=max_tokens,
            origin_app=origin_app,
        )
    except Exception as e:
        logger.exception(f'v1/chat/completions dispatch failed: {e}')
        return jsonify({
            'error': {'message': str(e), 'type': 'server_error'},
        }), 500

    result_data = result.get('result', {})
    text = _extract_response_text(result_data)

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

            # Content chunk (full response — backend is sync)
            content_chunk = _openai_stream_chunk(model, text)
            yield f'data: {json.dumps(content_chunk)}\n\n'

            # Final chunk with usage
            finish = _openai_stream_chunk(model, '', finish_reason='stop')
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

    # Non-streaming response
    return jsonify({
        'id': f'chatcmpl-{uuid.uuid4().hex[:12]}',
        'object': 'chat.completion',
        'created': int(time.time()),
        'model': model,
        'choices': [{
            'index': 0,
            'message': {'role': 'assistant', 'content': text},
            'finish_reason': 'stop',
        }],
        'usage': openai_usage,
    })
