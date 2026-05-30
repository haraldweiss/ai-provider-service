"""/chat Endpoint: provider-agnostische Chat-Completion."""

import logging
from flask import Blueprint, jsonify, request
from api.auth import require_token
from dispatcher import dispatch
from providers import PROVIDER_REGISTRY
from providers.model_manager import get_model_manager

logger = logging.getLogger(__name__)

chat_bp = Blueprint('chat', __name__)
model_manager = get_model_manager()


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
        # Auto-load model if using Ollama
        model_name = body.get('model', '')
        if provider == 'ollama' and model_name:
            logger.debug(f"Auto-loading model: {model_name}")
            if not model_manager.load_model(model_name):
                return jsonify({
                    'error': f'Failed to load model {model_name}; insufficient VRAM or model not found'
                }), 400
        
        result = dispatch(
            user_id=body['user_id'],
            provider_id=provider,
            model=model_name,
            messages=body['messages'],
            max_tokens=int(body.get('max_tokens', 600)),
        )
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.exception(f'/chat dispatch failed: {e}')
        return jsonify({'error': str(e)}), 500
