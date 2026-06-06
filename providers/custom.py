"""Generischer OpenAI-kompatibler Endpoint (LM Studio, llama.cpp server, vLLM)."""

from __future__ import annotations
import logging
import requests
from providers.base import BaseClient

logger = logging.getLogger(__name__)


class CustomClient(BaseClient):
    def __init__(self, config: dict):
        endpoint = config.get('api_endpoint')
        if not endpoint:
            raise ValueError("Custom: api_endpoint erforderlich")
        self.endpoint = endpoint.rstrip('/')
        self.api_key = config.get('api_key') or None

    def _headers(self) -> dict:
        h = {'Content-Type': 'application/json'}
        if self.api_key:
            h['Authorization'] = f'Bearer {self.api_key}'
        return h

    def get_models(self) -> list[str]:
        try:
            r = requests.get(f'{self.endpoint}/v1/models', headers=self._headers(), timeout=5)
            r.raise_for_status()
            data = r.json()
            return [m['id'] for m in data.get('data', [])]
        except Exception as e:
            logger.warning(f'Custom get_models failed: {e}')
            return []

    def create_message(self, model: str, messages: list[dict], max_tokens: int = 600, *, tools: list[dict] | None = None) -> dict:
        payload = {'model': model, 'messages': messages, 'max_tokens': max_tokens}
        r = requests.post(
            f'{self.endpoint}/v1/chat/completions',
            json=payload, headers=self._headers(), timeout=self.timeout,
        )
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
            r = requests.get(f'{self.endpoint}/v1/models', headers=self._headers(), timeout=3)
            return r.ok
        except Exception:
            return False
