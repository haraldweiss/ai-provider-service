"""Ollama Client (lokal, /api/tags + /api/chat)."""

from __future__ import annotations
import logging
import requests
from providers.base import BaseClient
from config import Config

logger = logging.getLogger(__name__)


class OllamaClient(BaseClient):
    timeout = 60  # lokale Models können langsamer antworten

    def __init__(self, config: dict):
        # Wenn config keine URL hat → fallback auf system-default (OLLAMA_URL).
        # Ollama läuft typischerweise lokal, daher meist 127.0.0.1:11434.
        url = (config or {}).get('api_endpoint') or Config.OLLAMA_URL
        self.base_url = url.rstrip('/')

    def get_models(self) -> list[str]:
        try:
            r = requests.get(f'{self.base_url}/api/tags', timeout=5)
            r.raise_for_status()
            data = r.json()
            return [m['name'] for m in data.get('models', [])]
        except Exception as e:
            logger.warning(f'Ollama get_models failed: {e}')
            return []

    def create_message(self, model: str, messages: list[dict], max_tokens: int = 600) -> dict:
        payload = {
            'model': model,
            'messages': messages,
            'stream': False,
            'options': {'num_predict': max_tokens},
        }
        r = requests.post(f'{self.base_url}/api/chat', json=payload, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        # Ollama trackt keine Token-Counts wie Claude — wir liefern 0/0 zurück.
        # eval_count = output tokens, prompt_eval_count = input tokens (wenn verfügbar).
        return {
            'content': [{'text': data.get('message', {}).get('content', '')}],
            'usage': {
                'input_tokens': data.get('prompt_eval_count', 0),
                'output_tokens': data.get('eval_count', 0),
            }
        }

    def health(self) -> bool:
        try:
            r = requests.get(f'{self.base_url}/api/tags', timeout=3)
            return r.ok
        except Exception:
            return False
