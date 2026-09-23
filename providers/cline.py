"""Cline Client — OpenAI-compatible hosted gateway (https://api.cline.bot).

Cline exposes an OpenAI-compatible Chat Completions API at
``https://api.cline.bot/api/v1``. Authentication is a Bearer API key.

Model IDs follow the ``provider/model`` form, e.g.
``anthropic/claude-sonnet-4-6``.

``GET /models`` returns the account's authoritative, currently servable model
list (it is public and needs no key). The committed
``pricing_overrides_cline.json`` is only a fallback: it is generated from
Cline's OSS client catalog and contains IDs the API rejects with 404 (e.g.
legacy ``Qwen/...``/``MiniMaxAI/...`` casing). Advertising the override list
was why the model picker offered models that failed with
``Provider cline rejected the request (HTTP 404)``.

Response format: {"data": {"choices": [...], "usage": ...}, "success": true}
"""

from __future__ import annotations
import json
import logging
import time
import uuid
from pathlib import Path
import httpx
from providers.base import BaseClient
from config import Config

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = 'https://api.cline.bot/api/v1'
_OVERRIDE_PATH = Path(__file__).parent.parent / 'pricing_overrides_cline.json'
_HEALTH_MODEL = 'openai/gpt-4o-mini'

# In-process TTL cache for the live /models list; the gateway refreshes
# /v1/models every MODEL_CACHE_TTL_SEC (default 60s), so this avoids an
# outbound call on every refresh without hiding catalog changes for long.
_LIVE_MODELS_TTL = 600
_live_models_cache: dict = {'ts': 0.0, 'models': []}


class ClineClient(BaseClient):
    def __init__(self, config: dict):
        self._api_key = config.get('api_key') or Config.CLINE_API_KEY
        if not self._api_key:
            raise ValueError("Cline: api_key erforderlich")
        self._base_url = config.get('api_endpoint') or Config.CLINE_BASE_URL or DEFAULT_BASE_URL

    def _get_headers(self, task_id: str | None = None) -> dict:
        """Return headers for Cline API requests including optional tracking headers.

        ``HTTP-Referer``/``X-Title`` are documented attribution headers;
        ``X-Task-ID`` is an optional unique task identifier (see
        https://docs.cline.bot/api/authentication).
        """
        headers = {
            'Authorization': f'Bearer {self._api_key}',
            'HTTP-Referer': 'https://ai-provider-service.wolfinisoftware.de',
            'X-Title': 'ai-provider-service',
        }
        if task_id:
            headers['X-Task-ID'] = task_id
        return headers

    def _models_from_override(self) -> list[str]:
        try:
            data = json.loads(_OVERRIDE_PATH.read_text())
            return sorted(k[7:] for k in data if k.startswith('cline::'))
        except Exception as e:
            logger.warning(f'Cline model override fallback failed: {e}')
            return []

    def _models_from_api(self) -> list[str]:
        """Fetch the live, servable model list from Cline's public /models."""
        now = time.time()
        if _live_models_cache['models'] and now - _live_models_cache['ts'] < _LIVE_MODELS_TTL:
            return list(_live_models_cache['models'])
        try:
            with httpx.Client(timeout=20) as hc:
                r = hc.get(f'{self._base_url}/models', headers=self._get_headers())
            r.raise_for_status()
            raw = r.json()
            entries = raw.get('data', raw) if isinstance(raw, dict) else raw
            ids = sorted(
                m['id'] for m in entries
                if isinstance(m, dict) and m.get('id')
            )
            if ids:
                _live_models_cache['models'] = ids
                _live_models_cache['ts'] = now
                logger.info('Cline: %d models from live API', len(ids))
                return ids
            logger.warning('Cline /models returned no usable entries; using override file')
        except Exception as e:
            logger.warning('Cline /models fetch failed (%s); using override file', e)
        return []

    def get_models(self) -> list[str]:
        models = self._models_from_api()
        if models:
            return models
        return self._models_from_override()

    def create_message(self, model: str, messages: list[dict], max_tokens: int = 600, *, tools: list[dict] | None = None) -> dict:
        body = {
            'model': model,
            'messages': messages,
            'max_tokens': max(16, max_tokens),  # Cline min 16
        }
        if tools:
            body['tools'] = tools
        with httpx.Client(timeout=120) as hc:
            r = hc.post(
                f'{self._base_url}/chat/completions',
                json=body,
                headers=self._get_headers(task_id=str(uuid.uuid4())),
            )
        r.raise_for_status()
        raw = r.json()
        # Cline wrapt in {"data": {choices, usage, ...}, "success": bool}
        data = raw.get('data', raw)
        choice = (data.get('choices') or [{}])[0]
        msg = choice.get('message', {})
        content = msg.get('content') or msg.get('reasoning_content') or ''
        usage = data.get('usage', {}) or {}
        return {
            'content': [{'text': content}],
            'usage': {
                'input_tokens': usage.get('prompt_tokens', 0),
                'output_tokens': usage.get('completion_tokens', 0),
            },
        }

    def health(self) -> bool:
        try:
            body = {
                'model': _HEALTH_MODEL,
                'messages': [{'role': 'user', 'content': 'ping'}],
                'max_tokens': 16,
            }
            with httpx.Client(timeout=15) as hc:
                r = hc.post(
                    f'{self._base_url}/chat/completions',
                    json=body,
                    headers=self._get_headers(),
                )
            if r.status_code >= 500:
                return False
            # Check that we actually got a response with content
            raw = r.json()
            data = raw.get('data', raw)
            choices = data.get('choices', [])
            return len(choices) > 0
        except Exception:
            return False
