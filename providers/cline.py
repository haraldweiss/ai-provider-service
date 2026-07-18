"""Cline Client — OpenAI-compatible hosted gateway (https://api.cline.bot).

Cline exposes an OpenAI-compatible Chat Completions API at
``https://api.cline.bot/api/v1``. Authentication is a Bearer API key.

Model IDs follow the ``provider/model`` form, e.g.
``anthropic/claude-sonnet-4-6``.
GET /models returns 404 — model list falls back to pricing_overrides_cline.json.

Response format: {"data": {"choices": [...], "usage": ...}, "success": true}
"""

from __future__ import annotations
import json
import logging
from pathlib import Path
import httpx
from providers.base import BaseClient
from config import Config

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = 'https://api.cline.bot/api/v1'
_OVERRIDE_PATH = Path(__file__).parent.parent / 'pricing_overrides_cline.json'
_HEALTH_MODEL = 'openai/gpt-4o-mini'


class ClineClient(BaseClient):
    def __init__(self, config: dict):
        self._api_key = config.get('api_key') or Config.CLINE_API_KEY
        if not self._api_key:
            raise ValueError("Cline: api_key erforderlich")
        self._base_url = config.get('api_endpoint') or Config.CLINE_BASE_URL or DEFAULT_BASE_URL

    def _models_from_override(self) -> list[str]:
        try:
            data = json.loads(_OVERRIDE_PATH.read_text())
            return sorted(k[7:] for k in data if k.startswith('cline::'))
        except Exception as e:
            logger.warning(f'Cline model override fallback failed: {e}')
            return []

    def get_models(self) -> list[str]:
        return self._models_from_override()

    def create_message(self, model: str, messages: list[dict], max_tokens: int = 600, *, tools: list[dict] | None = None) -> dict:
        body = {
            'model': model,
            'messages': messages,
            'max_tokens': max(16, max_tokens),  # Cline min 16
        }
        with httpx.Client(timeout=120) as hc:
            r = hc.post(
                f'{self._base_url}/chat/completions',
                json=body,
                headers={'Authorization': f'Bearer {self._api_key}'},
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
                    headers={'Authorization': f'Bearer {self._api_key}'},
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
