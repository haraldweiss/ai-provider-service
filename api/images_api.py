"""OpenAI-compatible Image-Generation API (Bilder via OpenRouter).

Endpoints:
  GET  /v1/images/models        - only models with image output (sortable by price/quality)
  POST /v1/images/generations   - Text-to-image in OpenAI format

The Frontend (e.g. Open WebUI) calls in OpenAI-format and expects
`{data: [{b64_json, media_type}]}`. The b64_json is returned as a Data-URI
(`data:<media_type>;base64,...`) so the consumer correctly detects the MIME type
(raw base64 would be misinterpreted as PNG by Open WebUI).

Costs: OpenRouter returns USD costs directly in `usage.cost` — this is written
into the UsageEvent for the KI-Usage-Tracker, rather than a token-based pricing
calc that does not exist for image models.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Optional

import requests
from flask import Blueprint, jsonify, request, g

from api.auth import require_token
from config import Config
from storage.models import db, UsageEvent

logger = logging.getLogger(__name__)

images_bp = Blueprint('images', __name__)

# Cache for OpenRouter image model list.
# Caches full metadata including pricing so sorting is cheap.
_IMAGES_MODELS_CACHE: dict = {'ts': 0, 'rows': []}
_IMAGES_MODELS_TTL = 6 * 3600  # 6h — list rarely changes

# Models with minimum resolution that OpenRouter's API rejects for 512x512.
# When the user picks these models and sends a too-small size we auto-fallback
# to the model's minimum.
_MIN_PIXELS_MODELS = {
    'bytedance-seed/seedream-4.5': '2048x2048',
}


def _origin_app() -> str:
    return request.headers.get('X-Origin-App') or 'openwebui'


def _principal_user_id() -> str:
    principal = getattr(g, 'principal', None)
    if principal is not None:
        uid = getattr(principal, 'user_id', None)
        if uid:
            return str(uid)
        if getattr(principal, 'credential', '') == 'service':
            return Config.ADMIN_USER_ID
    raise ValueError('principal user_id is missing')


def _log_image_usage(
    user_id: str, model: str, input_tokens: Optional[int],
    output_tokens: Optional[int], cost_usd: Optional[float],
    status: str, error_message: Optional[str] = None,
) -> None:
    try:
        ev = UsageEvent(
            user_id=user_id, provider_id='openrouter', model=model,
            input_tokens=input_tokens, output_tokens=output_tokens,
            cost_usd=cost_usd, origin_app=_origin_app(), status=status,
            error_message=error_message,
        )
        db.session.add(ev)
        db.session.commit()
    except Exception as e:
        logger.warning('image usage logging failed: %s', e)
        db.session.rollback()


def _fetch_image_models() -> list[dict]:
    """Fetch OpenRouter image models with pricing, cached."""
    now = time.time()
    if _IMAGES_MODELS_CACHE['rows'] and now - _IMAGES_MODELS_CACHE['ts'] < _IMAGES_MODELS_TTL:
        return _IMAGES_MODELS_CACHE['rows']

    rows: list[dict] = []
    try:
        resp = requests.get(
            f'{Config.OPENROUTER_BASE_URL}/models',
            params={'output_modalities': 'image'},
            headers={
                'Authorization': f'Bearer {Config.OPENROUTER_API_KEY}',
                'Content-Type': 'application/json',
            },
            timeout=20,
        )
        resp.raise_for_status()
        for m in resp.json().get('data', []):
            mid = m.get('id', '')
            if not mid:
                continue
            pricing = m.get('pricing') or {}
            img_output = pricing.get('image_output', '')
            img_token = pricing.get('image_token', '')
            # Build a human-readable name that includes price info.
            # E.g. "FLUX.2 Pro (0.0073$/img)" or "gpt-image-1 (free)"
            price_note = ''
            if img_output and float(img_output) > 0:
                price_note = f" (~{float(img_output):.6f}$/img)"
            name = m.get('name') or mid
            display_name = name + price_note if price_note else name
            # Auto-router models have no image_output → sort to end.
            _cost = float(img_output) if img_output else (float('inf') if img_output == '' else 0.0)
            rows.append({
                'id': mid,
                'name': display_name,
                '_base_name': name,
                '_image_output_cost': _cost,
                '_image_token_cost': float(img_token) if img_token else 0.0,
                '_pricing': pricing,
            })
        rows.sort(key=lambda r: r['id'])
        _IMAGES_MODELS_CACHE.update({'ts': now, 'rows': rows})
    except Exception as e:
        logger.warning('Failed to fetch OpenRouter image models: %s', e)
    return rows


def _resolve_size(model: str, requested: Optional[str]) -> Optional[str]:
    if requested and 'x' in requested:
        return requested
    return _MIN_PIXELS_MODELS.get(model)


def _apply_sort(rows: list[dict], sort: str, order: str) -> list[dict]:
    """Sort rows by price or quality.

    sort: 'price' (default) → cheapest image output first
          'quality'          → highest image output cost first (proxy for quality)
    order: 'asc'  (default for price = cheapest first)
           'desc' (default for quality = best first)
    """
    desc = order == 'desc'
    if sort == 'quality':
        # higher image_output cost ≈ higher quality → desc puts best first
        return sorted(rows, key=lambda r: r.get('_image_output_cost', 0), reverse=desc)
    return sorted(rows, key=lambda r: r.get('_image_output_cost', 0), reverse=desc)


@images_bp.get('/v1/images/models')
@require_token
def list_image_models():
    """List only models capable of image generation.

    Query params:
      sort  — 'price' (default) or 'quality'
      order — 'asc' (default) or 'desc'
    """
    try:
        user_id = _principal_user_id()
    except ValueError:
        return jsonify({'error': {'message': 'authenticated principal has no user_id',
                                   'type': 'invalid_request'}}), 401

    rows = _fetch_image_models()
    if not rows:
        return jsonify({'error': {'message': 'No image models available (OpenRouter unreachable?)',
                                   'type': 'service_unavailable'}}), 503

    sort = request.args.get('sort', 'price')
    order = request.args.get('order', 'asc')
    sorted_rows = _apply_sort(list(rows), sort, order)

    public_rows = [{'id': r['id'], 'name': r['name']} for r in sorted_rows]
    return jsonify({'object': 'list', 'data': public_rows, 'count': len(public_rows)})


@images_bp.post('/v1/images/generations')
@require_token
def image_generations():
    """OpenAI-compatible text-to-image via OpenRouter.

    Body: {model, prompt, n, size}
    Response: {created, data: [{b64_json (Data-URI), media_type}], usage}
    """
    body = request.get_json(silent=True) or {}
    model = body.get('model', '')
    prompt = body.get('prompt', '')
    if not model:
        return jsonify({'error': {'message': 'model is required', 'type': 'invalid_request'}}), 400
    if not prompt:
        return jsonify({'error': {'message': 'prompt is required', 'type': 'invalid_request'}}), 400

    try:
        n = int(body.get('n', 1))
    except (TypeError, ValueError):
        n = 1
    n = max(1, min(n, 10))

    size = _resolve_size(model, body.get('size'))

    try:
        user_id = _principal_user_id()
    except ValueError:
        return jsonify({'error': {'message': 'authenticated principal has no user_id',
                                   'type': 'invalid_request'}}), 401

    if not Config.OPENROUTER_API_KEY:
        return jsonify({'error': {'message': 'OPENROUTER_API_KEY not configured',
                                   'type': 'server_error'}}), 503

    payload = {'model': model, 'prompt': prompt, 'n': n}
    if size:
        payload['size'] = size

    start = time.time()
    try:
        resp = requests.post(
            f'{Config.OPENROUTER_BASE_URL}/images',
            json=payload,
            headers={
                'Authorization': f'Bearer {Config.OPENROUTER_API_KEY}',
                'Content-Type': 'application/json',
            },
            timeout=300,
        )
        resp.raise_for_status()
        res = resp.json()
    except requests.RequestException as e:
        detail = ''
        if e.response is not None:
            try:
                detail = e.response.json().get('error', {}).get('message', '')
            except Exception:
                detail = e.response.text[:300]
        logger.warning('OpenRouter image generation failed: %s %s', e, detail)
        status_code = e.response.status_code if e.response is not None and e.response.status_code < 500 else 502
        _log_image_usage(user_id, model, None, None, None, 'error', detail or str(e))
        return jsonify({'error': {'message': detail or str(e), 'type': 'provider_error'}}), status_code
    except Exception as e:
        logger.exception('Unexpected image generation error: %s', e)
        _log_image_usage(user_id, model, None, None, None, 'error', str(e))
        return jsonify({'error': {'message': str(e), 'type': 'server_error'}}), 500

    images = []
    for item in res.get('data', []):
        b64 = item.get('b64_json', '')
        media_type = item.get('media_type', 'image/png')
        if not b64:
            continue
        if b64.startswith('data:'):
            images.append({'b64_json': b64, 'media_type': media_type})
        else:
            images.append({
                'b64_json': f'data:{media_type};base64,{b64}',
                'media_type': media_type,
            })

    if not images:
        _log_image_usage(user_id, model, None, None, None, 'error', 'empty data from OpenRouter')
        return jsonify({'error': {'message': 'No image data returned', 'type': 'provider_error'}}), 502

    usage = res.get('usage', {}) or {}
    cost_usd = usage.get('cost')
    if cost_usd is not None:
        try:
            cost_usd = float(cost_usd)
        except (TypeError, ValueError):
            cost_usd = None
    _log_image_usage(
        user_id, model,
        input_tokens=usage.get('prompt_tokens'),
        output_tokens=usage.get('completion_tokens'),
        cost_usd=cost_usd, status='success',
    )

    return jsonify({
        'id': f'imgcmpl-{uuid.uuid4().hex[:12]}',
        'object': 'image.generation',
        'created': int(start),
        'model': model,
        'data': images,
        'usage': {
            'prompt_tokens': usage.get('prompt_tokens'),
            'completion_tokens': usage.get('completion_tokens'),
            'total_tokens': usage.get('total_tokens'),
            'cost': cost_usd,
        },
    })
