"""Opencode.ai (Zen) Client — OpenAI-compatible hosted gateway.

API surface assumed Bearer + OpenAI-compatible /v1/chat/completions and
/v1/models. If opencode.ai's published auth scheme differs at integration
time (OAuth, JWT, custom header), patch __init__ accordingly.
"""

from __future__ import annotations
import logging
from openai import OpenAI
from providers.base import BaseClient
from config import Config

logger = logging.getLogger(__name__)


class OpencodeClient(BaseClient):
    def __init__(self, config: dict):
        api_key = config.get('api_key')
        if not api_key:
            raise ValueError("Opencode: api_key erforderlich")
        base_url = config.get('api_endpoint') or Config.OPENCODE_BASE_URL
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def get_models(self) -> list[str]:
        try:
            return sorted(m.id for m in self.client.models.list().data)
        except Exception as e:
            logger.warning(f'Opencode get_models failed: {e}')
            return []

    def create_message(self, model: str, messages: list[dict], max_tokens: int = 600) -> dict:
        r = self.client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens
        )
        return {
            'content': [{'text': r.choices[0].message.content}],
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
