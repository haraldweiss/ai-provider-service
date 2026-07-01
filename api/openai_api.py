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
from dispatcher import dispatch, _extract_response_text
from providers import PROVIDER_REGISTRY
from flask import g

logger = logging.getLogger(__name__)

openai_bp = Blueprint('openai', __name__)

# ─── known model lists per provider ────────────────────────────────────────

ZAI_MODELS = [
    'zai/glm-4-flash',
    'zai/glm-4',
]

CLAUDE_MODELS = [
    'claude/claude-sonnet-4-6-20250514',
    'claude/claude-sonnet-4-20250514',
    'claude/claude-haiku-4-5-20251001',
    'claude/claude-opus-4-6-20250514',
]

OLLAMA_MODELS = [
    'ollama/qwen3.6:latest',
    'ollama/dev-coder:latest',
    'ollama/soc-analyst:latest',
    'ollama/soc-detect:latest',
    'ollama/qwen3-coder:latest',
    'ollama/qwen3-coder-cc:latest',
    'ollama/mistral-nemo-cc:latest',
    'ollama/glm-4.7-flash:latest',
]

WOLFINICHAT_MODELS = [
    'wolfinichat/qwen3.6:latest',
    'wolfinichat/dev-coder:latest',
]

ALL_MODELS = ZAI_MODELS + CLAUDE_MODELS + OLLAMA_MODELS + WOLFINICHAT_MODELS

MODEL_META = {
    'zai/glm-4-flash': {'provider': 'zai', 'context': 128000, 'reasoning': False},
    'zai/glm-4': {'provider': 'zai', 'context': 128000, 'reasoning': True},
    'claude/claude-sonnet-4-6-20250514': {'provider': 'claude', 'context': 200000, 'reasoning': True},
    'claude/claude-sonnet-4-20250514': {'provider': 'claude', 'context': 200000, 'reasoning': True},
    'claude/claude-haiku-4-5-20251001': {'provider': 'claude', 'context': 200000, 'reasoning': False},
    'claude/claude-opus-4-6-20250514': {'provider': 'claude', 'context': 200000, 'reasoning': True},
    'ollama/qwen3.6:latest': {'provider': 'ollama', 'context': 32000, 'reasoning': False},
    'ollama/dev-coder:latest': {'provider': 'ollama', 'context': 32000, 'reasoning': False},
    'ollama/soc-analyst:latest': {'provider': 'ollama', 'context': 32000, 'reasoning': False},
    'ollama/soc-detect:latest': {'provider': 'ollama', 'context': 32000, 'reasoning': False},
    'ollama/qwen3-coder:latest': {'provider': 'ollama', 'context': 32000, 'reasoning': False},
    'ollama/qwen3-coder-cc:latest': {'provider': 'ollama', 'context': 32000, 'reasoning': False},
    'ollama/mistral-nemo-cc:latest': {'provider': 'ollama', 'context': 32000, 'reasoning': False},
    'ollama/glm-4.7-flash:latest': {'provider': 'ollama', 'context': 32000, 'reasoning': False},
    'wolfinichat/qwen3.6:latest': {'provider': 'ollama', 'context': 32000, 'reasoning': False},
    'wolfinichat/dev-coder:latest': {'provider': 'ollama', 'context': 32000, 'reasoning': False},
}


def _parse_model(model: str) -> tuple[str, str, str | None]:
    """Split 'zai/glm-4-flash' → ('zai', 'glm-4-flash', origin_app)."""
    if '/' in model:
        prefix, name = model.split('/', 1)
        origin = 'chat.wolfinisoftware.de' if prefix == 'wolfinichat' else None
        return prefix, name, origin
    return 'claude', model, None


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
    data = []
    for mid in ALL_MODELS:
        meta = MODEL_META.get(mid, {})
        data.append({
            'id': mid,
            'object': 'model',
            'created': int(time.time()),
            'owned_by': meta.get('provider', 'ai-provider-service'),
        })
    return jsonify({'object': 'list', 'data': data})


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

    principal = getattr(g, 'principal', None)
    if principal and isinstance(principal, object) and hasattr(principal, 'user_id'):
        user_id = principal.user_id
    else:
        user_id = 'pi-agent'

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
