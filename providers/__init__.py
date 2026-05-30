"""Provider-Factory: erstellt Clients basierend auf provider_id + Config."""

from __future__ import annotations
import logging
from typing import Optional
from providers.base import BaseClient

logger = logging.getLogger(__name__)

# Soft-fail on optional dependencies
HAS_ANTHROPIC = False
HAS_OPENAI = False

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    logger.debug("anthropic not installed; Claude provider will be unavailable")

try:
    import openai
    HAS_OPENAI = True
except ImportError:
    logger.debug("openai not installed; OpenAI provider will be unavailable")


PROVIDER_REGISTRY = {
    'claude': {
        'name': 'Claude (Anthropic)',
        'system': True,
        'requires': [],
        'optional': [],
        'available': HAS_ANTHROPIC,  # New: track availability
    },
    'ollama': {
        'name': 'Ollama (lokal)',
        'system': True,
        'requires': [],
        'optional': [],
        'available': True,  # Ollama is always available (local)
    },
    'openai': {
        'name': 'ChatGPT / OpenAI',
        'system': False,
        'requires': ['api_key'],
        'optional': ['organization_id'],
        'available': HAS_OPENAI,
    },
    'mammouth': {
        'name': 'Mammouth',
        'system': False,
        'requires': ['api_endpoint'],
        'optional': [],
        'available': True,  # HTTP-based, always available
    },
    'custom': {
        'name': 'Custom OpenAI-compatible Endpoint',
        'system': False,
        'requires': ['api_endpoint'],
        'optional': ['api_key', 'name'],
        'available': True,
    },
}


def get_client(provider_id: str, config: Optional[dict] = None) -> BaseClient:
    """Erstellt einen Client für den angegebenen Provider.

    `config` enthält die provider-spezifischen Felder (api_key, api_endpoint, ...).
    Für System-Provider (Claude, Ollama) wird Config aus Environment gezogen.
    
    Raises:
        ValueError: If provider not found or dependencies missing
    """
    config = config or {}
    
    if provider_id not in PROVIDER_REGISTRY:
        raise ValueError(f"Unbekannter Provider: {provider_id}")
    
    provider_info = PROVIDER_REGISTRY[provider_id]
    
    # Check if provider dependencies are installed
    if not provider_info.get('available', False):
        raise ImportError(
            f"Provider '{provider_id}' is not available. "
            f"Install dependencies with: pip install ai-provider-service[providers]"
        )

    if provider_id == 'claude':
        from providers.claude import ClaudeClient
        return ClaudeClient(config)
    elif provider_id == 'ollama':
        from providers.ollama import OllamaClient
        return OllamaClient(config)
    elif provider_id == 'openai':
        from providers.openai_client import OpenAIClient
        return OpenAIClient(config)
    elif provider_id == 'mammouth':
        from providers.mammouth import MammouthClient
        return MammouthClient(config)
    elif provider_id == 'custom':
        from providers.custom import CustomClient
        return CustomClient(config)
    
    raise ValueError(f"Provider {provider_id} not implemented")


def list_provider_ids() -> list[str]:
    """List all provider IDs (including unavailable ones)."""
    return list(PROVIDER_REGISTRY.keys())


def list_available_providers() -> list[str]:
    """List only available providers (dependencies installed)."""
    return [pid for pid, info in PROVIDER_REGISTRY.items() if info.get('available', False)]


def is_provider_available(provider_id: str) -> bool:
    """Check if a specific provider is available."""
    return PROVIDER_REGISTRY.get(provider_id, {}).get('available', False)
