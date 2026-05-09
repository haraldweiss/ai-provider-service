"""OpenAI / ChatGPT Client."""

from __future__ import annotations
import logging
from providers.base import BaseClient

logger = logging.getLogger(__name__)


class OpenAIClient(BaseClient):
    def __init__(self, config: dict):
        api_key = config.get('api_key')
        if not api_key:
            raise ValueError("OpenAI: api_key erforderlich")
        from openai import OpenAI
        org = config.get('organization_id') or None
        self.client = OpenAI(api_key=api_key, organization=org)

    def get_models(self) -> list[str]:
        try:
            models = self.client.models.list()
            ids = [m.id for m in models.data if 'gpt' in m.id.lower()]
            return sorted(ids, reverse=True)
        except Exception as e:
            logger.warning(f'OpenAI get_models failed: {e}')
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
            }
        }

    def health(self) -> bool:
        try:
            self.client.models.list()
            return True
        except Exception:
            return False
