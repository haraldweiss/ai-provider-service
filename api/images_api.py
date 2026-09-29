"""OpenAI-compatible Image-Generation API (Bilder via OpenRouter).

Endpoints:
  GET  /v1/images/models        - only models with image output (sortable by
                                  price/quality, `capabilities` per model)
  POST /v1/images/generations   - Text-to-image in OpenAI format

The Frontend (e.g. Open WebUI / Wolfini AI Studio) calls in OpenAI-format and
expects `{data: [{b64_json, media_type}]}`. The b64_json is returned as a
Data-URI (`data:<media_type>;base64,...`) so the consumer correctly detects the
MIME type (raw base64 would be misinterpreted as PNG by Open WebUI).

Accepted body fields (superset of the OpenAI schema):
  required: model, prompt
  optional: n, size, aspect_ratio, resolution, quality, output_format,
            background, output_compression, seed, input_references

`aspect_ratio` / `resolution` / `quality` / `output_format` / `seed` are the
OpenRouter Image API parameters that a NightCafe-style picker UI needs; they are
validated (whitelist + range) and forwarded 1:1. Per-model support is advertised
via `capabilities` on /v1/images/models so a UI never offers an invalid option.

Costs: OpenRouter returns USD costs directly in `usage.cost` — this is written
into the UsageEvent for the KI-Usage-Tracker, rather than a token-based pricing
calc that does not exist for image models.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any, Optional

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

# --- Optional generation parameters ------------------------------------------
#
# OpenRouter's /images endpoint understands far more than the bare
# OpenAI-compatible {model, prompt, n, size}: aspect_ratio, resolution, quality,
# output_format, background, output_compression, seed and input_references
# (image-to-image). Callers such as the Wolfini AI Studio UI need those to offer
# model / aspect-ratio / quality pickers, so they are validated here and
# forwarded 1:1 (whitelist + range checks) instead of being silently dropped.
_ASPECT_RATIO_RE = re.compile(
    r'^(auto|[0-9]{1,3}(?:\.[0-9]+)?:[0-9]{1,3}(?:\.[0-9]+)?)$'
)
_RESOLUTION_TIERS = {'512', '1K', '2K', '4K'}
_QUALITY_VALUES = {'auto', 'low', 'medium', 'high'}
_OUTPUT_FORMATS = {'png', 'jpeg', 'webp', 'svg'}
_BACKGROUND_VALUES = {'auto', 'transparent', 'opaque'}
_MAX_INPUT_REFERENCES = 10


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


# Capability metadata (which aspect ratios / resolutions / quality levels a
# model actually supports). The pricing list above does NOT carry
# `supported_parameters`, so they are fetched from OpenRouter's dedicated image
# models endpoint and merged in for /v1/images/models consumers.
_IMAGES_CAPS_CACHE: dict = {'ts': 0, 'rows': {}}
_IMAGES_CAPS_TTL = 6 * 3600


def _enum_values(entry: Any) -> Optional[list]:
    """Extract `{type: 'enum', values: [...]}` from supported_parameters."""
    if isinstance(entry, dict) and isinstance(entry.get('values'), list):
        return [str(v) for v in entry['values']]
    return None


def _range_entry(entry: Any) -> Optional[dict]:
    """Extract min/max from `{type: 'range', min: .., max: ..}`."""
    if isinstance(entry, dict) and entry.get('type') == 'range':
        out = {k: entry[k] for k in ('min', 'max') if isinstance(entry.get(k), int)}
        return out or None
    return None


def _normalize_capabilities(supported: dict, supports_streaming: Any = None) -> dict:
    """Reduce OpenRouter's `supported_parameters` to what a UI needs."""
    caps: dict = {}
    aspect_ratios = _enum_values(supported.get('aspect_ratio'))
    if aspect_ratios:
        caps['aspect_ratios'] = aspect_ratios
    resolutions = _enum_values(supported.get('resolution')) or _range_entry(
        supported.get('resolution')
    )
    if resolutions:
        caps['resolutions'] = resolutions
    qualities = _enum_values(supported.get('quality'))
    if qualities:
        caps['qualities'] = qualities
    output_formats = _enum_values(supported.get('output_format'))
    if output_formats:
        caps['output_formats'] = output_formats
    backgrounds = _enum_values(supported.get('background'))
    if backgrounds:
        caps['backgrounds'] = backgrounds
    n_range = _range_entry(supported.get('n'))
    if n_range and n_range.get('max'):
        caps['max_n'] = n_range['max']
    if 'seed' in supported:
        caps['supports_seed'] = True
    refs = _range_entry(supported.get('input_references'))
    if refs:
        caps['input_references'] = refs
    if supports_streaming is not None:
        caps['supports_streaming'] = bool(supports_streaming)
    return caps


def _fetch_image_capabilities() -> dict:
    """Return {model_id: capabilities} from OpenRouter's image models API.

    Never raises: an unreachable endpoint yields {} and callers simply omit the
    capability block (the UI then falls back to a generic option set).
    """
    now = time.time()
    if _IMAGES_CAPS_CACHE['rows'] and now - _IMAGES_CAPS_CACHE['ts'] < _IMAGES_CAPS_TTL:
        return _IMAGES_CAPS_CACHE['rows']

    rows: dict = {}
    try:
        resp = requests.get(
            f'{Config.OPENROUTER_BASE_URL}/images/models',
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
            rows[mid] = _normalize_capabilities(
                m.get('supported_parameters') or {},
                m.get('supports_streaming'),
            )
        _IMAGES_CAPS_CACHE.update({'ts': now, 'rows': rows})
    except Exception as e:
        logger.warning('Failed to fetch OpenRouter image model capabilities: %s', e)
    return rows


def _clean_generation_params(body: dict):
    """Validate the optional generation params.

    Returns `(params, error_message)`; `params` only carries the keys the caller
    actually supplied (and that passed validation).
    """
    params: dict = {}

    aspect_ratio = body.get('aspect_ratio')
    if aspect_ratio not in (None, ''):
        if not isinstance(aspect_ratio, str) or not _ASPECT_RATIO_RE.match(aspect_ratio.strip()):
            return {}, f'invalid aspect_ratio: {aspect_ratio!r}'
        aspect_ratio = aspect_ratio.strip()
        params['aspect_ratio'] = 'auto' if aspect_ratio == 'auto' else aspect_ratio

    resolution = body.get('resolution')
    if resolution not in (None, ''):
        tier = str(resolution).strip().upper()
        if tier not in _RESOLUTION_TIERS:
            return {}, (
                f'invalid resolution: {resolution!r} '
                f'(expected one of {sorted(_RESOLUTION_TIERS)})'
            )
        params['resolution'] = tier

    quality = body.get('quality')
    if quality not in (None, ''):
        value = str(quality).strip().lower()
        if value not in _QUALITY_VALUES:
            return {}, (
                f'invalid quality: {quality!r} '
                f'(expected one of {sorted(_QUALITY_VALUES)})'
            )
        params['quality'] = value

    output_format = body.get('output_format')
    if output_format not in (None, ''):
        value = str(output_format).strip().lower()
        if value not in _OUTPUT_FORMATS:
            return {}, (
                f'invalid output_format: {output_format!r} '
                f'(expected one of {sorted(_OUTPUT_FORMATS)})'
            )
        params['output_format'] = value

    background = body.get('background')
    if background not in (None, ''):
        value = str(background).strip().lower()
        if value not in _BACKGROUND_VALUES:
            return {}, (
                f'invalid background: {background!r} '
                f'(expected one of {sorted(_BACKGROUND_VALUES)})'
            )
        params['background'] = value

    seed = body.get('seed')
    if seed not in (None, ''):
        try:
            seed_int = int(seed)
        except (TypeError, ValueError):
            return {}, f'invalid seed: {seed!r} (expected an integer)'
        if seed_int < 0:
            return {}, f'invalid seed: {seed!r} (must be >= 0)'
        params['seed'] = seed_int

    compression = body.get('output_compression')
    if compression not in (None, ''):
        try:
            compression_int = int(compression)
        except (TypeError, ValueError):
            return {}, f'invalid output_compression: {compression!r} (expected 0-100)'
        if not 0 <= compression_int <= 100:
            return {}, f'invalid output_compression: {compression!r} (expected 0-100)'
        params['output_compression'] = compression_int

    size = body.get('size')
    if size not in (None, ''):
        if not isinstance(size, str) or 'x' not in size:
            return {}, f'invalid size: {size!r} (expected <width>x<height>)'
        params['size'] = size.strip()

    return params, None


def _clean_input_references(body: dict):
    """Validate `input_references` (image-to-image). Returns (refs, error)."""
    references = body.get('input_references')
    if references in (None, ''):
        return None, None
    if not isinstance(references, list) or not references:
        return None, 'invalid input_references: expected a non-empty array'
    if len(references) > _MAX_INPUT_REFERENCES:
        return None, f'invalid input_references: at most {_MAX_INPUT_REFERENCES} entries'
    cleaned = []
    for ref in references:
        if isinstance(ref, str) and ref.strip():
            cleaned.append({'type': 'image_url', 'image_url': {'url': ref.strip()}})
        elif isinstance(ref, dict) and ref:
            cleaned.append(ref)
        else:
            return None, 'invalid input_references: entries must be URLs or image objects'
    return cleaned, None


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

    public_rows = []
    if request.args.get('details', '1') not in ('0', 'false', 'no'):
        caps = _fetch_image_capabilities()
        for r in sorted_rows:
            row = {'id': r['id'], 'name': r['name']}
            if caps.get(r['id']):
                row['capabilities'] = caps[r['id']]
            public_rows.append(row)
    else:
        public_rows = [{'id': r['id'], 'name': r['name']} for r in sorted_rows]
    return jsonify({'object': 'list', 'data': public_rows, 'count': len(public_rows)})


@images_bp.post('/v1/images/generations')
@require_token
def image_generations():
    """OpenAI-compatible text-to-image via OpenRouter.

    Body: {model, prompt, n, size, aspect_ratio, resolution, quality,
           output_format, background, output_compression, seed,
           input_references}
    Response: {created, data: [{b64_json (Data-URI), media_type}], usage, params}
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

    params, param_error = _clean_generation_params(body)
    if param_error:
        return jsonify({'error': {'message': param_error, 'type': 'invalid_request'}}), 400

    refs, refs_error = _clean_input_references(body)
    if refs_error:
        return jsonify({'error': {'message': refs_error, 'type': 'invalid_request'}}), 400
    if refs:
        params['input_references'] = refs

    # Legacy minimum-pixel fallback (some models reject 512x512): only applied
    # when the caller sent neither a size nor an aspect ratio, because an
    # injected size would contradict a requested aspect ratio.
    if 'size' not in params and 'aspect_ratio' not in params:
        fallback_size = _MIN_PIXELS_MODELS.get(model)
        if fallback_size:
            params['size'] = fallback_size

    try:
        user_id = _principal_user_id()
    except ValueError:
        return jsonify({'error': {'message': 'authenticated principal has no user_id',
                                   'type': 'invalid_request'}}), 401

    if not Config.OPENROUTER_API_KEY:
        return jsonify({'error': {'message': 'OPENROUTER_API_KEY not configured',
                                   'type': 'server_error'}}), 503

    payload = {'model': model, 'prompt': prompt, 'n': n}
    payload.update(params)

    logger.info(
        'Image generation request: model=%s n=%s params=%s prompt_tokens=%s',
        model, n, params, len(prompt.split()),
    )
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

    logger.info(f'Image generation completed: model={model} images={len(res.get("data", []))} cost={res.get("usage",{}).get("cost","?")}')
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
        'params': params,
        'data': images,
        'usage': {
            'prompt_tokens': usage.get('prompt_tokens'),
            'completion_tokens': usage.get('completion_tokens'),
            'total_tokens': usage.get('total_tokens'),
            'cost': cost_usd,
        },
    })
