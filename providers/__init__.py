"""Provider-Factory: erstellt Clients basierend auf provider_id + Config."""

from __future__ import annotations
from typing import Optional
from providers.base import BaseClient


PROVIDER_REGISTRY = {
    'claude': {
        'name': 'Claude (Anthropic)',
        'system': True,
        'requires': [],
        'optional': [],
    },
    'ollama': {
        'name': 'Ollama (lokal)',
        'system': True,
        'requires': [],
        'optional': [],
    },
    'openai': {
        'name': 'ChatGPT / OpenAI',
        'system': False,
        'requires': ['api_key'],
        'optional': ['organization_id'],
    },
    'mammouth': {
        'name': 'Mammouth',
        'system': False,
        'requires': ['api_endpoint'],
        'optional': [],
    },
    'custom': {
        'name': 'Custom OpenAI-compatible Endpoint',
        'system': False,
        'requires': ['api_endpoint'],
        'optional': ['api_key', 'name'],
    },
    'opencode': {
        'name': 'opencode.ai (Zen)',
        'system': False,
        'requires': ['api_key'],
        'optional': ['api_endpoint'],
    },
}


def get_client(provider_id: str, config: Optional[dict] = None) -> BaseClient:
    """Erstellt einen Client für den angegebenen Provider.

    `config` enthält die provider-spezifischen Felder (api_key, api_endpoint, ...).
    Für System-Provider (Claude, Ollama) wird Config aus Environment gezogen.
    """
    config = config or {}

    if provider_id == 'claude':
        from providers.claude import ClaudeClient
        return ClaudeClient(config)
    if provider_id == 'ollama':
        from providers.ollama import OllamaClient
        return OllamaClient(config)
    if provider_id == 'openai':
        from providers.openai_client import OpenAIClient
        return OpenAIClient(config)
    if provider_id == 'mammouth':
        from providers.mammouth import MammouthClient
        return MammouthClient(config)
    if provider_id == 'custom':
        from providers.custom import CustomClient
        return CustomClient(config)
    if provider_id == 'opencode':
        from providers.opencode import OpencodeClient
        return OpencodeClient(config)

    raise ValueError(f"Unbekannter Provider: {provider_id}")


def list_provider_ids() -> list[str]:
    return list(PROVIDER_REGISTRY.keys())
