"""Provider visibility helpers for user-facing provider/model lists."""

from __future__ import annotations

from api.gate import has_personal_api_key
from config import Config
from providers import PROVIDER_REGISTRY


def _server_key_available_for_user(user_id: str, provider_id: str) -> bool:
    from dispatcher import _is_claude_server_key_allowed, _is_zai_server_key_allowed

    if provider_id == 'claude':
        return bool(Config.ANTHROPIC_API_KEY) and _is_claude_server_key_allowed(user_id)
    if provider_id == 'opencode':
        return bool(Config.OPENCODE_API_KEY)
    if provider_id == 'openrouter':
        return True
    if provider_id == 'zai':
        return bool(Config.ZAI_API_KEY) and _is_zai_server_key_allowed(user_id)
    return False


def provider_requires_user_key(user_id: str, provider_id: str) -> bool:
    if provider_id == 'ollama':
        return False
    meta = PROVIDER_REGISTRY.get(provider_id, {})
    if not meta.get('personal_api_key'):
        return False
    if has_personal_api_key(user_id, provider_id):
        return False
    return not _server_key_available_for_user(user_id, provider_id)


def hidden_key_provider_rows(user_id: str) -> list[dict]:
    rows = []
    for provider_id, meta in PROVIDER_REGISTRY.items():
        if not provider_requires_user_key(user_id, provider_id):
            continue
        rows.append({
            'id': provider_id,
            'name': meta['name'],
            'reason': 'api_key_required',
            'message': 'Add a personal API key to enable this provider.',
        })
    return rows


def availability_hint(hidden_providers: list[dict]) -> dict:
    count = len(hidden_providers)
    if count == 0:
        return {'hidden_provider_count': 0, 'message': ''}
    return {
        'hidden_provider_count': count,
        'message': (
            'More AI providers are available when you add the corresponding '
            'personal API key in settings.'
        ),
    }
