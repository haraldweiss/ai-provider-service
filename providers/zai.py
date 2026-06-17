# SPDX-License-Identifier: AGPL-3.0-or-later
"""z.ai (Zhipu / GLM) Client — OpenAI-compatible hosted gateway.

Drop-in OpenAI SDK usage against https://api.z.ai/api/paas/v4. Antwort-Format
ist Claude-kompatibel (siehe providers.base).

Zugriffssteuerung passiert im Dispatcher (`_is_zai_server_key_allowed`): der
zentrale ZAI_API_KEY ist nur für den Owner freigeschaltet; andere User
übergeben ihren eigenen Key via ProviderConfig. Der Client selbst kennt diese
Unterscheidung nicht — er bekommt entweder einen Key oder fällt auf den
System-Key zurück.
"""

from __future__ import annotations
import logging
from openai import OpenAI
from providers.base import BaseClient
from config import Config

logger = logging.getLogger(__name__)


def _extract_content(choice) -> str:
    """Pull text from a chat choice. GLM-Reasoning-Modelle legen Output teils
    in reasoning_content statt content ab — ist content leer, dort nachsehen."""
    msg = getattr(choice, 'message', None)
    text = getattr(msg, 'content', None) or ''
    if not text:
        text = getattr(msg, 'reasoning_content', None) or ''
    return text


class ZaiClient(BaseClient):
    def __init__(self, config: dict):
        api_key = config.get('api_key') or Config.ZAI_API_KEY
        if not api_key:
            raise ValueError("z.ai: api_key oder ZAI_API_KEY erforderlich")
        base_url = config.get('api_endpoint') or Config.ZAI_BASE_URL
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def get_models(self) -> list[str]:
        try:
            ids = [m.id for m in self.client.models.list().data
                   if 'glm' in m.id.lower()]
            return sorted(ids)
        except Exception as e:
            logger.warning(f'z.ai get_models failed: {e}')
            return []

    def create_message(self, model: str, messages: list[dict], max_tokens: int = 600,
                       *, tools: list[dict] | None = None) -> dict:
        r = self.client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens,
        )
        return {
            'content': [{'text': _extract_content(r.choices[0])}],
            'usage': {
                'input_tokens': r.usage.prompt_tokens,
                'output_tokens': r.usage.completion_tokens,
            },
        }

    def health(self) -> bool:
        try:
            self.client.models.list()
            return True
        except Exception:
            return False
