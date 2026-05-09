"""Mammouth Client (lokales Modell mit OpenAI-kompatiblem Endpoint)."""

from __future__ import annotations
import logging
import requests
from providers.base import BaseClient

logger = logging.getLogger(__name__)


class MammouthClient(BaseClient):
    def __init__(self, config: dict):
        endpoint = config.get('api_endpoint')
        if not endpoint:
            raise ValueError("Mammouth: api_endpoint erforderlich")
        self.endpoint = endpoint.rstrip('/')

    def get_models(self) -> list[str]:
        try:
            r = requests.get(f'{self.endpoint}/models', timeout=5)
            r.raise_for_status()
            data = r.json()
            models = data.get('models') or data.get('data') or []
            return [m['id'] if isinstance(m, dict) else m for m in models]
        except Exception as e:
            logger.warning(f'Mammouth get_models failed: {e}')
            return []

    def create_message(self, model: str, messages: list[dict], max_tokens: int = 600) -> dict:
        payload = {'model': model, 'messages': messages, 'max_tokens': max_tokens}
        r = requests.post(f'{self.endpoint}/chat/completions', json=payload, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        return {
            'content': [{'text': data['choices'][0]['message']['content']}],
            'usage': {
                'input_tokens': data.get('usage', {}).get('prompt_tokens', 0),
                'output_tokens': data.get('usage', {}).get('completion_tokens', 0),
            }
        }

    def health(self) -> bool:
        try:
            r = requests.get(f'{self.endpoint}/models', timeout=3)
            return r.ok
        except Exception:
            return False
