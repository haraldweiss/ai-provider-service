"""OpenRouter Client — OpenAI-compatible hosted gateway with free model support.

OpenRouter offers many free models that require no API key. This client
discovers free models from the API and filters to free-only when the system
key is used (or no key is configured), following the same pattern as
providers/opencode.py.
"""

from __future__ import annotations
import json
import logging
import os
import time
from openai import OpenAI
from providers.base import BaseClient
from config import Config

logger = logging.getLogger(__name__)

_FREE_CACHE_FILE = '/tmp/openrouter_free_models.json'
_FREE_CACHE_TTL = 86400


def _is_free_model(api_model) -> bool:
    """Check if a model entry from OpenRouter is free."""
    pricing = getattr(api_model, 'pricing', None) or {}
    prompt = pricing.get('prompt', '0') if isinstance(pricing, dict) else getattr(pricing, 'prompt', '0')
    completion = pricing.get('completion', '0') if isinstance(pricing, dict) else getattr(pricing, 'completion', '0')
    try:
        return float(prompt) == 0.0 and float(completion) == 0.0
    except (ValueError, TypeError):
        return False


def _get_cached_free_models(client: OpenAI) -> list[str]:
    """Fetch free models from cache or API."""
    now = time.time()
    stale_models: list[str] = []
    try:
        if os.path.exists(_FREE_CACHE_FILE):
            with open(_FREE_CACHE_FILE) as f:
                cached = json.load(f)
            stale_models = cached.get('models', [])
            if now - cached.get('ts', 0) < _FREE_CACHE_TTL:
                return stale_models
    except Exception:
        pass

    return _refresh_free_models(client, fallback=stale_models)


def _iter_model_entries(raw) -> list:
    return list(getattr(raw, 'data', raw))


def _refresh_free_models(client: OpenAI, fallback: list[str] | None = None) -> list[str]:
    """Fetch current free models from OpenRouter API, update cache."""
    try:
        raw = client.models.list()
        free_models = sorted(
            m.id for m in _iter_model_entries(raw) if _is_free_model(m)
        )
    except Exception as e:
        logger.warning('OpenRouter free model discovery failed: %s', e)
        return fallback or []

    try:
        os.makedirs(os.path.dirname(_FREE_CACHE_FILE) or '.', exist_ok=True)
        with open(_FREE_CACHE_FILE, 'w') as f:
            json.dump({'ts': time.time(), 'models': free_models}, f)
    except Exception:
        pass

    logger.info('Discovered %d OpenRouter free models', len(free_models))
    return free_models


def _make_openai_client(api_key: str | None, base_url: str) -> OpenAI:
    """Create an OpenAI client that works across SDK versions.

    OpenRouter allows anonymous access for free models, but the OpenAI SDK
    requires an api_key to be set. Use a placeholder when none is configured
    so the client can be instantiated for anonymous/free-model use.
    """
    if not api_key:
        api_key = 'sk-anonymous'
    return OpenAI(api_key=api_key, base_url=base_url)


class OpenRouterClient(BaseClient):
    def __init__(self, config: dict):
        self._free_only = config.get('_free_only', False)
        api_key = config.get('api_key') or Config.OPENROUTER_API_KEY or None
        base_url = config.get('api_endpoint') or Config.OPENROUTER_BASE_URL
        self.client = _make_openai_client(api_key, base_url)
        self._free_models: list[str] | None = None
        self._base_url = base_url

    def get_models(self) -> list[str]:
        try:
            all_models = sorted(m.id for m in self.client.models.list().data)
            if self._free_only:
                free_set = set(self.get_free_models())
                filtered = [m for m in all_models if m in free_set]
                logger.info('OpenRouter free-only mode: %d/%d models shown',
                            len(filtered), len(all_models))
                return filtered
            return all_models
        except Exception as e:
            logger.warning('OpenRouter get_models failed: %s', e)
            return []

    def get_free_models(self) -> list[str]:
        if self._free_models is None:
            self._free_models = _get_cached_free_models(self.client)
        return self._free_models

    @classmethod
    def try_refresh_free_models(cls) -> list[str]:
        """Proactive refresh using optional OPENROUTER_API_KEY."""
        client = _make_openai_client(
            Config.OPENROUTER_API_KEY or None,
            Config.OPENROUTER_BASE_URL,
        )
        return _refresh_free_models(client)

    def create_message(self, model: str, messages: list[dict], max_tokens: int = 600,
                       *, tools: list[dict] | None = None) -> dict:
        kwargs = dict(model=model, messages=messages, max_tokens=max_tokens)
        if tools:
            kwargs['tools'] = tools

        r = self.client.chat.completions.create(**kwargs)
        choice = r.choices[0]
        msg = getattr(choice, 'message', None) or {}
        text = ''
        if hasattr(msg, 'content'):
            text = msg.content or ''
        elif isinstance(msg, dict):
            text = msg.get('content', '')

        return {
            'content': [{'text': text}],
            'usage': {
                'input_tokens': r.usage.prompt_tokens if r.usage else 0,
                'output_tokens': r.usage.completion_tokens if r.usage else 0,
            },
        }

    def health(self) -> bool:
        try:
            self.client.models.list()
            return True
        except Exception:
            return False