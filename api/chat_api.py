"""/chat Endpoint: provider-agnostische Chat-Completion."""

import logging
from flask import Blueprint, jsonify, request
from api.auth import require_token
from dispatcher import dispatch
from providers import PROVIDER_REGISTRY

logger = logging.getLogger(__name__)

chat_bp = Blueprint('chat', __name__)


@chat_bp.post('/chat')
@require_token
def chat():
    """Body:
      {
        "user_id": "...",
        "provider": "ollama",
        "model": "mistral",
        "messages": [{"role": "user", "content": "..."}],
        "max_tokens": 600
      }

    Response (sync):  { "result": {...}, "via": "ollama", "fallback_used": false }
    Response (queue): { "queued": true, "queue_id": "abc-123", "expires_at": "..." }
    """
    body = request.get_json() or {}

    required = ['user_id', 'provider', 'messages']
    missing = [k for k in required if k not in body]
    if missing:
        return jsonify({'error': f'Pflicht: {", ".join(missing)}'}), 400

    provider = body['provider']
    if provider not in PROVIDER_REGISTRY:
        return jsonify({'error': f'Unbekannter Provider: {provider}'}), 400

    try:
        result = dispatch(
            user_id=body['user_id'],
            provider_id=provider,
            model=body.get('model', ''),
            messages=body['messages'],
            max_tokens=int(body.get('max_tokens', 600)),
        )
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.exception(f'/chat dispatch failed: {e}')
        return jsonify({'error': str(e)}), 500
