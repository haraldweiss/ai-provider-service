"""Claude (Anthropic) Client."""

from __future__ import annotations
import logging
from providers.base import BaseClient
from config import Config

logger = logging.getLogger(__name__)

# Statische Model-Liste — Anthropic exposed keinen /models Endpoint mit allen
# generation-relevanten Models. Bei Bedarf manuell ergänzen.
KNOWN_MODELS = [
    'claude-opus-4-7',
    'claude-sonnet-4-6',
    'claude-haiku-4-5-20251001',
]
DEFAULT_MODEL = 'claude-haiku-4-5-20251001'


class ClaudeClient(BaseClient):
    def __init__(self, config: dict):
        api_key = config.get('api_key') or Config.ANTHROPIC_API_KEY
        if not api_key:
            raise ValueError("Claude: api_key oder ANTHROPIC_API_KEY erforderlich")
        from anthropic import Anthropic
        self.client = Anthropic(api_key=api_key)

    def get_models(self) -> list[str]:
        return list(KNOWN_MODELS)

    def create_message(self, model: str, messages: list[dict], max_tokens: int = 600) -> dict:
        # Anthropic-spezifisch: kein 'system'-Role in der Liste, sondern als top-level.
        system_msg = None
        chat_msgs = []
        for m in messages:
            if m.get('role') == 'system':
                system_msg = m.get('content', '')
            else:
                chat_msgs.append(m)

        kwargs = {
            'model': model or DEFAULT_MODEL,
            'max_tokens': max_tokens,
            'messages': chat_msgs,
        }
        if system_msg:
            kwargs['system'] = system_msg

        response = self.client.messages.create(**kwargs)
        return {
            'content': [{'text': response.content[0].text if response.content else ''}],
            'usage': {
                'input_tokens': response.usage.input_tokens,
                'output_tokens': response.usage.output_tokens,
            }
        }

    def health(self) -> bool:
        # Claude hat keinen günstigen Health-Endpoint — wenn API-Key gesetzt, gehen
        # wir von erreichbar aus. Echte Erreichbarkeit zeigt sich beim ersten Call.
        return self.client is not None
