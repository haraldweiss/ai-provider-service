"""Claude (Anthropic) Client."""

from __future__ import annotations
import hashlib
import logging
import time
from providers.base import BaseClient
from config import Config

logger = logging.getLogger(__name__)

# Fallback model list. Anthropic's Models API (GET /v1/models) is authoritative
# per API key: it reports the models that key may actually call (active and
# deprecated, never retired). This static copy only covers the case where that
# discovery call fails, so it must not contain retired IDs.
# Source: https://platform.claude.com/docs/en/about-claude/models/overview
KNOWN_MODELS = [
    'claude-fable-5-2',
    'claude-opus-5-5',
    'claude-sonnet-5-5',
    'claude-haiku-5-5',
    'claude-fable-5-1',
    'claude-opus-5',
    'claude-opus-4-8',
    'claude-opus-4-7',
    'claude-opus-4-6',
    'claude-opus-4-5',
    'claude-sonnet-5',
    'claude-sonnet-4-6',
    'claude-sonnet-4-5',
    'claude-haiku-4-5',
]
DEFAULT_MODEL = 'claude-haiku-5-5'

# Live model discovery is cheap but not free; /v1/models is requested often
# enough that a short process-local cache keeps provider latency predictable.
MODELS_CACHE_TTL_SEC = 600

_models_cache: dict = {'key': None, 'ts': 0.0, 'models': []}


class ClaudeClient(BaseClient):
    def __init__(self, config: dict):
        api_key = config.get('api_key') or Config.ANTHROPIC_API_KEY
        if not api_key:
            raise ValueError("Claude: api_key oder ANTHROPIC_API_KEY erforderlich")
        from anthropic import Anthropic
        self._api_key = api_key
        # The SDK sends an API key in the `Authorization: Bearer` header, the
        # primary scheme documented by Anthropic. The `x-api-key` header is
        # documented as a legacy fallback and is not needed.
        self.client = Anthropic(api_key=api_key)

    def _models_from_api(self) -> list[str]:
        """Discover servable model IDs via GET /v1/models (process-local TTL)."""
        now = time.time()
        cache_key = hashlib.sha256(self._api_key.encode()).hexdigest()
        if (_models_cache.get('key') == cache_key and _models_cache['models']
                and now - _models_cache['ts'] < MODELS_CACHE_TTL_SEC):
            return list(_models_cache['models'])
        try:
            listed = self.client.models.list()
            ids = sorted({m.id for m in listed if getattr(m, 'id', None)})
            if ids:
                _models_cache.update({'key': cache_key, 'ts': now, 'models': ids})
                logger.info('Claude: %d models from Models API', len(ids))
                return ids
            logger.warning('Claude Models API returned no usable entries; '
                           'using static model list')
        except Exception as e:
            # Discovery failures must never break /v1/models or a chat call.
            logger.warning('Claude Models API fetch failed (%s); '
                           'using static model list', e)
        return []

    def get_models(self) -> list[str]:
        # Allow runtime override via env var (no code change needed for
        # new model additions/releases by Anthropic).
        if Config.CLAUDE_MODEL_LIST:
            return [m.strip() for m in Config.CLAUDE_MODEL_LIST.split(',') if m.strip()]
        models = self._models_from_api()
        return models or list(KNOWN_MODELS)

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
            # for short prompts too. Cache hits cost ~10% of normal input tokens
            # (2.5%/5% on the newest Fable/Mythos/Opus 5.5/Sonnet 5.5 models).
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
                'cache_creation_1h_input_tokens': getattr(
                    getattr(usage, 'cache_creation', None), 'ephemeral_1h_input_tokens', 0) or 0,
            }
        }

    def health(self) -> bool:
        # Claude hat keinen günstigen Health-Endpoint — wenn API-Key gesetzt, gehen
        # wir von erreichbar aus. Echte Erreichbarkeit zeigt sich beim ersten Call.
        return self.client is not None
