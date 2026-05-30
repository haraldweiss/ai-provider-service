"""Ollama-kompatible Facade fuer Open WebUI (chat.wolfinisoftware.de).

Open WebUI spricht nativ das Ollama-API. Damit chat-Traffic im Usage-Tracking
unter `wolfinichat` auftaucht (statt direkt am Tunnel vorbei zu fliessen),
ersetzt dieser Blueprint die Endpoints, die Open WebUI tatsaechlich aufruft:

  GET  /api/tags     -> OllamaClient.get_models() im Ollama-Format
  GET  /api/version  -> Stub mit Version-Marker
  POST /api/show     -> Pass-through an ein echtes Ollama-Endpoint (Modell-Meta)
  POST /api/chat     -> dispatch() mit user_id=wolfinichat, ggf. Pseudo-Stream
  POST /api/ps       -> Pass-through (loaded models)

Auth: Bearer-Token = SERVICE_TOKEN (vom Apache-Frontend per RequestHeader
gesetzt). User-ID wird hier FEST auf 'wolfinichat' gesetzt - das Ollama-
Protokoll kennt kein user_id-Feld, und die Facade ist explizit nur fuer
Open WebUI gedacht.

Streaming: Open WebUI sendet "stream": true. Variante A (Pseudo-Stream):
wir rufen Ollama mit stream=False, geben die Antwort als zwei NDJSON-Chunks
zurueck (1x message-Delta, 1x done=true mit Token-Counts). Reicht fuer
Open WebUI, blockiert UsageEvent-Logging nicht, kein Eingriff in dispatcher.
"""

from __future__ import annotations

import json
import logging
import time

import requests
from flask import Blueprint, Response, g, jsonify, request, stream_with_context

from api.auth import Principal, _resolve_principal
from config import Config
from dispatcher import dispatch
from providers import get_client

logger = logging.getLogger(__name__)

ollama_facade_bp = Blueprint('ollama_facade', __name__)

WOLFINICHAT_USER_ID = 'wolfinichat'
WOLFINICHAT_ORIGIN_APP = 'chat.wolfinisoftware.de'


def _require_facade_auth():
    """Bearer-Token-Check; auf Erfolg g.principal = wolfinichat-User."""
    p = _resolve_principal()
    if p is None:
        return jsonify({'error': 'Missing or invalid Bearer token'}), 401
    g.principal = Principal(user_id=WOLFINICHAT_USER_ID, role='user')
    g.agent = request.headers.get('X-Agent') or 'open-webui'
    return None


def _first_ollama_endpoint() -> str:
    """Erste konfigurierte Ollama-URL aus dem Pool. Pass-through fuer
    Endpoints, die wir nicht selbst implementieren."""
    client = get_client('ollama', {})
    return client.endpoints[0]


@ollama_facade_bp.get('/api/tags')
def api_tags():
    err = _require_facade_auth()
    if err is not None:
        return err
    try:
        client = get_client('ollama', {})
        names = client.get_models()
        models = [
            {
                'name': name,
                'model': name,
                'modified_at': '1970-01-01T00:00:00Z',
                'size': 0,
                'digest': '',
                'details': {},
            }
            for name in names
        ]
        return jsonify({'models': models})
    except Exception as e:
        logger.exception(f'/api/tags failed: {e}')
        return jsonify({'error': str(e)}), 500


@ollama_facade_bp.get('/api/version')
def api_version():
    err = _require_facade_auth()
    if err is not None:
        return err
    return jsonify({'version': 'wolfinichat-facade-1'})


@ollama_facade_bp.post('/api/show')
def api_show():
    err = _require_facade_auth()
    if err is not None:
        return err
    try:
        body = request.get_json(silent=True) or {}
        upstream = _first_ollama_endpoint()
        r = requests.post(f'{upstream}/api/show', json=body, timeout=10)
        return Response(r.content, status=r.status_code,
                        content_type=r.headers.get('Content-Type', 'application/json'))
    except Exception as e:
        logger.warning(f'/api/show passthrough failed: {e}')
        return jsonify({'error': str(e)}), 502


@ollama_facade_bp.get('/api/ps')
def api_ps():
    err = _require_facade_auth()
    if err is not None:
        return err
    try:
        upstream = _first_ollama_endpoint()
        r = requests.get(f'{upstream}/api/ps', timeout=5)
        return Response(r.content, status=r.status_code,
                        content_type=r.headers.get('Content-Type', 'application/json'))
    except Exception as e:
        logger.warning(f'/api/ps passthrough failed: {e}')
        return jsonify({'error': str(e)}), 502


def _ollama_chat_response(model: str, content: str, input_tokens: int,
                          output_tokens: int) -> dict:
    """Baut eine Ollama-/api/chat-Antwort (non-streaming)."""
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    return {
        'model': model,
        'created_at': now,
        'message': {'role': 'assistant', 'content': content},
        'done_reason': 'stop',
        'done': True,
        'total_duration': 0,
        'load_duration': 0,
        'prompt_eval_count': input_tokens,
        'prompt_eval_duration': 0,
        'eval_count': output_tokens,
        'eval_duration': 0,
    }


def _extract_text_and_usage(dispatch_result: dict) -> tuple[str, int, int]:
    """dispatch() liefert {'result': {...}, 'via': ..., 'fallback_used': ...}.
    Das innere result ist provider-spezifisch; Ollama liefert
    {'content': [{'text': ...}], 'usage': {'input_tokens': N, 'output_tokens': M}}.
    """
    inner = (dispatch_result or {}).get('result') or {}
    content_blocks = inner.get('content') or []
    text = ''
    if content_blocks and isinstance(content_blocks, list):
        text = content_blocks[0].get('text', '') or ''
    usage = inner.get('usage') or {}
    return text, int(usage.get('input_tokens') or 0), int(usage.get('output_tokens') or 0)


@ollama_facade_bp.post('/api/chat')
def api_chat():
    err = _require_facade_auth()
    if err is not None:
        return err

    body = request.get_json(silent=True) or {}
    model = body.get('model') or ''
    messages = body.get('messages') or []
    stream = bool(body.get('stream', True))  # Open WebUI default ist true

    if not model:
        return jsonify({'error': 'model is required'}), 400
    if not messages:
        return jsonify({'error': 'messages is required'}), 400

    # Ollama-Options koennen num_predict enthalten -> als max_tokens mappen.
    options = body.get('options') or {}
    max_tokens = int(options.get('num_predict') or 600)

    try:
        result = dispatch(
            user_id=WOLFINICHAT_USER_ID,
            provider_id='ollama',
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            origin_app=WOLFINICHAT_ORIGIN_APP,
        )
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.exception(f'/api/chat dispatch failed: {e}')
        return jsonify({'error': str(e)}), 500

    if result.get('queued'):
        # Open WebUI hat kein Queue-Konzept; melde 503, damit User es spaeter
        # erneut versucht. Im Praxisbetrieb mit healthy Tunnel passiert das nicht.
        return jsonify({'error': 'ollama queued (provider down)',
                        'queue_id': result.get('queue_id')}), 503

    text, in_tok, out_tok = _extract_text_and_usage(result)
    payload = _ollama_chat_response(model, text, in_tok, out_tok)

    if not stream:
        return jsonify(payload)

    # Pseudo-Stream: zwei NDJSON-Chunks. Erster traegt den Inhalt (done=false),
    # zweiter ist das done-Signal mit Token-Counts. Open WebUI rendert beide.
    def gen():
        chunk1 = {
            'model': model,
            'created_at': payload['created_at'],
            'message': {'role': 'assistant', 'content': text},
            'done': False,
        }
        yield json.dumps(chunk1) + '\n'
        chunk2 = dict(payload)
        chunk2['message'] = {'role': 'assistant', 'content': ''}
        yield json.dumps(chunk2) + '\n'

    return Response(stream_with_context(gen()),
                    content_type='application/x-ndjson')
