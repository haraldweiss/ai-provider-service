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

    def create_message(self, model: str, messages: list[dict], max_tokens: int = 600,
                       *, tools: list[dict] | None = None) -> dict:
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
            # Cache the (stable) system prompt as an ephemeral block. Anthropic ignores
            # the cache hint silently when the block is below the model's minimum
            # cacheable size (1024 tok Opus/Sonnet, 2048 tok Haiku), so this is safe
            # for short prompts too. Cache hits cost ~10% of normal input tokens.
            kwargs['system'] = [{
                'type': 'text',
                'text': system_msg,
                'cache_control': {'type': 'ephemeral'},
            }]
        if tools:
            kwargs['tools'] = tools

        response = self.client.messages.create(**kwargs)
        usage = response.usage

        # Project every content block (text or tool_use) into a plain dict so
        # callers can iterate without depending on the Anthropic SDK types.
        # Legacy callers reading result['content'][0]['text'] keep working
        # because Anthropic emits text blocks first by convention.
        content_blocks: list[dict] = []
        tool_calls: list[dict] = []
        for block in response.content or []:
            btype = getattr(block, 'type', None)
            if btype == 'text':
                content_blocks.append({'type': 'text',
                                       'text': getattr(block, 'text', '')})
            elif btype == 'tool_use':
                tc = {'id': getattr(block, 'id', ''),
                      'name': getattr(block, 'name', ''),
                      'input': getattr(block, 'input', {}) or {}}
                content_blocks.append({'type': 'tool_use', **tc})
                tool_calls.append(tc)
            else:
                # Forward unknown block types verbatim (best-effort, may
                # appear with future Anthropic API extensions).
                content_blocks.append({'type': btype or 'unknown'})

        # Always include at least one text block so existing callers that do
        # `result['content'][0]['text']` don't blow up on tool-use-only turns.
        if not content_blocks:
            content_blocks = [{'type': 'text', 'text': ''}]
        elif content_blocks[0].get('type') != 'text':
            content_blocks.insert(0, {'type': 'text', 'text': ''})

        return {
            'content': content_blocks,
            'tool_calls': tool_calls,
            'stop_reason': getattr(response, 'stop_reason', 'end_turn'),
            'usage': {
                'input_tokens': usage.input_tokens,
                'output_tokens': usage.output_tokens,
                'cache_creation_input_tokens': getattr(usage, 'cache_creation_input_tokens', 0) or 0,
                'cache_read_input_tokens': getattr(usage, 'cache_read_input_tokens', 0) or 0,
            }
        }

    def health(self) -> bool:
        # Claude hat keinen günstigen Health-Endpoint — wenn API-Key gesetzt, gehen
        # wir von erreichbar aus. Echte Erreichbarkeit zeigt sich beim ersten Call.
        return self.client is not None
