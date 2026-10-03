"""OpenAI-style Video/Animation API (Animations via OpenRouter).

Endpoints:
  GET  /v1/videos/models                    - only models with video output,
                                              with capabilities + normalized pricing
  POST /v1/videos/generations               - submit an (async) video job
  GET  /v1/videos/generations/<job_id>      - poll job status (+ usage cost)
  GET  /v1/videos/generations/<job_id>/content  - download the finished clip

OpenRouter's video generation is asynchronous (see
https://openrouter.ai/docs/guides/overview/multimodal/video-generation):
  POST /api/v1/videos           -> 202 {id, polling_url, status}
  GET  /api/v1/videos/<id>      -> {status, unsigned_urls, usage:{cost}}
  GET  /api/v1/videos/<id>/content?index=N -> binary (needs the API key)

The Wolfini AI Studio talks only to this bridge so the OpenRouter token stays
server-side (AGENTS §3.22). Content bytes are streamed through so the browser can
play them directly.

Accepted submit fields (superset of OpenRouter's request):
  required: model, (prompt unless frame_images are given)
  optional: duration, resolution, aspect_ratio, size, frame_images,
            input_references, generate_audio, seed

`frame_images` are image-to-video first/last frames; `input_references` are
reference-to-video style images. They are validated and normalised to the shape
OpenRouter expects.

Costs: video billing is per-second/per-token depending on the model. This module
normalises each model's OpenRouter `pricing_skus` into simple per-second tiers so
the UI can show an estimate *before* generation; the real cost is written into the
UsageEvent (KI-Usage-Tracker) once the job completes.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional

import requests
from flask import Blueprint, jsonify, request, g, Response, stream_with_context

from api.auth import require_token
from config import Config
from storage.models import db, UsageEvent

logger = logging.getLogger(__name__)

videos_bp = Blueprint('videos', __name__)

# OpenRouter video model list is fetched once and cached; the catalog changes
# rarely. Pricing + capabilities live together in this listing.
_VIDEO_MODELS_CACHE: dict = {'ts': 0, 'rows': [], 'by_id': {}}
_VIDEO_MODELS_TTL = 6 * 3600

# Job ids whose completion has already been logged, so a chatty poll loop does
# not write the same UsageEvent several times. Bounded to avoid unbounded growth.
_LOGGED_JOBS: set = set()
_LOGGED_JOBS_MAX = 5000

_MAX_INPUT_REFERENCES = 10

_RESOLUTION_TOKENS = ('480p', '720p', '768p', '1080p', '1024p', '1k', '2k', '4k')
_RES_RE = re.compile(
    r'(?<![0-9a-z])(' + '|'.join(_RESOLUTION_TOKENS) + r')(?![0-9a-z])', re.I
)


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


def _log_video_usage(
    user_id: str, model: str, cost_usd: Optional[float],
    status: str, error_message: Optional[str] = None,
) -> None:
    try:
        ev = UsageEvent(
            user_id=user_id, provider_id='openrouter', model=model,
            input_tokens=None, output_tokens=None,
            cost_usd=cost_usd, origin_app=_origin_app(), status=status,
            error_message=error_message,
        )
        db.session.add(ev)
        db.session.commit()
    except Exception as e:  # pragma: no cover - defensive, mirrors images_api
        logger.warning('video usage logging failed: %s', e)
        db.session.rollback()


# --- Pricing normalisation ---------------------------------------------------
#
# OpenRouter exposes a per-model `pricing_skus` map whose keys differ between
# model families ("duration_seconds_720p", "cents_per_second_output_1080p",
# "video_tokens", "reference_duration_seconds_480p", ...). A UI needs a single
# "USD per second" number, so every duration/second SKU is reduced to a tier with
# an optional resolution and audio flag. Token- and megapixel-based billing
# cannot be estimated reliably before generation and are flagged instead.

def _extract_resolution(key: str) -> Optional[str]:
    match = _RES_RE.search(key)
    if not match:
        return None
    token = match.group(1)
    # Normalise casing: '1k' -> '1K', '720P' -> '720p'.
    if token.endswith('p') and token[:-1].isdigit():
        return token.lower()
    return token.upper()


def _normalize_pricing(skus: dict) -> dict:
    tiers: list = []
    per_image_usd: Optional[float] = None
    minimum_usd: Optional[float] = None
    token_based = False
    megapixel_based = False

    for raw_key, raw_value in (skus or {}).items():
        key = str(raw_key).lower()
        try:
            amount = float(raw_value)
        except (TypeError, ValueError):
            continue

        if 'token' in key:
            token_based = True
            continue
        if 'megapixel' in key:
            megapixel_based = True
            continue
        if 'minimum' in key:
            minimum_usd = amount / 100.0 if 'cents' in key else amount
            continue
        if 'image_input' in key or 'image_inputs' in key:
            value = amount / 100.0 if 'cents' in key else amount
            per_image_usd = (per_image_usd or 0.0) + value
            continue

        is_second = 'second' in key or 'duration_seconds' in key
        if not is_second:
            continue
        # Video-continuation billing (feeding an existing clip in) is not the
        # base text/image-to-video path the UI estimates.
        if 'continuation' in key:
            continue

        audio: Optional[bool] = None
        if 'with_audio' in key:
            audio = True
        elif 'without_audio' in key:
            audio = False

        kind = 'any'
        if 'image_to_video' in key or ('reference' in key and 'duration' in key):
            kind = 'image'
        elif 'text_to_video' in key:
            kind = 'text'

        cents = 'cents' in key or key.startswith('per-video-second')
        usd = amount / 100.0 if cents else amount
        tiers.append({
            'resolution': _extract_resolution(key),
            'audio': audio,
            'kind': kind,
            'usd_per_second': usd,
        })

    # De-duplicate identical tiers (e.g. text_to_video and image_to_video sharing
    # the same resolution+rate) keeping the first occurrence.
    deduped: list = []
    seen: set = set()
    for tier in tiers:
        marker = (tier['resolution'], tier['audio'], tier['kind'], tier['usd_per_second'])
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(tier)

    return {
        'currency': 'USD',
        'tiers': deduped,
        'per_image_usd': per_image_usd,
        'minimum_usd': minimum_usd,
        'token_based': token_based,
        'megapixel_based': megapixel_based,
    }


def _price_hint(pricing: dict) -> str:
    rates = [t['usd_per_second'] for t in pricing.get('tiers', [])
             if isinstance(t.get('usd_per_second'), (int, float))]
    if rates:
        return f'ab {min(rates):.4f} $/s'
    if pricing.get('token_based'):
        return 'token-basiert'
    if pricing.get('megapixel_based'):
        return 'nach Pixeln'
    return ''


def _tier_score(tier: dict, resolution: Optional[str], audio: bool,
                uses_image: bool) -> int:
    want_kind = 'image' if uses_image else 'text'
    kind = tier.get('kind', 'any')
    if kind == want_kind:
        score = 100
    elif kind == 'any':
        score = 40
    else:
        return -1
    if tier.get('audio') == audio:
        score += 30
    elif tier.get('audio') is None:
        score += 5
    else:
        return -1
    if resolution and tier.get('resolution') and str(tier['resolution']).lower() == str(resolution).lower():
        score += 20
    elif tier.get('resolution') is None:
        score += 3
    elif resolution:
        return -1
    return score


def estimate_video_cost(
    capabilities: dict, duration: int, resolution: Optional[str],
    generate_audio: bool, uses_image: bool, image_count: int = 0,
) -> Optional[float]:
    """Best-effort pre-generation cost estimate in USD (None if not estimable)."""
    pricing = (capabilities or {}).get('pricing') or {}
    if pricing.get('token_based') or pricing.get('megapixel_based'):
        return None
    best = None
    best_score = -1
    for tier in pricing.get('tiers', []):
        score = _tier_score(tier, resolution, generate_audio, uses_image)
        if score > best_score:
            best_score = score
            best = tier
    if best is None or best_score < 0:
        return None
    cost = float(best.get('usd_per_second', 0.0)) * max(1, int(duration))
    if pricing.get('per_image_usd') and image_count:
        cost += float(pricing['per_image_usd']) * int(image_count)
    minimum = pricing.get('minimum_usd')
    if minimum is not None and cost < float(minimum):
        cost = float(minimum)
    return round(cost, 4)


# --- Model catalogue ---------------------------------------------------------

def _fetch_video_models() -> list[dict]:
    now = time.time()
    if (_VIDEO_MODELS_CACHE['rows']
            and now - _VIDEO_MODELS_CACHE['ts'] < _VIDEO_MODELS_TTL):
        return _VIDEO_MODELS_CACHE['rows']

    rows: list[dict] = []
    by_id: dict = {}
    try:
        resp = requests.get(
            f'{Config.OPENROUTER_BASE_URL}/videos/models',
            headers={
                'Authorization': f'Bearer {Config.OPENROUTER_API_KEY}',
                'Content-Type': 'application/json',
            },
            timeout=20,
        )
        resp.raise_for_status()
        payload = resp.json()
        listing = payload.get('data', payload) if isinstance(payload, dict) else payload
        for m in listing or []:
            mid = m.get('id', '')
            if not mid:
                continue
            pricing = _normalize_pricing(m.get('pricing_skus') or {})
            capabilities = {
                'aspect_ratios': m.get('supported_aspect_ratios') or [],
                'resolutions': m.get('supported_resolutions') or [],
                'durations': sorted(
                    int(d) for d in (m.get('supported_durations') or [])
                    if isinstance(d, (int, float)) or str(d).isdigit()
                ),
                'sizes': m.get('supported_sizes') or None,
                'frame_images': m.get('supported_frame_images') or [],
                'generate_audio': bool(m.get('generate_audio')),
                'supports_seed': bool(m.get('seed')),
                'passthrough': m.get('allowed_passthrough_parameters') or [],
                'pricing': pricing,
                'price_hint': _price_hint(pricing),
            }
            row = {'id': mid, 'name': m.get('name') or mid,
                   'capabilities': capabilities}
            rows.append(row)
            by_id[mid] = row
        rows.sort(key=lambda r: r['id'])
        _VIDEO_MODELS_CACHE.update({'ts': now, 'rows': rows, 'by_id': by_id})
    except Exception as e:
        logger.warning('Failed to fetch OpenRouter video models: %s', e)
    return rows


# --- Request validation ------------------------------------------------------

_ASPECT_RATIO_RE = re.compile(
    r'^(auto|[0-9]{1,3}(?:\.[0-9]+)?:[0-9]{1,3}(?:\.[0-9]+)?)$'
)


def _clean_frame_images(body: dict):
    """Normalise first/last frame images to OpenRouter's frame_images shape."""
    frames = body.get('frame_images')
    if frames in (None, ''):
        return None, None
    if not isinstance(frames, list) or not frames:
        return None, 'invalid frame_images: expected a non-empty array'
    cleaned = []
    for frame in frames:
        if isinstance(frame, str):
            cleaned.append({
                'type': 'image_url',
                'image_url': {'url': frame.strip()},
                'frame_type': 'first_frame',
            })
            continue
        if not isinstance(frame, dict):
            return None, 'invalid frame_images: entries must be URLs or objects'
        url = frame.get('url')
        frame_type = frame.get('frame_type') or 'first_frame'
        image_url = frame.get('image_url')
        if not url and isinstance(image_url, dict):
            url = image_url.get('url')
        if not url:
            return None, 'invalid frame_images: each entry needs a URL'
        if frame_type not in ('first_frame', 'last_frame'):
            return None, (
                f"invalid frame_images frame_type: {frame_type!r} "
                "(expected 'first_frame' or 'last_frame')"
            )
        cleaned.append({
            'type': 'image_url',
            'image_url': {'url': str(url).strip()},
            'frame_type': frame_type,
        })
    return cleaned, None


def _clean_input_references(body: dict):
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


def _clean_video_params(body: dict):
    """Validate the optional video params. Returns (params, error)."""
    params: dict = {}

    duration = body.get('duration')
    if duration not in (None, ''):
        try:
            duration_int = int(duration)
        except (TypeError, ValueError):
            return {}, f'invalid duration: {duration!r} (expected seconds)'
        if duration_int < 1:
            return {}, f'invalid duration: {duration!r} (must be >= 1)'
        params['duration'] = duration_int

    resolution = body.get('resolution')
    if resolution not in (None, ''):
        params['resolution'] = str(resolution).strip()

    aspect_ratio = body.get('aspect_ratio')
    if aspect_ratio not in (None, ''):
        if not isinstance(aspect_ratio, str) or not _ASPECT_RATIO_RE.match(aspect_ratio.strip()):
            return {}, f'invalid aspect_ratio: {aspect_ratio!r}'
        params['aspect_ratio'] = aspect_ratio.strip()

    size = body.get('size')
    if size not in (None, ''):
        if not isinstance(size, str) or 'x' not in size.lower():
            return {}, f'invalid size: {size!r} (expected <width>x<height>)'
        params['size'] = size.strip()

    audio = body.get('generate_audio')
    if audio not in (None, ''):
        if not isinstance(audio, bool):
            return {}, f'invalid generate_audio: {audio!r} (expected boolean)'
        params['generate_audio'] = audio

    seed = body.get('seed')
    if seed not in (None, ''):
        try:
            seed_int = int(seed)
        except (TypeError, ValueError):
            return {}, f'invalid seed: {seed!r} (expected an integer)'
        if seed_int < 0:
            return {}, f'invalid seed: {seed!r} (must be >= 0)'
        params['seed'] = seed_int

    return params, None


# --- Endpoints ---------------------------------------------------------------

@videos_bp.get('/v1/videos/models')
@require_token
def list_video_models():
    """List only models capable of video generation, with capabilities + pricing."""
    try:
        _principal_user_id()
    except ValueError:
        return jsonify({'error': {'message': 'authenticated principal has no user_id',
                                   'type': 'invalid_request'}}), 401

    rows = _fetch_video_models()
    if not rows:
        return jsonify({'error': {'message': 'No video models available (OpenRouter unreachable?)',
                                   'type': 'service_unavailable'}}), 503
    return jsonify({'object': 'list', 'data': rows, 'count': len(rows)})


@videos_bp.post('/v1/videos/generations')
@require_token
def video_generations():
    """Submit an async video generation job via OpenRouter."""
    body = request.get_json(silent=True) or {}
    model = body.get('model', '')
    prompt = body.get('prompt', '')
    if not model:
        return jsonify({'error': {'message': 'model is required', 'type': 'invalid_request'}}), 400

    frames, frames_error = _clean_frame_images(body)
    if frames_error:
        return jsonify({'error': {'message': frames_error, 'type': 'invalid_request'}}), 400
    refs, refs_error = _clean_input_references(body)
    if refs_error:
        return jsonify({'error': {'message': refs_error, 'type': 'invalid_request'}}), 400
    if not prompt and not frames:
        return jsonify({'error': {'message': 'prompt is required (or provide frame_images)',
                                   'type': 'invalid_request'}}), 400

    params, param_error = _clean_video_params(body)
    if param_error:
        return jsonify({'error': {'message': param_error, 'type': 'invalid_request'}}), 400

    try:
        user_id = _principal_user_id()
    except ValueError:
        return jsonify({'error': {'message': 'authenticated principal has no user_id',
                                   'type': 'invalid_request'}}), 401

    if not Config.OPENROUTER_API_KEY:
        return jsonify({'error': {'message': 'OPENROUTER_API_KEY not configured',
                                   'type': 'server_error'}}), 503

    payload = {'model': model}
    if prompt:
        payload['prompt'] = prompt
    payload.update(params)
    if frames:
        payload['frame_images'] = frames
    if refs:
        payload['input_references'] = refs

    catalog = _fetch_video_models()  # warms cache for the estimate
    capabilities = next(
        (row['capabilities'] for row in catalog if row['id'] == model), {}
    )
    uses_image = bool(frames or refs)
    estimate = estimate_video_cost(
        capabilities,
        duration=int(params.get('duration', 0)) or 1,
        resolution=params.get('resolution'),
        generate_audio=bool(params.get('generate_audio')),
        uses_image=uses_image,
        image_count=(len(frames or []) + len(refs or [])),
    )

    logger.info(
        'Video generation submit: model=%s duration=%s resolution=%s '
        'audio=%s frames=%s refs=%s estimate=%s',
        model, params.get('duration'), params.get('resolution'),
        params.get('generate_audio'), len(frames or []), len(refs or []), estimate,
    )
    try:
        resp = requests.post(
            f'{Config.OPENROUTER_BASE_URL}/videos',
            json=payload,
            headers={
                'Authorization': f'Bearer {Config.OPENROUTER_API_KEY}',
                'Content-Type': 'application/json',
            },
            timeout=60,
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
        logger.warning('OpenRouter video submit failed: %s %s', e, detail)
        status_code = e.response.status_code if e.response is not None and e.response.status_code < 500 else 502
        _log_video_usage(user_id, model, None, 'error', detail or str(e))
        return jsonify({'error': {'message': detail or str(e), 'type': 'provider_error'}}), status_code
    except Exception as e:
        logger.exception('Unexpected video submit error: %s', e)
        _log_video_usage(user_id, model, None, 'error', str(e))
        return jsonify({'error': {'message': str(e), 'type': 'server_error'}}), 500

    job_id = res.get('id')
    if not job_id:
        _log_video_usage(user_id, model, None, 'error', 'OpenRouter returned no job id')
        return jsonify({'error': {'message': 'OpenRouter returned no job id',
                                   'type': 'provider_error'}}), 502

    return jsonify({
        'id': job_id,
        'object': 'video.generation',
        'status': res.get('status', 'pending'),
        'model': model,
        'params': params,
        'estimate_usd': estimate,
    }), 202


@videos_bp.get('/v1/videos/generations/<job_id>')
@require_token
def video_generation_status(job_id: str):
    """Poll a video job; logs the real cost once when it completes."""
    try:
        user_id = _principal_user_id()
    except ValueError:
        return jsonify({'error': {'message': 'authenticated principal has no user_id',
                                   'type': 'invalid_request'}}), 401

    try:
        resp = requests.get(
            f'{Config.OPENROUTER_BASE_URL}/videos/{job_id}',
            headers={'Authorization': f'Bearer {Config.OPENROUTER_API_KEY}'},
            timeout=60,
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
        status_code = e.response.status_code if e.response is not None and e.response.status_code < 500 else 502
        return jsonify({'error': {'message': detail or str(e), 'type': 'provider_error'}}), status_code

    status = res.get('status')
    usage = res.get('usage') or {}
    cost = usage.get('cost')
    if cost is not None:
        try:
            cost = float(cost)
        except (TypeError, ValueError):
            cost = None

    urls = res.get('unsigned_urls') or []
    if status == 'completed' and job_id not in _LOGGED_JOBS:
        _log_video_usage(user_id, res.get('model') or 'video', cost, 'success')
        if len(_LOGGED_JOBS) >= _LOGGED_JOBS_MAX:
            _LOGGED_JOBS.clear()
        _LOGGED_JOBS.add(job_id)
    elif status in ('failed', 'cancelled', 'expired') and job_id not in _LOGGED_JOBS:
        _log_video_usage(user_id, res.get('model') or 'video', cost, 'error',
                         res.get('error') or status)
        _LOGGED_JOBS.add(job_id)

    return jsonify({
        'id': job_id,
        'generation_id': res.get('generation_id'),
        'status': status,
        'error': res.get('error'),
        'content_count': len(urls),
        'usage': {'cost': cost, 'is_byok': usage.get('is_byok')},
    })


@videos_bp.get('/v1/videos/generations/<job_id>/content')
@require_token
def video_generation_content(job_id: str):
    """Stream the finished clip from OpenRouter (keeps the API key server-side)."""
    index = request.args.get('index', '0')
    headers = {'Authorization': f'Bearer {Config.OPENROUTER_API_KEY}'}
    # Forward Range so browsers can seek/stream large clips.
    range_header = request.headers.get('Range')
    if range_header:
        headers['Range'] = range_header
    try:
        upstream = requests.get(
            f'{Config.OPENROUTER_BASE_URL}/videos/{job_id}/content',
            params={'index': index},
            headers=headers,
            timeout=300,
            stream=True,
        )
    except requests.RequestException as e:
        return jsonify({'error': {'message': str(e), 'type': 'provider_error'}}), 502

    if upstream.status_code >= 400:
        detail = ''
        try:
            detail = upstream.json().get('error', {}).get('message', '')
        except Exception:
            detail = upstream.text[:300]
        return jsonify({'error': {'message': detail or f'upstream HTTP {upstream.status_code}',
                                   'type': 'provider_error'}}), upstream.status_code

    passthrough = {
        header: upstream.headers[header]
        for header in ('Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges')
        if header in upstream.headers
    }
    passthrough.setdefault('Content-Type', 'video/mp4')

    def generate():
        try:
            for chunk in upstream.iter_content(chunk_size=64 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return Response(stream_with_context(generate()), status=upstream.status_code,
                    headers=passthrough)
