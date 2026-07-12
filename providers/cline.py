"""Cline Client — OpenAI-compatible hosted gateway (https://api.cline.bot).

Cline exposes an OpenAI-compatible Chat Completions API at
``https://api.cline.bot/api/v1``. Authentication is a Bearer API key
(configured per user via ProviderConfig — Cline has no shared server key).

Model IDs follow the ``provider/model`` form, e.g.
``anthropic/claude-sonnet-4-6``. The gateway prefixes them with ``cline/``
for routing, so a request for ``cline/anthropic/claude-sonnet-4-6`` is
split on the first slash into provider ``cline`` and model name
``anthropic/claude-sonnet-4-6`` — the model name keeps its own slash intact.

Cline's API does NOT expose a GET /models endpoint (returns 404).
``get_models()`` falls back to the 513 model IDs from
``pricing_overrides_cline.json``, sourced from Cline's OSS catalog.
``health()`` treats a 404 as "server is alive".
"""

from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Optional
from openai import OpenAI, NotFoundError, APIStatusError
from providers.base import BaseClient
from config import Config

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = 'https://api.cline.bot/api/v1'

# Path to the pricing override file relative to providers/ (grandparent dir).
_OVERRIDE_PATH = Path(__file__).parent.parent / 'pricing_overrides_cline.json'


class ClineClient(BaseClient):
    def __init__(self, config: dict):
        api_key = config.get('api_key') or Config.CLINE_API_KEY
        if not api_key:
            raise ValueError("Cline: api_key erforderlich")
        base_url = config.get('api_endpoint') or Config.CLINE_BASE_URL or DEFAULT_BASE_URL
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self._base_url = base_url

    def _models_from_override(self) -> list[str]:
        """Fallback: extract model IDs from pricing_overrides_cline.json."""
        try:
            data = json.loads(_OVERRIDE_PATH.read_text())
            return sorted(k[7:] for k in data if k.startswith('cline::'))
        except Exception as e:
            logger.warning(f'Cline model override fallback failed: {e}')
            return []

    def get_models(self) -> list[str]:
        try:
            return sorted(m.id for m in self.client.models.list().data)
        except NotFoundError:
            # Cline's API doesn't have a GET /models endpoint (404) —
            # fall back to the catalog-derived override file.
            return self._models_from_override()
        except Exception as e:
            logger.warning(f'Cline get_models failed: {e}')
            return []

    def create_message(self, model: str, messages: list[dict], max_tokens: int = 600, *, tools: list[dict] | None = None) -> dict:
        r = self.client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens
        )
        return {
            'content': [{'text': r.choices[0].message.content or ''}],
            'usage': {
                'input_tokens': r.usage.prompt_tokens,
                'output_tokens': r.usage.completion_tokens,
            },
        }

    def health(self) -> bool:
        try:
            self.client.models.list()
            return True
        except NotFoundError:
            # 404 = server reachable, endpoint not supported (Cline).
            return True
        except Exception:
            return False
