"""Ollama Cloud client using the native remote Ollama API."""

from urllib.parse import urlparse

import requests

from providers.base import BaseClient


class OllamaCloudClient(BaseClient):
    timeout = 180

    def __init__(self, config: dict):
        self.api_key = str(config.get('api_key') or '').strip()
        if not self.api_key:
            raise ValueError('Ollama Cloud: api_key erforderlich')
        endpoint = str(config.get('api_endpoint') or 'https://ollama.com').rstrip('/')
        parsed = urlparse(endpoint)
        if parsed.scheme != 'https' or parsed.hostname != 'ollama.com':
            raise ValueError('Ollama Cloud: api_endpoint must use https://ollama.com')
        if endpoint.endswith('/api'):
            endpoint = endpoint[:-4]
        self.base_url = endpoint
        self.headers = {'Authorization': f'Bearer {self.api_key}'}

    @staticmethod
    def _error(operation: str, exc: Exception) -> RuntimeError:
        response = getattr(exc, 'response', None)
        status = getattr(response, 'status_code', None)
        suffix = f' (HTTP {status})' if status else ''
        return RuntimeError(f'Ollama Cloud {operation} failed{suffix}')

    def get_models(self) -> list[str]:
        try:
            response = requests.get(
                f'{self.base_url}/api/tags', headers=self.headers, timeout=5,
            )
            response.raise_for_status()
            data = response.json()
            return sorted({
                model['name'] for model in data.get('models', [])
                if isinstance(model, dict) and model.get('name')
            })
        except Exception as exc:
            raise self._error('model listing', exc) from None

    def create_message(
        self, model: str, messages: list[dict], max_tokens: int = 600,
        *, tools: list[dict] | None = None,
    ) -> dict:
        payload = {
            'model': model,
            'messages': messages,
            'stream': False,
            'options': {'num_predict': max_tokens},
        }
        try:
            response = requests.post(
                f'{self.base_url}/api/chat', json=payload,
                headers=self.headers, timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
            return {
                'content': [{'text': data.get('message', {}).get('content', '')}],
                'usage': {
                    'input_tokens': data.get('prompt_eval_count', 0),
                    'output_tokens': data.get('eval_count', 0),
                },
            }
        except Exception as exc:
            raise self._error('chat request', exc) from None

    def health(self) -> bool:
        try:
            self.get_models()
            return True
        except RuntimeError:
            return False
