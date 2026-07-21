"""oMLX client for the MacBook's OpenAI-compatible local server."""

from __future__ import annotations

import logging

import requests

from config import Config
from providers.base import BaseClient

logger = logging.getLogger(__name__)


class OmlxClient(BaseClient):
    timeout = 180

    def __init__(self, config: dict):
        self._base_url = (config.get('api_endpoint') or Config.OMLX_BASE_URL).rstrip('/')
        self._api_key = config.get('api_key') or Config.OMLX_API_KEY
        if not self._api_key:
            raise ValueError('oMLX: api_key oder OMLX_API_KEY erforderlich')

    def _headers(self) -> dict:
        return {'Authorization': f'Bearer {self._api_key}'}

    def get_models(self) -> list[str]:
        try:
            response = requests.get(
                f'{self._base_url}/models', headers=self._headers(), timeout=5,
            )
            response.raise_for_status()
            return [model['id'] for model in response.json().get('data', [])]
        except Exception as error:
            logger.warning('oMLX get_models failed: %s', type(error).__name__)
            return []

    def create_message(self, model: str, messages: list[dict], max_tokens: int = 600,
                       *, tools: list[dict] | None = None) -> dict:
        payload = {'model': model, 'messages': messages, 'max_tokens': max_tokens}
        if tools:
            payload['tools'] = tools
        response = requests.post(
            f'{self._base_url}/chat/completions', json=payload,
            headers=self._headers(), timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        usage = data.get('usage', {})
        return {
            'content': [{'text': data['choices'][0]['message'].get('content') or ''}],
            'usage': {
                'input_tokens': usage.get('prompt_tokens', 0),
                'output_tokens': usage.get('completion_tokens', 0),
            },
        }

    def health(self) -> bool:
        try:
            return requests.get(
                f'{self._base_url}/models', headers=self._headers(), timeout=3,
            ).ok
        except Exception:
            return False
