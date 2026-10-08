"""OpenAI / ChatGPT Client."""

from __future__ import annotations
import logging
import uuid
from providers.base import BaseClient
from providers.response_metadata import completion_metadata, reported_cost

logger = logging.getLogger(__name__)


class OpenAIClient(BaseClient):
    def __init__(self, config: dict):
        api_key = config.get('api_key')
        if not api_key:
            raise ValueError("OpenAI: api_key erforderlich")
        from openai import OpenAI
        org = config.get('organization_id') or None
        self.client = OpenAI(
            api_key=api_key,
            organization=org,
            default_headers={'X-Client-Request-Id': str(uuid.uuid4())}
        )

    def get_models(self) -> list[str]:
        try:
            models = self.client.models.list()
            ids = [m.id for m in models.data if 'gpt' in m.id.lower()]
            return sorted(ids, reverse=True)
        except Exception as e:
            logger.warning(f'OpenAI get_models failed: {e}')
            return []

    def create_message(self, model: str, messages: list[dict], max_tokens: int = 600, *, tools: list[dict] | None = None) -> dict:
        kwargs = dict(model=model, messages=messages, max_tokens=max_tokens)
        if tools:
            kwargs['tools'] = tools
        # Add unique request ID for tracking
        kwargs['extra_headers'] = {'X-Client-Request-Id': str(uuid.uuid4())}
        r = self.client.chat.completions.create(**kwargs)
        return {
            **completion_metadata(r),
            'content': [{'text': r.choices[0].message.content or ''}],
            'usage': {
                **reported_cost(r.usage),
                'input_tokens': getattr(r.usage, 'prompt_tokens', None),
                'output_tokens': getattr(r.usage, 'completion_tokens', None),
            }
        }

    def health(self) -> bool:
        try:
            self.client.models.list()
            return True
        except Exception:
            return False
