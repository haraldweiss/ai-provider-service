# News-Agent Hybrid (Claude+Ollama) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the Anthropic Platform news-agent (local-LLM ecosystem roundup with WordPress output) into `ai-provider-service` as a tool-calling-capable module with `.env`-only switch between Claude (Phase 1) and Ollama (Phase 2).

**Architecture:** Extend `BaseClient.create_message` and `dispatcher.dispatch` with an optional `tools` parameter. Add provider-agnostic tool schema (Claude-format as lingua franca), Claude-native pass-through, and Ollama-OpenAI-format mapping. New `agents/news/` module orchestrates a tool-loop over `dispatch()`; tool implementations (`web_search` via self-hosted SearXNG, `web_fetch` via trafilatura, `publish_to_wordpress` via WP REST API) live alongside the runner. systemd-timer triggers daily at 07:00.

**Tech Stack:** Python 3, Flask + SQLAlchemy (existing service), `anthropic` SDK, `requests`, `trafilatura` (new dep), pytest with `unittest.mock`, Docker (for SearXNG), systemd timer.

**Spec:** [docs/superpowers/specs/2026-05-23-news-agent-hybrid-design.md](../specs/2026-05-23-news-agent-hybrid-design.md)

---

## Task 1: Extend `BaseClient.create_message` signature with `tools` param

**Files:**
- Modify: `providers/base.py`
- Test: `tests/test_provider_base.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_provider_base.py`:

```python
"""Tests for BaseClient interface — verifies the tools parameter exists on the contract."""
from __future__ import annotations
import inspect

from providers.base import BaseClient


def test_create_message_signature_accepts_tools_param():
    sig = inspect.signature(BaseClient.create_message)
    assert 'tools' in sig.parameters, "BaseClient.create_message must accept a 'tools' kwarg"
    assert sig.parameters['tools'].default is None, "tools must default to None for backward compat"
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_provider_base.py -v
```

Expected: FAIL — `'tools' not in sig.parameters`.

- [ ] **Step 3: Update `BaseClient.create_message` signature**

Edit `providers/base.py`:

```python
"""Basis-Schnittstelle für alle Provider-Clients.

Antwort-Format ist Claude-kompatibel (für Drop-in-Migration):

  {
    "content": [{"text": "..."}],
    "stop_reason": "end_turn" | "tool_use",   # NEW: present when tools= was passed
    "tool_calls": [                            # NEW: only when stop_reason == "tool_use"
      {"id": "...", "name": "...", "input": {...}},
      ...
    ],
    "usage": {"input_tokens": N, "output_tokens": M}
  }

Backward-compat: when `tools` is None or omitted, `stop_reason`/`tool_calls` MAY be
absent (existing callers ignore them).
"""

from __future__ import annotations
from abc import ABC, abstractmethod


class BaseClient(ABC):
    timeout: int = 30

    @abstractmethod
    def get_models(self) -> list[str]:
        """Liste verfügbarer Models. Leere Liste wenn nicht erreichbar."""

    @abstractmethod
    def create_message(
        self,
        model: str,
        messages: list[dict],
        max_tokens: int = 600,
        tools: list[dict] | None = None,
    ) -> dict:
        """Sende eine Chat-Completion. Format siehe Modul-Doc."""

    @abstractmethod
    def health(self) -> bool:
        """Schneller Erreichbarkeits-Check."""
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/test_provider_base.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```
git add providers/base.py tests/test_provider_base.py
git commit -m "feat(providers): add tools param to BaseClient.create_message"
```

---

## Task 2: Claude provider — native tool-calling support

**Files:**
- Modify: `providers/claude.py`
- Test: `tests/test_provider_tools_claude.py`

- [ ] **Step 1: Write failing test for tool-use response mapping**

Create `tests/test_provider_tools_claude.py`:

```python
"""Tests for Claude provider tool-calling — verifies native pass-through and response mapping."""
from __future__ import annotations
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ['ANTHROPIC_API_KEY'] = 'sk-test-key-stub'

from providers.claude import ClaudeClient


def _fake_response_tool_use():
    """Build an anthropic SDK-style response object with a ToolUseBlock."""
    block = MagicMock()
    block.type = 'tool_use'
    block.id = 'toolu_01ABCDEFGH'
    block.name = 'web_search'
    block.input = {'query': 'Ollama 0.24 release'}
    resp = MagicMock()
    resp.content = [block]
    resp.stop_reason = 'tool_use'
    resp.usage = MagicMock(input_tokens=120, output_tokens=42,
                           cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return resp


def _fake_response_end_turn():
    block = MagicMock()
    block.type = 'text'
    block.text = 'All done.'
    resp = MagicMock()
    resp.content = [block]
    resp.stop_reason = 'end_turn'
    resp.usage = MagicMock(input_tokens=10, output_tokens=5,
                           cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return resp


def test_claude_tool_use_response_mapped_to_normalized_schema():
    client = ClaudeClient({'api_key': 'sk-test'})
    client.client = MagicMock()
    client.client.messages.create.return_value = _fake_response_tool_use()

    tools = [{'name': 'web_search',
              'description': 'search',
              'input_schema': {'type': 'object', 'properties': {'query': {'type': 'string'}}}}]

    out = client.create_message('claude-sonnet-4-6',
                                [{'role': 'user', 'content': 'find Ollama news'}],
                                max_tokens=500, tools=tools)

    assert out['stop_reason'] == 'tool_use'
    assert out['tool_calls'] == [{'id': 'toolu_01ABCDEFGH',
                                  'name': 'web_search',
                                  'input': {'query': 'Ollama 0.24 release'}}]
    kwargs = client.client.messages.create.call_args.kwargs
    assert kwargs['tools'] == tools, "tools must be passed natively to anthropic SDK"


def test_claude_end_turn_response_has_no_tool_calls():
    client = ClaudeClient({'api_key': 'sk-test'})
    client.client = MagicMock()
    client.client.messages.create.return_value = _fake_response_end_turn()

    out = client.create_message('claude-sonnet-4-6', [{'role': 'user', 'content': 'hi'}])

    assert out['stop_reason'] == 'end_turn'
    assert out['tool_calls'] == []
    assert out['content'] == [{'text': 'All done.'}]


def test_claude_backward_compat_no_tools_param():
    """Existing callers that don't pass tools still work."""
    client = ClaudeClient({'api_key': 'sk-test'})
    client.client = MagicMock()
    client.client.messages.create.return_value = _fake_response_end_turn()

    out = client.create_message('claude-sonnet-4-6', [{'role': 'user', 'content': 'hi'}])

    assert 'content' in out and 'usage' in out
    kwargs = client.client.messages.create.call_args.kwargs
    assert 'tools' not in kwargs, "tools must NOT be sent when caller did not pass any"
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_provider_tools_claude.py -v
```

Expected: FAIL — `create_message` doesn't accept `tools`, doesn't return `stop_reason`/`tool_calls`.

- [ ] **Step 3: Implement tool-calling in Claude client**

Replace the body of `providers/claude.py` `create_message` method:

```python
    def create_message(
        self,
        model: str,
        messages: list[dict],
        max_tokens: int = 600,
        tools: list[dict] | None = None,
    ) -> dict:
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

        content_out: list[dict] = []
        tool_calls: list[dict] = []
        for block in response.content or []:
            btype = getattr(block, 'type', None)
            if btype == 'text':
                content_out.append({'text': block.text})
            elif btype == 'tool_use':
                tool_calls.append({
                    'id': block.id,
                    'name': block.name,
                    'input': block.input,
                })

        return {
            'content': content_out or [{'text': ''}],
            'stop_reason': getattr(response, 'stop_reason', 'end_turn'),
            'tool_calls': tool_calls,
            'usage': {
                'input_tokens': usage.input_tokens,
                'output_tokens': usage.output_tokens,
                'cache_creation_input_tokens': getattr(usage, 'cache_creation_input_tokens', 0) or 0,
                'cache_read_input_tokens': getattr(usage, 'cache_read_input_tokens', 0) or 0,
            },
        }
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_provider_tools_claude.py -v
```

Expected: PASS (all 3 tests).

- [ ] **Step 5: Verify existing claude usage still works**

```
pytest tests/ -v -k "not tools" 2>&1 | tail -20
```

Expected: no regressions in pre-existing tests.

- [ ] **Step 6: Commit**

```
git add providers/claude.py tests/test_provider_tools_claude.py
git commit -m "feat(claude): native tool-calling support with normalized response"
```

---

## Task 3: Ollama provider — tool-calling via OpenAI-format mapping

**Files:**
- Modify: `providers/ollama.py`
- Test: `tests/test_provider_tools_ollama.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_provider_tools_ollama.py`:

```python
"""Tests for Ollama provider tool-calling — verifies tool format conversion in both directions."""
from __future__ import annotations
from unittest.mock import MagicMock, patch

import pytest

from providers.ollama import OllamaClient


def _ollama_tool_call_response():
    """Simulate an /api/chat response where Ollama returned a tool_call."""
    return {
        'message': {
            'role': 'assistant',
            'content': '',
            'tool_calls': [
                {'function': {'name': 'web_search',
                              'arguments': {'query': 'llama.cpp release'}}},
            ],
        },
        'done': True,
        'done_reason': 'stop',
        'prompt_eval_count': 200,
        'eval_count': 30,
    }


def _ollama_end_turn_response():
    return {
        'message': {'role': 'assistant', 'content': 'Done.'},
        'done': True,
        'done_reason': 'stop',
        'prompt_eval_count': 10,
        'eval_count': 5,
    }


def _fake_post_factory(payload_response):
    def _fake_post(url, json=None, timeout=None):
        # Capture the outgoing payload so the test can inspect tool format conversion.
        _fake_post.last_payload = json
        r = MagicMock()
        r.raise_for_status = MagicMock()
        r.json = MagicMock(return_value=payload_response)
        return r
    _fake_post.last_payload = None
    return _fake_post


def test_ollama_tools_mapped_to_openai_function_format():
    fake_post = _fake_post_factory(_ollama_end_turn_response())
    client = OllamaClient({'api_endpoint': 'http://127.0.0.1:11434'})

    tools = [{'name': 'web_search',
              'description': 'search',
              'input_schema': {'type': 'object',
                               'properties': {'query': {'type': 'string'}},
                               'required': ['query']}}]

    with patch('providers.ollama.requests.post', side_effect=fake_post):
        client.create_message('qwen3.6:latest',
                              [{'role': 'user', 'content': 'find news'}],
                              tools=tools)

    payload = fake_post.last_payload
    assert 'tools' in payload, "tools must be in outgoing /api/chat payload"
    assert payload['tools'] == [{
        'type': 'function',
        'function': {
            'name': 'web_search',
            'description': 'search',
            'parameters': {'type': 'object',
                           'properties': {'query': {'type': 'string'}},
                           'required': ['query']},
        }
    }]


def test_ollama_tool_call_response_mapped_to_normalized_schema():
    fake_post = _fake_post_factory(_ollama_tool_call_response())
    client = OllamaClient({'api_endpoint': 'http://127.0.0.1:11434'})

    with patch('providers.ollama.requests.post', side_effect=fake_post):
        out = client.create_message('qwen3.6:latest',
                                    [{'role': 'user', 'content': 'find news'}],
                                    tools=[{'name': 'web_search', 'input_schema': {}}])

    assert out['stop_reason'] == 'tool_use'
    assert len(out['tool_calls']) == 1
    tc = out['tool_calls'][0]
    assert tc['name'] == 'web_search'
    assert tc['input'] == {'query': 'llama.cpp release'}
    assert tc['id']  # synthesized ID must be non-empty


def test_ollama_end_turn_has_empty_tool_calls():
    fake_post = _fake_post_factory(_ollama_end_turn_response())
    client = OllamaClient({'api_endpoint': 'http://127.0.0.1:11434'})

    with patch('providers.ollama.requests.post', side_effect=fake_post):
        out = client.create_message('qwen3.6:latest',
                                    [{'role': 'user', 'content': 'hi'}])

    assert out['stop_reason'] == 'end_turn'
    assert out['tool_calls'] == []


def test_ollama_backward_compat_no_tools():
    fake_post = _fake_post_factory(_ollama_end_turn_response())
    client = OllamaClient({'api_endpoint': 'http://127.0.0.1:11434'})

    with patch('providers.ollama.requests.post', side_effect=fake_post):
        out = client.create_message('qwen3.6:latest',
                                    [{'role': 'user', 'content': 'hi'}])

    assert 'tools' not in fake_post.last_payload, "tools must NOT appear in payload when caller passed none"
    assert out['content'] == [{'text': 'Done.'}]
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_provider_tools_ollama.py -v
```

Expected: FAIL — `create_message` doesn't accept `tools`, doesn't return `stop_reason`/`tool_calls`.

- [ ] **Step 3: Implement tool-calling in Ollama client**

In `providers/ollama.py`, replace the `create_message` method signature and body. Add a helper `_map_tools_to_openai_format` and update response parsing:

```python
    @staticmethod
    def _map_tools_to_openai_format(tools: list[dict]) -> list[dict]:
        """Convert provider-agnostic (Claude-shaped) tool defs into OpenAI's
        function-calling format expected by Ollama's /api/chat."""
        return [
            {
                'type': 'function',
                'function': {
                    'name': t['name'],
                    'description': t.get('description', ''),
                    'parameters': t.get('input_schema', {'type': 'object'}),
                },
            }
            for t in tools
        ]

    def create_message(
        self,
        model: str,
        messages: list[dict],
        max_tokens: int = 600,
        tools: list[dict] | None = None,
    ) -> dict:
        # num_ctx: see existing comment above.
        try:
            char_count = sum(len(m.get('content', '') or '') for m in messages)
        except Exception:
            char_count = 0
        needed = max(8192, int(char_count / 3) + max_tokens + 1024)
        num_ctx = 1
        while num_ctx < needed:
            num_ctx *= 2

        payload: dict = {
            'model': model,
            'messages': messages,
            'stream': False,
            'options': {
                'num_predict': max_tokens,
                'num_ctx': num_ctx,
            },
        }
        if tools:
            payload['tools'] = self._map_tools_to_openai_format(tools)

        last_exc: Exception | None = None
        order = self._pick_order(model)
        for idx, url in enumerate(order):
            try:
                r = requests.post(f'{url}/api/chat', json=payload, timeout=self.timeout)
                r.raise_for_status()
                data = r.json()
                msg = data.get('message', {}) or {}
                text = msg.get('content', '') or ''
                out_tokens = data.get('eval_count', 0)
                raw_tool_calls = msg.get('tool_calls') or []

                if not raw_tool_calls and (out_tokens == 0 or not text):
                    logger.warning(
                        f'Ollama returned empty output ({url}): done_reason={data.get("done_reason")}, '
                        f'eval_count={out_tokens}, prompt_eval_count={data.get("prompt_eval_count")}, '
                        f'num_ctx={num_ctx}, model={model}'
                    )
                if len(self.endpoints) > 1 and idx > 0:
                    logger.info(f'Ollama call recovered on fallback endpoint #{idx}: {url}')

                tool_calls_out = []
                for i, tc in enumerate(raw_tool_calls):
                    fn = tc.get('function', {}) or {}
                    args = fn.get('arguments', {})
                    # Some Ollama builds return arguments as JSON-string; normalize.
                    if isinstance(args, str):
                        import json as _json
                        try:
                            args = _json.loads(args)
                        except Exception:
                            args = {'_raw': args}
                    tool_calls_out.append({
                        'id': tc.get('id') or f'ollama-tool-{i}-{int(__import__("time").time()*1000)}',
                        'name': fn.get('name', ''),
                        'input': args,
                    })

                stop_reason = 'tool_use' if tool_calls_out else 'end_turn'
                return {
                    'content': [{'text': text}],
                    'stop_reason': stop_reason,
                    'tool_calls': tool_calls_out,
                    'usage': {
                        'input_tokens': data.get('prompt_eval_count', 0),
                        'output_tokens': out_tokens,
                    },
                }
            except (requests.ConnectionError, requests.Timeout) as e:
                last_exc = e
                logger.warning(f'Ollama endpoint {url} unreachable ({type(e).__name__}); trying next')
                continue
            except requests.HTTPError as e:
                status = getattr(e.response, 'status_code', 0)
                if status == 404:
                    OllamaClient._endpoint_models.setdefault(url, set()).discard(model)
                if (500 <= status < 600 or status == 404) and len(order) > 1:
                    last_exc = e
                    logger.warning(f'Ollama endpoint {url} returned {status}; trying next')
                    continue
                raise

        raise last_exc if last_exc else RuntimeError('All Ollama endpoints failed')
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_provider_tools_ollama.py -v
```

Expected: PASS (all 4 tests).

- [ ] **Step 5: Verify existing ollama usage still works**

```
pytest tests/ -v 2>&1 | tail -30
```

Expected: pre-existing tests still green; new files green.

- [ ] **Step 6: Commit**

```
git add providers/ollama.py tests/test_provider_tools_ollama.py
git commit -m "feat(ollama): tool-calling via OpenAI-format mapping"
```

---

## Task 4: Dispatcher — pass `tools` through to providers

**Files:**
- Modify: `dispatcher.py`
- Test: `tests/test_dispatcher_tools.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_dispatcher_tools.py`:

```python
"""Tests that dispatcher.dispatch() forwards `tools` kwarg to the provider client."""
from __future__ import annotations
import os
import sys
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['ENCRYPTION_KEY'] = 'X' * 44
os.environ['SERVICE_TOKEN'] = 'test-token'

from app import create_app
from database import db


@pytest.fixture
def app():
    os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
    app = create_app()
    app.config['TESTING'] = True
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_dispatch_forwards_tools_to_provider(app):
    from dispatcher import dispatch

    fake_client = MagicMock()
    fake_client.create_message.return_value = {
        'content': [{'text': ''}],
        'stop_reason': 'tool_use',
        'tool_calls': [{'id': 't1', 'name': 'web_search', 'input': {'query': 'x'}}],
        'usage': {'input_tokens': 10, 'output_tokens': 5},
    }

    with patch('dispatcher.health_tracker.is_healthy', return_value=True), \
         patch('dispatcher.get_client', return_value=fake_client):
        out = dispatch('news-agent', 'claude', 'claude-sonnet-4-6',
                       [{'role': 'user', 'content': 'find news'}],
                       max_tokens=4096,
                       tools=[{'name': 'web_search', 'input_schema': {}}],
                       origin_app='news-agent')

    assert out['result']['stop_reason'] == 'tool_use'
    kwargs = fake_client.create_message.call_args.kwargs
    args = fake_client.create_message.call_args.args
    # tools may be passed as kwarg or positional; check kwarg path (our convention)
    assert kwargs.get('tools') is not None or (len(args) >= 4 and args[3] is not None), \
        "tools must be forwarded to the provider client"


def test_dispatch_without_tools_param_unchanged(app):
    """Backward compatibility: existing callers without tools still work."""
    from dispatcher import dispatch

    fake_client = MagicMock()
    fake_client.create_message.return_value = {
        'content': [{'text': 'hi'}],
        'usage': {'input_tokens': 5, 'output_tokens': 1},
    }

    with patch('dispatcher.health_tracker.is_healthy', return_value=True), \
         patch('dispatcher.get_client', return_value=fake_client):
        out = dispatch('user-1', 'claude', 'claude-haiku-4-5-20251001',
                       [{'role': 'user', 'content': 'hi'}])

    assert out['result']['content'] == [{'text': 'hi'}]
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_dispatcher_tools.py -v
```

Expected: FAIL — `dispatch` doesn't accept `tools`.

- [ ] **Step 3: Add `tools` param to `_execute` and `dispatch`**

In `dispatcher.py`, modify these two functions. First, `_execute`:

```python
def _execute(
    user_id: str, provider_id: str, model: str, messages: list, max_tokens: int,
    config_override: Optional[dict] = None,
    origin_app: Optional[str] = None,
    tools: Optional[list] = None,
) -> dict:
    """[existing docstring]

    `tools` (optional) wird unverändert an den Provider durchgereicht. Provider, die
    Tool-Calling nicht unterstützen, ignorieren das Argument (BaseClient-Default = None).
    """
    cfg = config_override if config_override is not None else _load_config(user_id, provider_id)
    if cfg is None:
        raise ValueError(f"Provider {provider_id} ist nicht konfiguriert für user_id={user_id}")

    client = get_client(provider_id, cfg)
    try:
        result = client.create_message(model, messages, max_tokens, tools=tools)
        health_tracker.set_status(provider_id, True)
        usage = (result or {}).get('usage') or {}
        _log_usage_event(
            user_id, provider_id, model,
            usage.get('input_tokens'), usage.get('output_tokens'),
            'success', origin_app=origin_app,
        )
        return result
    except Exception as e:
        health_tracker.set_status(provider_id, False, reason=f"{type(e).__name__}: {e}")
        _log_usage_event(
            user_id, provider_id, model, None, None,
            'error', error_message=f"{type(e).__name__}: {e}",
            origin_app=origin_app,
        )
        raise
```

Then `dispatch`, adding the `tools` kwarg and forwarding it in both `_execute` calls:

```python
def dispatch(
    user_id: str,
    provider_id: str,
    model: str,
    messages: list,
    max_tokens: int = 600,
    *,
    fallback_provider_override: Optional[str] = None,
    fallback_model_override: Optional[str] = None,
    fallback_config_override: Optional[dict] = None,
    origin_app: Optional[str] = None,
    tools: Optional[list] = None,
) -> dict:
    """[existing docstring]

    `tools` (optional) wird an Primary UND Fallback weitergereicht. Tool-Loops
    sind Runner-Verantwortung — der Dispatcher bleibt one-shot.
    """
    pc = ProviderConfig.query.filter_by(user_id=user_id, provider_id=provider_id).first()
    should_queue = pc.queue_when_unavailable if pc else False
    queue_ttl_h = pc.queue_ttl_hours if pc else 24

    if fallback_provider_override:
        fallback = fallback_provider_override
        fallback_model = fallback_model_override or model
        fallback_cfg = fallback_config_override
    else:
        fallback = pc.fallback_provider if pc else None
        fallback_model = model
        fallback_cfg = None

    primary_healthy = health_tracker.is_healthy(provider_id)

    if primary_healthy:
        try:
            result = _execute(user_id, provider_id, model, messages, max_tokens,
                              origin_app=origin_app, tools=tools)
            return {
                'result': result, 'via': provider_id, 'model': model,
                'fallback_used': False,
            }
        except Exception as e:
            logger.info(f'Primary {provider_id} failed for user={user_id}: {e}')

    if fallback:
        try:
            logger.info(f'Trying fallback {fallback} (model={fallback_model}) for user={user_id}')
            result = _execute(user_id, fallback, fallback_model, messages, max_tokens,
                              fallback_cfg, origin_app=origin_app, tools=tools)
            return {
                'result': result, 'via': fallback, 'model': fallback_model,
                'fallback_used': True, 'primary_provider': provider_id,
                'primary_model': model,
            }
        except Exception as e:
            logger.warning(f'Fallback {fallback} also failed for user={user_id}: {e}')

    if should_queue:
        q = RequestQueue(
            id=str(uuid.uuid4()),
            user_id=user_id,
            primary_provider=provider_id,
            payload=json.dumps({
                'provider': provider_id, 'model': model,
                'messages': messages, 'max_tokens': max_tokens,
            }),
            status='pending',
            expires_at=datetime.utcnow() + timedelta(hours=queue_ttl_h),
        )
        db.session.add(q)
        db.session.commit()
        return {
            'queued': True, 'queue_id': q.id,
            'primary_provider': provider_id,
            'expires_at': q.expires_at.isoformat(),
        }

    raise RuntimeError(
        f"Provider {provider_id} nicht erreichbar, kein Fallback/Queue konfiguriert"
    )
```

- [ ] **Step 4: Run new + existing dispatcher tests**

```
pytest tests/test_dispatcher_tools.py tests/test_dispatcher_fallback.py tests/test_dispatcher_logging.py -v
```

Expected: all PASS, no regressions.

- [ ] **Step 5: Commit**

```
git add dispatcher.py tests/test_dispatcher_tools.py
git commit -m "feat(dispatcher): pass tools kwarg through to provider clients"
```

---

## Task 5: News-Agent tool schemas (provider-agnostic)

**Files:**
- Create: `agents/__init__.py`
- Create: `agents/news/__init__.py`
- Create: `agents/news/tool_schemas.py`

- [ ] **Step 1: Create package init files**

`agents/__init__.py`:

```python
"""Tool-calling agents that run on top of the ai-provider-service dispatcher."""
```

`agents/news/__init__.py`:

```python
"""Daily news-roundup agent for the local-LLM ecosystem."""
```

- [ ] **Step 2: Write tool schemas**

Create `agents/news/tool_schemas.py`:

```python
"""Tool-Definitionen für den News-Agent — provider-agnostisch im Claude-Schema.

Die Schemas werden von BaseClient.create_message in das jeweilige
Provider-Format umgemappt (Claude: nativ, Ollama: OpenAI-function-format).
"""
from __future__ import annotations


TOOLS: list[dict] = [
    {
        'name': 'web_search',
        'description': (
            'Search the web via the configured SearXNG instance. '
            'Returns a list of {title, url, snippet} hits.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'query': {'type': 'string', 'description': 'Search query.'},
                'max_results': {'type': 'integer', 'default': 10,
                                'description': 'Max number of results to return (1-25).'},
            },
            'required': ['query'],
        },
    },
    {
        'name': 'web_fetch',
        'description': (
            'Fetch a URL and return the main article text (boilerplate stripped). '
            'Returns {text, title, url} or {error, url}.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'url': {'type': 'string', 'description': 'Absolute URL to fetch.'},
            },
            'required': ['url'],
        },
    },
    {
        'name': 'publish_to_wordpress',
        'description': (
            'Publish the news roundup as a WordPress post. The body must be valid HTML. '
            'Returns {post_id, url} on success or {error} on failure. Idempotent: '
            'a second call with the same title on the same day returns the existing post.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'title': {'type': 'string', 'description': 'Post headline (clean, not the first body sentence).'},
                'body_html': {'type': 'string', 'description': 'Post body as HTML.'},
                'tags': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'Tag names (from the allowlist: Ollama, llama.cpp, Open-Weight, Security). Unknown tags are dropped.',
                },
            },
            'required': ['title', 'body_html'],
        },
    },
]


TAG_ALLOWLIST = {'Ollama', 'llama.cpp', 'Open-Weight', 'Security'}
"""Tags die der Agent setzen darf. Alles andere wird gefiltert (verhindert Tag-Wildwuchs)."""

DEFAULT_CATEGORY = 'AI-News'
```

- [ ] **Step 3: Commit**

```
git add agents/
git commit -m "feat(agents): add news-agent tool schemas (provider-agnostic)"
```

---

## Task 6: News-Agent `web_search` tool (SearXNG client)

**Files:**
- Create: `agents/news/tools.py` (initial — only `web_search`)
- Test: `tests/test_news_tools_search.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_news_tools_search.py`:

```python
"""Tests for web_search tool (SearXNG-backed)."""
from __future__ import annotations
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault('SEARXNG_URL', 'http://127.0.0.1:8888')
os.environ.setdefault('SERVICE_TOKEN', 'test-token')
os.environ.setdefault('ENCRYPTION_KEY', 'X' * 44)


def _fake_searxng_response():
    return {
        'results': [
            {'title': 'Ollama v0.24 Release',
             'url': 'https://github.com/ollama/ollama/releases/tag/v0.24.0',
             'content': 'New features: Codex App, ...'},
            {'title': 'llama.cpp speculative decoding',
             'url': 'https://github.com/ggerganov/llama.cpp/pull/12345',
             'content': 'Adds spec-dec for Apple Silicon ...'},
        ],
    }


def test_web_search_returns_normalized_hits():
    from agents.news.tools import web_search

    fake = MagicMock()
    fake.raise_for_status = MagicMock()
    fake.json = MagicMock(return_value=_fake_searxng_response())

    with patch('agents.news.tools.requests.get', return_value=fake) as mock_get:
        hits = web_search(query='Ollama 0.24', max_results=5)

    assert len(hits) == 2
    assert hits[0] == {
        'title': 'Ollama v0.24 Release',
        'url': 'https://github.com/ollama/ollama/releases/tag/v0.24.0',
        'snippet': 'New features: Codex App, ...',
    }
    args, kwargs = mock_get.call_args
    assert kwargs['params']['q'] == 'Ollama 0.24'
    assert kwargs['params']['format'] == 'json'


def test_web_search_caps_max_results():
    from agents.news.tools import web_search

    big_response = {'results': [{'title': f't{i}', 'url': f'http://x/{i}', 'content': 's'} for i in range(50)]}
    fake = MagicMock()
    fake.raise_for_status = MagicMock()
    fake.json = MagicMock(return_value=big_response)

    with patch('agents.news.tools.requests.get', return_value=fake):
        hits = web_search(query='x', max_results=3)

    assert len(hits) == 3


def test_web_search_returns_error_dict_on_failure():
    from agents.news.tools import web_search

    with patch('agents.news.tools.requests.get', side_effect=ConnectionError('refused')):
        result = web_search(query='x')

    assert isinstance(result, dict), "single-error result is a dict, not a list"
    assert 'error' in result
    assert 'search backend unavailable' in result['error'].lower()
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_news_tools_search.py -v
```

Expected: FAIL — `agents.news.tools` doesn't exist yet.

- [ ] **Step 3: Implement `web_search`**

Create `agents/news/tools.py`:

```python
"""Tool-Implementierungen für den News-Agent.

Jede Tool-Funktion folgt der Konvention:
- Erfolg: gibt das fachlich relevante Resultat zurück (dict oder list[dict]).
- Fehler: gibt {'error': '<message>', ...} zurück. Wirft NICHT.

Das Modell sieht den Fehler dann als Tool-Result und kann entweder neu
versuchen, ausweichen oder im Post ergänzen. Werfen würde den ganzen
Lauf abbrechen.
"""
from __future__ import annotations
import logging
import os

import requests

logger = logging.getLogger(__name__)

_DEFAULT_SEARXNG_URL = 'http://127.0.0.1:8888'
_DEFAULT_TIMEOUT = 10
_USER_AGENT = 'ai-provider-service/news-agent (+https://wolfinisoftware.de)'


def _searxng_url() -> str:
    return os.getenv('SEARXNG_URL', _DEFAULT_SEARXNG_URL).rstrip('/')


def web_search(query: str, max_results: int = 10) -> list[dict] | dict:
    """SearXNG-Suche. Liefert Liste {title, url, snippet} oder Error-Dict."""
    max_results = max(1, min(25, int(max_results or 10)))
    try:
        r = requests.get(
            f'{_searxng_url()}/search',
            params={'q': query, 'format': 'json'},
            headers={'User-Agent': _USER_AGENT},
            timeout=_DEFAULT_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning(f'web_search failed: {type(e).__name__}: {e}')
        return {'error': f'search backend unavailable: {type(e).__name__}'}

    results = data.get('results', []) or []
    return [
        {
            'title': hit.get('title', ''),
            'url': hit.get('url', ''),
            'snippet': hit.get('content', ''),
        }
        for hit in results[:max_results]
    ]
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/test_news_tools_search.py -v
```

Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```
git add agents/news/tools.py tests/test_news_tools_search.py
git commit -m "feat(news): web_search tool via SearXNG"
```

---

## Task 7: News-Agent `web_fetch` tool (with Trafilatura)

**Files:**
- Modify: `requirements.txt` (add `trafilatura`)
- Modify: `agents/news/tools.py` (add `web_fetch`)
- Test: `tests/test_news_tools_fetch.py`

- [ ] **Step 1: Add `trafilatura` to requirements**

Read `requirements.txt`, then append `trafilatura>=1.6,<2.0` on a new line.

- [ ] **Step 2: Install the dep into the venv**

```
./venv/bin/pip install 'trafilatura>=1.6,<2.0'
```

Expected: "Successfully installed trafilatura-..."

- [ ] **Step 3: Write failing test**

Create `tests/test_news_tools_fetch.py`:

```python
"""Tests for web_fetch tool."""
from __future__ import annotations
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault('SERVICE_TOKEN', 'test-token')
os.environ.setdefault('ENCRYPTION_KEY', 'X' * 44)


def test_web_fetch_extracts_main_text():
    from agents.news.tools import web_fetch

    html = b'<html><body><nav>menu</nav><article><h1>Hi</h1><p>Body text here that is long enough to survive trafilatura extraction heuristics for sure.</p></article></body></html>'
    fake = MagicMock()
    fake.raise_for_status = MagicMock()
    fake.content = html
    fake.headers = {'Content-Type': 'text/html'}

    with patch('agents.news.tools.requests.get', return_value=fake):
        out = web_fetch(url='https://example.com/post')

    assert 'text' in out
    assert 'Body text here' in out['text']
    assert out['url'] == 'https://example.com/post'


def test_web_fetch_returns_error_on_4xx():
    from agents.news.tools import web_fetch
    import requests as r

    fake = MagicMock()
    err = r.HTTPError(response=MagicMock(status_code=404))
    fake.raise_for_status = MagicMock(side_effect=err)

    with patch('agents.news.tools.requests.get', return_value=fake):
        out = web_fetch(url='https://example.com/missing')

    assert 'error' in out
    assert out['url'] == 'https://example.com/missing'


def test_web_fetch_caps_text_length():
    from agents.news.tools import web_fetch

    big_text = 'word ' * 10000
    big_html = f'<html><body><article><p>{big_text}</p></article></body></html>'.encode()
    fake = MagicMock()
    fake.raise_for_status = MagicMock()
    fake.content = big_html
    fake.headers = {'Content-Type': 'text/html'}

    with patch('agents.news.tools.requests.get', return_value=fake):
        out = web_fetch(url='https://example.com/big')

    assert len(out['text']) <= 20_500, "text must be capped around 20k chars"
    assert out['text'].endswith('… [truncated]')
```

- [ ] **Step 4: Run tests to verify they fail**

```
pytest tests/test_news_tools_fetch.py -v
```

Expected: FAIL — `web_fetch` not implemented.

- [ ] **Step 5: Implement `web_fetch`**

Append to `agents/news/tools.py`:

```python
import trafilatura

_FETCH_TIMEOUT = 10
_MAX_TEXT_CHARS = 20_000


def web_fetch(url: str) -> dict:
    """Fetch URL, extract main article text with trafilatura, cap length.

    Returns {text, title, url} on success or {error, url} on failure.
    """
    if not url or not isinstance(url, str):
        return {'error': 'invalid url', 'url': str(url)}
    try:
        r = requests.get(
            url,
            headers={'User-Agent': _USER_AGENT, 'Accept': 'text/html,application/xhtml+xml,*/*'},
            timeout=_FETCH_TIMEOUT,
            allow_redirects=True,
        )
        r.raise_for_status()
    except Exception as e:
        logger.warning(f'web_fetch failed for {url}: {type(e).__name__}: {e}')
        return {'error': f'fetch failed: {type(e).__name__}', 'url': url}

    try:
        text = trafilatura.extract(r.content, include_comments=False,
                                   include_tables=False, no_fallback=False) or ''
    except Exception as e:
        logger.warning(f'web_fetch extract failed for {url}: {e}')
        return {'error': f'extract failed: {type(e).__name__}', 'url': url}

    title = ''
    try:
        meta = trafilatura.extract_metadata(r.content)
        if meta and meta.title:
            title = meta.title
    except Exception:
        pass

    truncated = len(text) > _MAX_TEXT_CHARS
    if truncated:
        text = text[:_MAX_TEXT_CHARS].rstrip() + '… [truncated]'
    return {'text': text, 'title': title, 'url': url}
```

- [ ] **Step 6: Run tests to verify they pass**

```
pytest tests/test_news_tools_fetch.py -v
```

Expected: PASS (3 tests).

- [ ] **Step 7: Commit**

```
git add agents/news/tools.py tests/test_news_tools_fetch.py requirements.txt
git commit -m "feat(news): web_fetch tool with trafilatura extraction"
```

---

## Task 8: News-Agent `publish_to_wordpress` tool

**Files:**
- Modify: `agents/news/tools.py`
- Test: `tests/test_news_tools_wp.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_news_tools_wp.py`:

```python
"""Tests for publish_to_wordpress — verifies REST API contract + idempotency + tag/category lookup."""
from __future__ import annotations
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault('WORDPRESS_URL', 'https://wolfinisoftware.de')
os.environ.setdefault('WORDPRESS_USER', 'news-agent')
os.environ.setdefault('WORDPRESS_APP_PASSWORD', 'test-pwd')
os.environ.setdefault('WORDPRESS_CATEGORY', 'AI-News')
os.environ.setdefault('WORDPRESS_STATUS', 'publish')
os.environ.setdefault('SERVICE_TOKEN', 'test-token')
os.environ.setdefault('ENCRYPTION_KEY', 'X' * 44)


def _resp(status_code: int, json_data):
    r = MagicMock()
    r.status_code = status_code
    r.raise_for_status = MagicMock()
    r.json = MagicMock(return_value=json_data)
    return r


def test_publish_creates_post_with_tags_and_category():
    """Happy path: no existing post → create category if missing → create new tags → POST /posts."""
    from agents.news import tools
    tools._wp_cache_reset()  # ensure tests are independent

    def fake_get(url, **kwargs):
        if 'users/me' in url:
            return _resp(200, {'id': 12, 'name': 'News Agent'})
        if '/posts' in url:
            return _resp(200, [])  # no existing post for idempotency check
        if '/categories' in url:
            return _resp(200, [{'id': 7, 'name': 'AI-News', 'slug': 'ai-news'}])
        if '/tags' in url:
            return _resp(200, [{'id': 1, 'name': 'Ollama', 'slug': 'ollama'}])
        raise AssertionError(f'unexpected GET {url}')

    posted = {}
    def fake_post(url, json=None, auth=None, **kwargs):
        if '/posts' in url:
            posted['payload'] = json
            return _resp(201, {'id': 999, 'link': 'https://wolfinisoftware.de/?p=999'})
        if '/tags' in url:
            return _resp(201, {'id': 42, 'name': json['name']})
        raise AssertionError(f'unexpected POST {url}')

    with patch('agents.news.tools.requests.get', side_effect=fake_get), \
         patch('agents.news.tools.requests.post', side_effect=fake_post):
        out = tools.publish_to_wordpress(
            title='Daily LLM Roundup',
            body_html='<p>Hi</p>',
            tags=['Ollama', 'llama.cpp', 'NOT_IN_ALLOWLIST'],
        )

    assert out == {'post_id': 999, 'url': 'https://wolfinisoftware.de/?p=999'}
    assert posted['payload']['title'] == 'Daily LLM Roundup'
    assert posted['payload']['content'] == '<p>Hi</p>'
    assert posted['payload']['status'] == 'publish'
    assert 7 in posted['payload']['categories']
    # 'NOT_IN_ALLOWLIST' must be dropped, Ollama (id=1) must be in tags, llama.cpp newly created (id=42)
    assert 1 in posted['payload']['tags']
    assert 42 in posted['payload']['tags']


def test_publish_is_idempotent_when_post_exists_today():
    from agents.news import tools
    tools._wp_cache_reset()

    existing_post = {'id': 555, 'link': 'https://wolfinisoftware.de/?p=555', 'title': {'rendered': 'Daily LLM Roundup'}}

    def fake_get(url, **kwargs):
        if 'users/me' in url:
            return _resp(200, {'id': 12})
        if '/posts' in url:
            return _resp(200, [existing_post])
        raise AssertionError(f'unexpected GET {url}')

    with patch('agents.news.tools.requests.get', side_effect=fake_get), \
         patch('agents.news.tools.requests.post') as mock_post:
        out = tools.publish_to_wordpress(title='Daily LLM Roundup', body_html='<p>x</p>')

    assert out == {'post_id': 555, 'url': 'https://wolfinisoftware.de/?p=555'}
    mock_post.assert_not_called()


def test_publish_returns_error_on_4xx():
    from agents.news import tools
    import requests as r
    tools._wp_cache_reset()

    def fake_get(url, **kwargs):
        if 'users/me' in url:
            return _resp(200, {'id': 12})
        if '/posts' in url:
            return _resp(200, [])
        if '/categories' in url:
            return _resp(200, [{'id': 7, 'name': 'AI-News'}])
        raise AssertionError(f'unexpected GET {url}')

    err = r.HTTPError(response=MagicMock(status_code=400, text='bad request'))
    def fake_post(url, **kwargs):
        m = MagicMock()
        m.raise_for_status = MagicMock(side_effect=err)
        return m

    with patch('agents.news.tools.requests.get', side_effect=fake_get), \
         patch('agents.news.tools.requests.post', side_effect=fake_post):
        out = tools.publish_to_wordpress(title='X', body_html='<p>x</p>')

    assert 'error' in out


def test_dry_run_does_not_post():
    from agents.news import tools
    tools._wp_cache_reset()

    with patch('agents.news.tools.requests.get') as mock_get, \
         patch('agents.news.tools.requests.post') as mock_post:
        out = tools.publish_to_wordpress(title='X', body_html='<p>x</p>',
                                         tags=['Ollama'], dry_run=True)

    assert out['dry_run'] is True
    assert out['title'] == 'X'
    mock_get.assert_not_called()
    mock_post.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_news_tools_wp.py -v
```

Expected: FAIL — `publish_to_wordpress` not implemented.

- [ ] **Step 3: Implement `publish_to_wordpress`**

Append to `agents/news/tools.py`:

```python
from datetime import datetime, timezone
from typing import Any

from agents.news.tool_schemas import TAG_ALLOWLIST, DEFAULT_CATEGORY


_wp_cache: dict[str, Any] = {'self_id': None, 'category_id': None, 'tag_ids': {}}


def _wp_cache_reset() -> None:
    """For tests only — clears the in-process WP lookup cache."""
    _wp_cache['self_id'] = None
    _wp_cache['category_id'] = None
    _wp_cache['tag_ids'] = {}


def _wp_url() -> str:
    return os.getenv('WORDPRESS_URL', '').rstrip('/')


def _wp_auth() -> tuple[str, str]:
    return (os.getenv('WORDPRESS_USER', ''), os.getenv('WORDPRESS_APP_PASSWORD', ''))


def _wp_status() -> str:
    return os.getenv('WORDPRESS_STATUS', 'publish')


def _wp_category_name() -> str:
    return os.getenv('WORDPRESS_CATEGORY', DEFAULT_CATEGORY)


def _wp_get_self_id() -> int:
    if _wp_cache['self_id'] is not None:
        return _wp_cache['self_id']
    r = requests.get(f'{_wp_url()}/wp-json/wp/v2/users/me',
                     auth=_wp_auth(), timeout=_FETCH_TIMEOUT)
    r.raise_for_status()
    uid = int(r.json()['id'])
    _wp_cache['self_id'] = uid
    return uid


def _wp_get_or_create_category(name: str) -> int:
    if _wp_cache['category_id'] is not None:
        return _wp_cache['category_id']
    r = requests.get(f'{_wp_url()}/wp-json/wp/v2/categories',
                     params={'search': name, 'per_page': 100},
                     auth=_wp_auth(), timeout=_FETCH_TIMEOUT)
    r.raise_for_status()
    for cat in r.json():
        if cat.get('name', '').lower() == name.lower():
            _wp_cache['category_id'] = cat['id']
            return cat['id']
    r2 = requests.post(f'{_wp_url()}/wp-json/wp/v2/categories',
                       json={'name': name},
                       auth=_wp_auth(), timeout=_FETCH_TIMEOUT)
    r2.raise_for_status()
    cid = int(r2.json()['id'])
    _wp_cache['category_id'] = cid
    return cid


def _wp_get_or_create_tag(name: str) -> int:
    if name in _wp_cache['tag_ids']:
        return _wp_cache['tag_ids'][name]
    r = requests.get(f'{_wp_url()}/wp-json/wp/v2/tags',
                     params={'search': name, 'per_page': 100},
                     auth=_wp_auth(), timeout=_FETCH_TIMEOUT)
    r.raise_for_status()
    for tag in r.json():
        if tag.get('name', '').lower() == name.lower():
            _wp_cache['tag_ids'][name] = tag['id']
            return tag['id']
    r2 = requests.post(f'{_wp_url()}/wp-json/wp/v2/tags',
                       json={'name': name},
                       auth=_wp_auth(), timeout=_FETCH_TIMEOUT)
    r2.raise_for_status()
    tid = int(r2.json()['id'])
    _wp_cache['tag_ids'][name] = tid
    return tid


def _wp_find_today_post(title: str, self_id: int) -> dict | None:
    today_utc = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    r = requests.get(
        f'{_wp_url()}/wp-json/wp/v2/posts',
        params={
            'author': self_id,
            'after': today_utc.isoformat(),
            'search': title,
            'status': 'publish,draft,pending,future',
            'per_page': 10,
        },
        auth=_wp_auth(), timeout=_FETCH_TIMEOUT,
    )
    r.raise_for_status()
    posts = r.json()
    for p in posts:
        existing_title = p.get('title', {}).get('rendered', '') if isinstance(p.get('title'), dict) else str(p.get('title', ''))
        if existing_title.strip().lower() == title.strip().lower():
            return p
    return None


def publish_to_wordpress(title: str, body_html: str, tags: list[str] | None = None,
                        dry_run: bool = False) -> dict:
    """Publish/upsert a WordPress post. Idempotent per (author, title, day).

    Tags must be in TAG_ALLOWLIST — unknown tags are silently dropped.
    Returns {post_id, url} or {error}.
    """
    if dry_run:
        return {
            'dry_run': True,
            'title': title,
            'tags': [t for t in (tags or []) if t in TAG_ALLOWLIST],
            'body_html_len': len(body_html or ''),
        }
    if not title or not body_html:
        return {'error': 'title and body_html are required'}

    try:
        self_id = _wp_get_self_id()
        existing = _wp_find_today_post(title, self_id)
        if existing:
            return {'post_id': existing['id'], 'url': existing.get('link', '')}

        category_id = _wp_get_or_create_category(_wp_category_name())
        filtered_tags = [t for t in (tags or []) if t in TAG_ALLOWLIST]
        tag_ids = [_wp_get_or_create_tag(t) for t in filtered_tags]

        payload = {
            'title': title,
            'content': body_html,
            'status': _wp_status(),
            'categories': [category_id],
            'tags': tag_ids,
        }
        r = requests.post(f'{_wp_url()}/wp-json/wp/v2/posts',
                          json=payload, auth=_wp_auth(), timeout=_FETCH_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        return {'post_id': int(data['id']), 'url': data.get('link', '')}
    except Exception as e:
        logger.warning(f'publish_to_wordpress failed: {type(e).__name__}: {e}')
        return {'error': f'{type(e).__name__}: {e}'}
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_news_tools_wp.py -v
```

Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```
git add agents/news/tools.py tests/test_news_tools_wp.py
git commit -m "feat(news): publish_to_wordpress with idempotency and tag allowlist"
```

---

## Task 9: News-Agent system prompt module

**Files:**
- Create: `agents/news/prompts.py`

- [ ] **Step 1: Write the prompt module**

Create `agents/news/prompts.py`:

```python
"""System-Prompt für den News-Agent.

Quelle: ursprünglicher Anthropic-Platform-Agent (agent_013EWBvafL8FSkeo6tNKnAgS),
ergänzt um deutschen Output-Hinweis.
"""
from __future__ import annotations


NEWS_SYSTEM_PROMPT = """You are a news tracking agent for the **local-LLM ecosystem** — primarily Ollama and llama.cpp, plus the tools built around them (llamafile, KoboldCpp, Jan, LM Studio, ramalama, llama-swap, Open WebUI). Your job is to search the web for recent news, releases, GitHub activity, blog posts, and community updates across this ecosystem. When asked, fetch and summarize recent developments: new model support, version releases, feature announcements, tutorials, benchmarks, and community discussions. Organize findings clearly by date and source. Always cite URLs. Flag breaking changes or major releases prominently. Be concise and factual — skip speculation and stick to verifiable information.

**Treat Ollama and llama.cpp as first-class, equal coverage.** When one ships a notable feature or hits a notable bug, briefly mention how the other handles the same area — concrete and short, no manufactured rivalry. Examples:
- "Ollama 0.24.0 ships the Codex App with integrated browser; llama.cpp continues to expose a plain OpenAI-compatible HTTP server and recommends external UIs like Open WebUI."
- "GGML assertion bug X affects Ollama users on quant Y; llama.cpp shipped the equivalent fix in release Z."
- "llama.cpp adds speculative decoding for Apple Silicon; Ollama doesn't expose this knob yet."
Don't insist on a comparison for every item — only when the difference is interesting or actionable for a reader picking between the two.

**Open-weight model coverage**: include new model releases (LLaMA, Qwen, Mistral, Gemma, Kimi, DeepSeek, Phi, SmolLM, Granite, Command, …) when they bring something new to local inference: new sizes, new quantizations (GGUF, EXL2, AWQ), new architectures (MoE, multimodal, long-context), notable benchmark wins. Mention whether they are already available on Ollama's library / Hugging Face GGUF mirrors.

**Skip pure cloud-LLM news** (GPT-X, Claude version bumps, Gemini features, Anthropic blog posts) unless it directly affects local users — for example: a chat-template that lands in llama.cpp, an open release of a model previously cloud-only, or a tooling integration relevant to self-hosted setups.

When covering security topics (CVEs, vulnerabilities, advisories): always state the **affected version range** (e.g. "Ollama < 0.17.1") and the **affected platform(s)** (e.g. "Windows only", "all platforms") in the same paragraph as the CVE number or severity. Never lead with CVSS scores or scary names alone — the reader must be able to tell from a single skim whether they need to act. If the CVE only affects an older release line, say so explicitly ("fixed in 0.17.1, not relevant for current 0.24.x users"). If platform-specific (e.g. Windows-only), say so explicitly. Verify each CVE on NVD or the project's GitHub Security Advisories before including it — do not paraphrase CVE details from secondary sources alone.

**Layout suggestion** (use as a guide, not a rigid template — drop sections that have nothing newsworthy that day):
- 🚀 **Releases** (Ollama, llama.cpp, supporting tools — version, date, one-line headline)
- 🆕 **Open-Weight-Modelle** (new models worth pulling locally; size, license, what's notable)
- 🔴 **Sicherheit** (CVEs with the affected-version/platform rule above)
- 🔀 **Ökosystem** (Jan, llamafile, KoboldCpp, Open WebUI, ramalama, llama-swap — feature parity, integrations)
- 🧠 **Performance / Engineering** (benchmarks, new quants, MoE/multimodal/long-context work)
- 🆚 **Ollama vs llama.cpp** (optional section — only when there is a concrete current difference worth surfacing)

When you have completed your roundup, call the publish_to_wordpress tool with a clean headline title (not the first sentence of the body) and HTML body. ALWAYS call the tool — do not just output the text.

**Output language:** Schreibe den finalen WordPress-Post auf Deutsch. Section-Header sind bereits deutsch (🚀 Releases, 🔴 Sicherheit, etc.). Suchanfragen darfst du auf Englisch formulieren — die zugrundeliegenden Quellen sind überwiegend englisch."""
```

- [ ] **Step 2: Commit**

```
git add agents/news/prompts.py
git commit -m "feat(news): system prompt module (original + German output hint)"
```

---

## Task 10: News-Agent runner — tool-loop orchestrator

**Files:**
- Create: `agents/news/runner.py`
- Test: `tests/test_news_runner.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_news_runner.py`:

```python
"""Tests for the news-agent runner — verifies tool-loop convergence and termination."""
from __future__ import annotations
import os
from unittest.mock import patch, MagicMock

import pytest

os.environ.setdefault('SERVICE_TOKEN', 'test-token')
os.environ.setdefault('ENCRYPTION_KEY', 'X' * 44)
os.environ.setdefault('NEWS_AGENT_PROVIDER', 'claude')
os.environ.setdefault('NEWS_AGENT_MODEL_CLAUDE', 'claude-sonnet-4-6')
os.environ.setdefault('NEWS_AGENT_MODEL_OLLAMA', 'qwen3.6:latest')
os.environ.setdefault('NEWS_AGENT_MAX_ITERATIONS', '10')


@pytest.fixture
def app():
    from app import create_app
    from database import db
    os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
    app = create_app()
    app.config['TESTING'] = True
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_runner_loop_converges_with_one_tool_call(app):
    """LLM does one web_search, then ends with publish_to_wordpress, then end_turn."""
    from agents.news import runner

    responses = [
        {'result': {'content': [{'text': ''}], 'stop_reason': 'tool_use',
                    'tool_calls': [{'id': 't1', 'name': 'web_search', 'input': {'query': 'x'}}],
                    'usage': {'input_tokens': 1, 'output_tokens': 1}},
         'via': 'claude', 'model': 'claude-sonnet-4-6', 'fallback_used': False},
        {'result': {'content': [{'text': ''}], 'stop_reason': 'tool_use',
                    'tool_calls': [{'id': 't2', 'name': 'publish_to_wordpress',
                                    'input': {'title': 'T', 'body_html': '<p>x</p>'}}],
                    'usage': {'input_tokens': 1, 'output_tokens': 1}},
         'via': 'claude', 'model': 'claude-sonnet-4-6', 'fallback_used': False},
        {'result': {'content': [{'text': 'Done.'}], 'stop_reason': 'end_turn',
                    'tool_calls': [],
                    'usage': {'input_tokens': 1, 'output_tokens': 1}},
         'via': 'claude', 'model': 'claude-sonnet-4-6', 'fallback_used': False},
    ]
    dispatch_mock = MagicMock(side_effect=responses)
    exec_mock = MagicMock(side_effect=[
        [{'title': 'h', 'url': 'http://x', 'snippet': 's'}],   # web_search result
        {'post_id': 42, 'url': 'http://x/42'},                  # publish result
    ])

    with patch('agents.news.runner.dispatch', dispatch_mock), \
         patch('agents.news.runner.execute_tool', exec_mock):
        summary = runner.run_news_agent()

    assert summary['iterations'] == 2
    assert summary['final_stop_reason'] == 'end_turn'
    assert dispatch_mock.call_count == 3
    assert exec_mock.call_count == 2
    assert exec_mock.call_args_list[0].args[0] == 'web_search'
    assert exec_mock.call_args_list[1].args[0] == 'publish_to_wordpress'


def test_runner_raises_on_divergence(app):
    """Loop must terminate via MAX_ITERATIONS if model keeps calling tools."""
    from agents.news import runner

    # Always return tool_use → never ends
    response = {'result': {'content': [{'text': ''}], 'stop_reason': 'tool_use',
                           'tool_calls': [{'id': 't', 'name': 'web_search', 'input': {'query': 'x'}}],
                           'usage': {'input_tokens': 1, 'output_tokens': 1}},
                'via': 'claude', 'model': 'm', 'fallback_used': False}

    with patch('agents.news.runner.dispatch', return_value=response), \
         patch('agents.news.runner.execute_tool', return_value=[]), \
         patch.dict(os.environ, {'NEWS_AGENT_MAX_ITERATIONS': '3'}):
        with pytest.raises(RuntimeError, match='did not converge'):
            runner.run_news_agent()


def test_runner_passes_fallback_when_configured(app):
    """When NEWS_AGENT_FALLBACK is set, dispatch must receive fallback_provider_override."""
    from agents.news import runner

    response = {'result': {'content': [{'text': 'Done'}], 'stop_reason': 'end_turn',
                           'tool_calls': [],
                           'usage': {'input_tokens': 1, 'output_tokens': 1}},
                'via': 'claude', 'model': 'm', 'fallback_used': False}
    dispatch_mock = MagicMock(return_value=response)

    with patch('agents.news.runner.dispatch', dispatch_mock), \
         patch.dict(os.environ, {'NEWS_AGENT_FALLBACK': 'ollama'}):
        runner.run_news_agent()

    assert dispatch_mock.call_args.kwargs['fallback_provider_override'] == 'ollama'
    assert dispatch_mock.call_args.kwargs['fallback_model_override'] == 'qwen3.6:latest'
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_news_runner.py -v
```

Expected: FAIL — `agents.news.runner` doesn't exist.

- [ ] **Step 3: Implement the runner**

Create `agents/news/runner.py`:

```python
"""News-Agent Runner — orchestriert den Tool-Loop über dispatcher.dispatch().

Verantwortlichkeit: Dispatcher bleibt one-shot pro Iteration. Der Runner führt
die Tool-Calls aus und reicht die Resultate als nächste User-Message zurück.
"""
from __future__ import annotations
import argparse
import json
import logging
import os
import sys
import time
from typing import Any

logger = logging.getLogger(__name__)

# Lazy imports happen inside run_news_agent so that test mocks can patch
# 'agents.news.runner.dispatch' / 'agents.news.runner.execute_tool' before
# the actual symbols would be resolved.
from dispatcher import dispatch
from agents.news.tool_schemas import TOOLS
from agents.news.prompts import NEWS_SYSTEM_PROMPT
from agents.news import tools as news_tools


_TOOL_FUNCTIONS = {
    'web_search': news_tools.web_search,
    'web_fetch': news_tools.web_fetch,
    'publish_to_wordpress': news_tools.publish_to_wordpress,
}


def execute_tool(name: str, payload: dict, dry_run: bool = False) -> Any:
    """Dispatch a single tool call. Unknown tools return an error dict (never raise)."""
    fn = _TOOL_FUNCTIONS.get(name)
    if fn is None:
        return {'error': f'unknown tool: {name}'}
    try:
        if name == 'publish_to_wordpress':
            return fn(dry_run=dry_run, **(payload or {}))
        return fn(**(payload or {}))
    except TypeError as e:
        return {'error': f'invalid tool input: {e}'}
    except Exception as e:
        logger.exception(f'tool {name} raised unexpectedly')
        return {'error': f'tool failed: {type(e).__name__}: {e}'}


def _max_iterations() -> int:
    return int(os.getenv('NEWS_AGENT_MAX_ITERATIONS', '40'))


def _model_for(provider: str) -> str:
    if provider == 'claude':
        return os.getenv('NEWS_AGENT_MODEL_CLAUDE', 'claude-sonnet-4-6')
    if provider == 'ollama':
        return os.getenv('NEWS_AGENT_MODEL_OLLAMA', 'qwen3.6:latest')
    raise ValueError(f'unsupported provider: {provider}')


def run_news_agent(dry_run: bool = False) -> dict:
    """Run one full news-roundup. Returns summary dict, raises RuntimeError on divergence."""
    primary = os.getenv('NEWS_AGENT_PROVIDER', 'claude')
    fallback = os.getenv('NEWS_AGENT_FALLBACK', '').strip() or None
    primary_model = _model_for(primary)
    fallback_model = _model_for(fallback) if fallback else None
    max_iter = _max_iterations()

    user_kickoff = ('Erstelle den heutigen News-Roundup für das Local-LLM-Ökosystem '
                    '(Ollama, llama.cpp, supporting tools). Halte dich an die Layout-'
                    'Vorgaben im System-Prompt und schließe mit publish_to_wordpress ab.')

    messages: list[dict] = [
        {'role': 'system', 'content': NEWS_SYSTEM_PROMPT},
        {'role': 'user', 'content': user_kickoff},
    ]

    start = time.monotonic()
    tool_counts: dict[str, int] = {}
    final_post: dict | None = None

    for iteration in range(max_iter):
        result_envelope = dispatch(
            user_id='news-agent',
            provider_id=primary,
            model=primary_model,
            messages=messages,
            max_tokens=4096,
            tools=TOOLS,
            fallback_provider_override=fallback,
            fallback_model_override=fallback_model,
            origin_app='news-agent',
        )
        msg = result_envelope['result']
        stop_reason = msg.get('stop_reason', 'end_turn')

        # Append the assistant turn to the conversation.
        # We round-trip via JSON to keep the structure portable across providers.
        assistant_blocks: list[dict] = []
        for c in msg.get('content', []):
            assistant_blocks.append({'type': 'text', 'text': c.get('text', '')})
        for tc in msg.get('tool_calls', []) or []:
            assistant_blocks.append({'type': 'tool_use',
                                     'id': tc['id'],
                                     'name': tc['name'],
                                     'input': tc.get('input', {})})
        messages.append({'role': 'assistant', 'content': assistant_blocks})

        if stop_reason != 'tool_use':
            duration = time.monotonic() - start
            logger.info(f'news-agent run complete: iterations={iteration} '
                        f'tool_counts={tool_counts} duration={duration:.1f}s '
                        f'via={result_envelope.get("via")} '
                        f'fallback_used={result_envelope.get("fallback_used")}')
            return {
                'iterations': iteration,
                'final_stop_reason': stop_reason,
                'tool_counts': tool_counts,
                'duration_seconds': duration,
                'final_post': final_post,
                'via': result_envelope.get('via'),
                'fallback_used': result_envelope.get('fallback_used'),
            }

        tool_results = []
        for call in msg.get('tool_calls', []) or []:
            tool_counts[call['name']] = tool_counts.get(call['name'], 0) + 1
            tr = execute_tool(call['name'], call.get('input', {}), dry_run=dry_run)
            if call['name'] == 'publish_to_wordpress' and isinstance(tr, dict):
                if tr.get('post_id') or tr.get('dry_run'):
                    final_post = tr
            tool_results.append({
                'type': 'tool_result',
                'tool_use_id': call['id'],
                'content': json.dumps(tr, ensure_ascii=False)[:50_000],
            })
        messages.append({'role': 'user', 'content': tool_results})

    raise RuntimeError(f'Tool-Loop did not converge after {max_iter} iterations')


def main() -> int:
    parser = argparse.ArgumentParser(description='Run the news-agent once.')
    parser.add_argument('--dry-run', action='store_true',
                        help='Skip actual WordPress publish; print payload only.')
    args = parser.parse_args()

    logging.basicConfig(
        level=os.getenv('LOG_LEVEL', 'INFO'),
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )

    # We need a Flask app context for the UsageEvent DB writes inside dispatch().
    from app import create_app
    app = create_app()
    with app.app_context():
        try:
            summary = run_news_agent(dry_run=args.dry_run)
        except Exception as e:
            logger.exception('news-agent run failed')
            print(f'FAIL: {type(e).__name__}: {e}', file=sys.stderr)
            return 1

    print(json.dumps(summary, default=str, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
```

- [ ] **Step 4: Run runner tests**

```
pytest tests/test_news_runner.py -v
```

Expected: PASS (3 tests).

- [ ] **Step 5: Run full test suite to verify nothing broke**

```
pytest tests/ -v
```

Expected: all green.

- [ ] **Step 6: Commit**

```
git add agents/news/runner.py tests/test_news_runner.py
git commit -m "feat(news): runner orchestrates tool-loop over dispatch()"
```

---

## Task 11: Config additions — `.env` variables for the news-agent

**Files:**
- Modify: `config.py`
- Modify: `.env.example`

- [ ] **Step 1: Extend `Config` class**

Edit `config.py`, add new attributes after the existing ones:

```python
    # ---- News-Agent (Phase 1: Claude primary, Ollama fallback) ----
    NEWS_AGENT_PROVIDER = os.getenv('NEWS_AGENT_PROVIDER', 'claude')
    NEWS_AGENT_FALLBACK = os.getenv('NEWS_AGENT_FALLBACK', 'ollama')
    NEWS_AGENT_MODEL_CLAUDE = os.getenv('NEWS_AGENT_MODEL_CLAUDE', 'claude-sonnet-4-6')
    NEWS_AGENT_MODEL_OLLAMA = os.getenv('NEWS_AGENT_MODEL_OLLAMA', 'qwen3.6:latest')
    NEWS_AGENT_MAX_ITERATIONS = int(os.getenv('NEWS_AGENT_MAX_ITERATIONS', '40'))

    # ---- SearXNG (self-hosted, lokal über docker-compose) ----
    SEARXNG_URL = os.getenv('SEARXNG_URL', 'http://127.0.0.1:8888')

    # ---- WordPress (publish target für News-Agent) ----
    WORDPRESS_URL = os.getenv('WORDPRESS_URL', '')
    WORDPRESS_USER = os.getenv('WORDPRESS_USER', '')
    WORDPRESS_APP_PASSWORD = os.getenv('WORDPRESS_APP_PASSWORD', '')
    WORDPRESS_CATEGORY = os.getenv('WORDPRESS_CATEGORY', 'AI-News')
    WORDPRESS_STATUS = os.getenv('WORDPRESS_STATUS', 'publish')
```

- [ ] **Step 2: Extend `.env.example`**

Append to `.env.example`:

```
# --- News-Agent ---
# Provider-Switch: claude (Hybrid Phase 1) | ollama (Voll-Migration Phase 2)
NEWS_AGENT_PROVIDER=claude
NEWS_AGENT_FALLBACK=ollama
NEWS_AGENT_MODEL_CLAUDE=claude-sonnet-4-6
NEWS_AGENT_MODEL_OLLAMA=qwen3.6:latest
NEWS_AGENT_MAX_ITERATIONS=40

# SearXNG (self-hosted, läuft auf 127.0.0.1:8888 via docker-compose unter /opt/searxng)
SEARXNG_URL=http://127.0.0.1:8888

# WordPress (Publish-Ziel für news-agent)
WORDPRESS_URL=https://wolfinisoftware.de
WORDPRESS_USER=news-agent
WORDPRESS_APP_PASSWORD=
WORDPRESS_CATEGORY=AI-News
WORDPRESS_STATUS=publish
```

- [ ] **Step 3: Add real values to `.env`** (production deployment, not committed)

Add to `/Users/haraldweiss/projects/ai-provider-service/.env`:

```
NEWS_AGENT_PROVIDER=claude
NEWS_AGENT_FALLBACK=ollama
NEWS_AGENT_MODEL_CLAUDE=claude-sonnet-4-6
NEWS_AGENT_MODEL_OLLAMA=qwen3.6:latest
NEWS_AGENT_MAX_ITERATIONS=40
SEARXNG_URL=http://127.0.0.1:8888
WORDPRESS_URL=https://wolfinisoftware.de
WORDPRESS_USER=news-agent
WORDPRESS_APP_PASSWORD=HCdlq79FBMJH9gtib745g3K5
WORDPRESS_CATEGORY=AI-News
WORDPRESS_STATUS=publish
```

(`.env` is gitignored — do NOT `git add` it.)

- [ ] **Step 4: Run tests to verify config still loads**

```
pytest tests/ -v 2>&1 | tail -5
```

Expected: all green, no import-time errors from `config.py`.

- [ ] **Step 5: Commit (.env.example + config.py only — NOT .env)**

```
git add config.py .env.example
git commit -m "feat(config): add news-agent / SearXNG / WordPress env vars"
```

---

## Task 12: SearXNG deployment files (docker-compose + settings)

**Files:**
- Create: `deploy/searxng/docker-compose.yml`
- Create: `deploy/searxng/settings.yml.example` (placeholder secret, committed)
- Create: `deploy/searxng/README.md`

**Important:** the live `settings.yml` (with the real secret key) lives **only on the VPS**, never in git. The repo ships only `settings.yml.example` with a placeholder.

- [ ] **Step 1: Write `docker-compose.yml`**

Create `deploy/searxng/docker-compose.yml`:

```yaml
services:
  searxng:
    image: docker.io/searxng/searxng:latest
    container_name: searxng
    restart: unless-stopped
    ports:
      - "127.0.0.1:8888:8080"
    volumes:
      - ./settings.yml:/etc/searxng/settings.yml:ro
    environment:
      - SEARXNG_BASE_URL=http://127.0.0.1:8888/
      - INSTANCE_NAME=ai-provider-news
    cap_drop: [ALL]
    cap_add: [CHOWN, SETGID, SETUID]
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

- [ ] **Step 2: Write `settings.yml.example`** (this is the committed file)

Create `deploy/searxng/settings.yml.example`:

```yaml
# Copy to settings.yml and replace REPLACE_ME_SECRET_HEX_64 with a fresh
# 64-hex-char string before starting the container.
# Generate one with:  openssl rand -hex 32

use_default_settings: true

general:
  instance_name: "ai-provider-news"

search:
  safe_search: 0
  autocomplete: ""
  default_lang: ""
  formats:
    - html
    - json

server:
  secret_key: "REPLACE_ME_SECRET_HEX_64"
  limiter: false
  image_proxy: false
  http_protocol_version: "1.1"

ui:
  static_use_hash: true

engines:
  - name: google
    disabled: false
  - name: duckduckgo
    disabled: false
  - name: bing
    disabled: false
  - name: github
    disabled: false
  - name: reddit
    disabled: false
  - name: hackernews
    disabled: false
  - name: youtube
    disabled: true
  - name: bing images
    disabled: true
  - name: google images
    disabled: true
```

- [ ] **Step 3: Ensure `settings.yml` is gitignored**

Append to `.gitignore`:

```
deploy/searxng/settings.yml
```

- [ ] **Step 4: Write a setup README**

Create `deploy/searxng/README.md`:

````markdown
# SearXNG (news-agent search backend)

Self-hosted meta-search engine used by `agents/news/tools.py:web_search`.

## Deployment (VPS)

```
ssh ionos-vps
sudo mkdir -p /opt/searxng
sudo cp /opt/ai-provider-service/deploy/searxng/docker-compose.yml /opt/searxng/
sudo cp /opt/ai-provider-service/deploy/searxng/settings.yml.example /opt/searxng/settings.yml
sudo sed -i "s/REPLACE_ME_SECRET_HEX_64/$(openssl rand -hex 32)/" /opt/searxng/settings.yml
cd /opt/searxng
sudo docker compose up -d
```

Verify:

```
curl -s 'http://127.0.0.1:8888/search?q=ollama&format=json' | jq '.results | length'
```

Should return a number ≥ 1.

## Notes

- Only bound to `127.0.0.1:8888` — no public exposure, no Apache vhost.
- `server.limiter: false` because we are the only client.
- `settings.yml` itself is **gitignored** — only `settings.yml.example` is committed.
- Rotating the secret: `sudo sed -i "s/^  secret_key:.*/  secret_key: \"$(openssl rand -hex 32)\"/" /opt/searxng/settings.yml && sudo docker compose restart`
- Logs: `sudo docker logs searxng -f`.
````

- [ ] **Step 5: Commit**

```
git add deploy/searxng/docker-compose.yml deploy/searxng/settings.yml.example deploy/searxng/README.md .gitignore
git commit -m "feat(deploy): SearXNG docker-compose for news-agent search backend"
```

- [ ] **Step 6: Deploy SearXNG on the VPS**

Run the README's deployment block on the VPS (the `sed` substitutes a freshly generated secret in-place, so the secret never enters git):

```
ssh ionos-vps 'sudo mkdir -p /opt/searxng && \
               sudo cp /opt/ai-provider-service/deploy/searxng/docker-compose.yml /opt/searxng/ && \
               sudo cp /opt/ai-provider-service/deploy/searxng/settings.yml.example /opt/searxng/settings.yml && \
               sudo sed -i "s/REPLACE_ME_SECRET_HEX_64/$(openssl rand -hex 32)/" /opt/searxng/settings.yml && \
               cd /opt/searxng && sudo docker compose up -d'
```

Expected: container starts, `sudo docker ps` shows `searxng` running.

- [ ] **Step 7: Verify SearXNG is reachable**

```
ssh ionos-vps 'curl -s "http://127.0.0.1:8888/search?q=ollama&format=json" | head -c 500'
```

Expected: JSON with a `results` array.

- [ ] **Step 8: Verify the secret really isn't in git**

```
git grep -E "secret_key:.*[0-9a-f]{32}" || echo "OK: no real secret committed"
```

Expected: `OK: no real secret committed` (only the placeholder remains).

---

## Task 13: systemd-units for daily run

**Files:**
- Create: `deploy/systemd/news-agent.service`
- Create: `deploy/systemd/news-agent.timer`
- Create: `deploy/systemd/README.md`

- [ ] **Step 1: Write the service unit**

Create `deploy/systemd/news-agent.service`:

```ini
[Unit]
Description=AI News Agent (one-shot daily run)
After=network-online.target
Requires=ai-provider-service.service

[Service]
Type=oneshot
User=ai-provider
Group=ai-provider
WorkingDirectory=/opt/ai-provider-service
EnvironmentFile=/opt/ai-provider-service/.env
ExecStart=/opt/ai-provider-service/venv/bin/python -m agents.news.runner
StandardOutput=journal
StandardError=journal
TimeoutStartSec=1800
```

- [ ] **Step 2: Write the timer unit**

Create `deploy/systemd/news-agent.timer`:

```ini
[Unit]
Description=AI News Agent daily run

[Timer]
OnCalendar=*-*-* 07:00:00 Europe/Berlin
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Write deploy README**

Create `deploy/systemd/README.md`:

```markdown
# News-Agent systemd units

## Install (on VPS)

```
sudo cp deploy/systemd/news-agent.service /etc/systemd/system/
sudo cp deploy/systemd/news-agent.timer /etc/systemd/system/
sudo mkdir -p /var/log/news-agent
sudo chown ai-provider:ai-provider /var/log/news-agent
sudo systemctl daemon-reload
sudo systemctl enable --now news-agent.timer
```

## Inspect

- Next run:    `systemctl list-timers news-agent.timer`
- Last run:    `journalctl -u news-agent.service -n 200`
- Manual run:  `sudo systemctl start news-agent.service`
- Dry-run:     `sudo -u ai-provider /opt/ai-provider-service/venv/bin/python -m agents.news.runner --dry-run`

## Disable

```
sudo systemctl disable --now news-agent.timer
```
```

- [ ] **Step 4: Commit**

```
git add deploy/systemd/
git commit -m "feat(deploy): systemd units for daily news-agent run"
```

---

## Task 14: Dry-run end-to-end smoke test

**Files:** none — manual verification.

- [ ] **Step 1: Run the dry-run locally**

```
cd /Users/haraldweiss/projects/ai-provider-service
./venv/bin/python -m agents.news.runner --dry-run 2>&1 | tee /tmp/news-agent-dryrun.log | tail -80
```

Expected:
- The runner connects to Claude (since `NEWS_AGENT_PROVIDER=claude`)
- Multiple iterations of tool calls visible in logs (`web_search`, `web_fetch`)
- Final iteration calls `publish_to_wordpress` which returns `{dry_run: true, title: "...", ...}`
- Summary JSON printed at end with `final_stop_reason: "end_turn"`, `tool_counts: {...}`, and a non-`null` `final_post` containing `dry_run: true`

- [ ] **Step 2: If the dry-run errors, inspect the failure**

Check `/tmp/news-agent-dryrun.log` for the failing tool result. Typical issues:
- `search backend unavailable`: SearXNG not running locally → either start it via `cd deploy/searxng && docker compose up -d` or skip (we're testing flow, not search quality).
- `Anthropic API key`: ensure `ANTHROPIC_API_KEY` is set in `.env`.
- `Tool-Loop did not converge`: model may be looping. Raise `NEWS_AGENT_MAX_ITERATIONS` temporarily or read transcript from log.

Fix the root cause; do not skip this step — this is the integration safety net.

- [ ] **Step 3: Run one real (live-publish) trial against Claude**

```
./venv/bin/python -m agents.news.runner 2>&1 | tee /tmp/news-agent-live.log | tail -40
```

Expected: summary with `final_post.post_id` populated. Then check the post in WordPress:

```
curl -s -u 'news-agent:HCdlq79FBMJH9gtib745g3K5' \
     'https://wolfinisoftware.de/wp-json/wp/v2/posts?author=12&per_page=1' \
     | python3 -m json.tool | head -40
```

Expected: today's post visible with status `publish`.

- [ ] **Step 4: Spot-check the post in browser**

Open the `final_post.url` from the summary. Manually verify:
- Title is a clean headline, not the first sentence of the body
- Body is German, sections present, URLs cited
- Category = `AI-News`, tags from the allowlist applied
- No obvious hallucinations (especially CVE numbers + version ranges look plausible)

If any of this is off: the system prompt or the model is the culprit, not the code. Open a separate issue/spec for prompt tuning — do not block this plan.

---

## Task 15: Production deploy on VPS

**Files:** none — operations only.

- [ ] **Step 1: Push the branch**

```
git push -u origin feat/prompt-cache-system-block
```

(Or whatever branch name you're on; check with `git branch --show-current`.)

- [ ] **Step 2: Pull on VPS**

```
ssh ionos-vps 'cd /opt/ai-provider-service && git fetch && git checkout <branch> && git pull'
```

- [ ] **Step 3: Install the new Python dependency**

```
ssh ionos-vps 'cd /opt/ai-provider-service && ./venv/bin/pip install -r requirements.txt'
```

Expected: `trafilatura` installed.

- [ ] **Step 4: Add news-agent env vars to production `.env`**

```
ssh ionos-vps 'cat >> /opt/ai-provider-service/.env <<EOF

# --- News-Agent ---
NEWS_AGENT_PROVIDER=claude
NEWS_AGENT_FALLBACK=ollama
NEWS_AGENT_MODEL_CLAUDE=claude-sonnet-4-6
NEWS_AGENT_MODEL_OLLAMA=qwen3.6:latest
NEWS_AGENT_MAX_ITERATIONS=40
SEARXNG_URL=http://127.0.0.1:8888
WORDPRESS_URL=https://wolfinisoftware.de
WORDPRESS_USER=news-agent
WORDPRESS_APP_PASSWORD=HCdlq79FBMJH9gtib745g3K5
WORDPRESS_CATEGORY=AI-News
WORDPRESS_STATUS=publish
EOF'
```

- [ ] **Step 5: Install systemd units**

```
ssh ionos-vps 'sudo cp /opt/ai-provider-service/deploy/systemd/news-agent.service /etc/systemd/system/
                sudo cp /opt/ai-provider-service/deploy/systemd/news-agent.timer /etc/systemd/system/
                sudo mkdir -p /var/log/news-agent
                sudo chown ai-provider:ai-provider /var/log/news-agent
                sudo systemctl daemon-reload
                sudo systemctl enable --now news-agent.timer'
```

- [ ] **Step 6: Trigger one manual run to confirm production wiring**

```
ssh ionos-vps 'sudo systemctl start news-agent.service && sleep 5 && sudo systemctl status news-agent.service --no-pager'
```

Expected: `Active: inactive (dead)` (because oneshot completed). Then:

```
ssh ionos-vps 'sudo journalctl -u news-agent.service -n 60 --no-pager'
```

Expected: last lines show the JSON summary with `final_post.post_id`.

- [ ] **Step 7: Confirm timer is armed**

```
ssh ionos-vps 'systemctl list-timers news-agent.timer --no-pager'
```

Expected: next trigger shown for tomorrow 07:00 Europe/Berlin.

---

## Task 16: Documentation polish

**Files:**
- Modify: `README.md`
- Optionally: `OPERATIONS.md`

- [ ] **Step 1: Add a News-Agent section to `README.md`**

Read the existing `README.md` and append (or insert into the components list, depending on its structure) a section like:

```markdown
## News-Agent (täglicher Local-LLM-News-Roundup)

Modul `agents/news/` — täglicher WordPress-Post mit News aus dem Local-LLM-Ökosystem
(Ollama, llama.cpp, Tools drumherum).

- **Trigger:** systemd timer `news-agent.timer`, täglich 07:00 Europe/Berlin
- **Provider-Switch:** `NEWS_AGENT_PROVIDER=claude|ollama` in `.env`
- **Setup:** SearXNG (`deploy/searxng/`), WordPress App-Password
- **Spec:** [docs/superpowers/specs/2026-05-23-news-agent-hybrid-design.md](docs/superpowers/specs/2026-05-23-news-agent-hybrid-design.md)

Manueller Lauf:

    sudo systemctl start news-agent.service
    # oder mit Dry-Run (kein WP-Publish):
    sudo -u ai-provider /opt/ai-provider-service/venv/bin/python -m agents.news.runner --dry-run
```

- [ ] **Step 2: Commit**

```
git add README.md
git commit -m "docs: add News-Agent section to README"
```

- [ ] **Step 3: Final test sweep**

```
pytest tests/ -v 2>&1 | tail -10
```

Expected: all green.

---

## Done.

Phase 1 (Hybrid) running. To switch to Phase 2 (Voll-Migration on Ollama), edit `.env`:

```
NEWS_AGENT_PROVIDER=ollama
NEWS_AGENT_FALLBACK=          # leer = kein Fallback (oder 'claude' als Notnagel)
```

Then `sudo systemctl restart ai-provider-service` (only needed if the main service caches Config; for the news-agent cron run, the next 07:00 trigger picks up the new `.env` automatically).
