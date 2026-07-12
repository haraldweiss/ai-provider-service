"""Cline Client — OpenAI-compatible hosted gateway (https://api.cline.bot).

Cline exposes an OpenAI-compatible Chat Completions API at
``https://api.cline.bot/api/v1``. Authentication is a Bearer API key
(configured per user via ProviderConfig — Cline has no shared server key).

Model IDs follow the ``provider/model`` form, e.g.
``anthropic/claude-sonnet-4-6``. The gateway prefixes them with ``cline/``
for routing, so a request for ``cline/anthropic/claude-sonnet-4-6`` is
split on the first slash into provider ``cline`` and model name
``anthropic/claude-sonnet-4-6`` — the model name keeps its own slash intact.
"""

from __future__ import annotations
import logging
from typing import Optional
from openai import OpenAI
from providers.base import BaseClient
from config import Config

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = 'https://api.cline.bot/api/v1'


class ClineClient(BaseClient):
    def __init__(self, config: dict):
        api_key = config.get('api_key') or Config.CLINE_API_KEY
        if not api_key:
            raise ValueError("Cline: api_key erforderlich")
        base_url = config.get('api_endpoint') or Config.CLINE_BASE_URL or DEFAULT_BASE_URL
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self._base_url = base_url

    def get_models(self) -> list[str]:
        try:
            return sorted(m.id for m in self.client.models.list().data)
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
        except Exception:
            return False
