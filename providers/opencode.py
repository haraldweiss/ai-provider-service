"""Opencode.ai (Zen) Client — OpenAI-compatible hosted gateway.
Auto-retry with free model variants when balance is insufficient.
Discovers free models from the API periodically.
Handles reasoning_content for models that output there.
"""

from __future__ import annotations
import json
import logging
import os
import re
import subprocess
import time
from typing import Optional
from openai import OpenAI, AuthenticationError
from providers.base import BaseClient
from config import Config

logger = logging.getLogger(__name__)

_MODEL_PREFIX_RE = re.compile(r'^(?:opencode-go/|opencode-)', re.IGNORECASE)
_BALANCE_ERR_RE = re.compile(r'insufficient balance|CreditsError', re.IGNORECASE)

NOTIFY_EMAIL = 'harald.weiss@wolfinisoftware.de'
_FREE_CACHE_FILE = '/tmp/opencode_free_models.json'
_FREE_CACHE_TTL = 86400


def _send_notification(subject: str, body: str) -> None:
    try:
        msg = f'Subject: {subject}\nFrom: ai-provider@wolfinisoftware.de\nTo: {NOTIFY_EMAIL}\n\n{body}\n'
        subprocess.run(
            ['/usr/sbin/sendmail', '-t'],
            input=msg, capture_output=True, timeout=10, text=True,
        )
        logger.info('Notification sent: %s', subject)
    except Exception as e:
        logger.warning('Failed to send notification: %s', e)


def refresh_free_models(client: OpenAI) -> list[str]:
    """Fetch current free models from API, compare with previous state,
    notify about additions AND removals, update cache."""
    raw = client.models.list()
    free_models = sorted(
        m.id for m in raw
        if m.id.endswith('-free') or m.id == 'big-pickle'
    )

    old = set()
    if os.path.exists(_FREE_CACHE_FILE):
        try:
            with open(_FREE_CACHE_FILE) as f:
                cached = json.load(f)
                old = set(cached.get('models', []))
        except Exception:
            pass
    elif os.path.exists(_FREE_CACHE_FILE + '.prev'):
        try:
            with open(_FREE_CACHE_FILE + '.prev') as pf:
                old = set(json.load(pf).get('models', []))
        except Exception:
            pass

    current = set(free_models)
    new_free = current - old
    removed = old - current

    parts = []
    if new_free:
        parts.append(
            'Neue Free-Modelle:\n' + '\n'.join(f'  + {m}' for m in sorted(new_free))
        )
    if removed:
        parts.append(
            'Nicht mehr kostenlos:\n' + '\n'.join(f'  - {m}' for m in sorted(removed))
        )
    if parts:
        _send_notification(
            'opencode.ai: Free-Modell-Änderungen',
            '\n\n'.join(parts)
            + '\n\nDer Auto-Failover wurde aktualisiert.',
        )

    try:
        os.replace(_FREE_CACHE_FILE, _FREE_CACHE_FILE + '.prev')
    except Exception:
        pass
    with open(_FREE_CACHE_FILE, 'w') as f:
        json.dump({'ts': time.time(), 'models': free_models}, f)
    logger.info('Discovered %d free models (%d new, %d removed)',
                 len(free_models), len(new_free), len(removed))
    return free_models


def _get_cached_free_models(client: OpenAI) -> list[str]:
    now = time.time()
    try:
        if os.path.exists(_FREE_CACHE_FILE):
            with open(_FREE_CACHE_FILE) as f:
                cached = json.load(f)
            if now - cached.get('ts', 0) < _FREE_CACHE_TTL:
                return cached['models']
    except Exception:
        pass

    return refresh_free_models(client)


def _extract_content(choice) -> str:
    """Extract text content from a chat choice, falling back to reasoning_content."""
    msg = getattr(choice, 'message', None) or choice.get('message', {})
    if hasattr(msg, 'content'):
        text = msg.content or ''
    else:
        text = msg.get('content') or ''
    if not text:
        # Some reasoning models put output in reasoning_content
        if hasattr(msg, 'reasoning_content'):
            text = msg.reasoning_content or ''
        elif isinstance(msg, dict):
            text = msg.get('reasoning_content', '')
    return text


class OpencodeClient(BaseClient):
    def __init__(self, config: dict):
        self._free_only = config.get('_free_only', False)
        api_key = config.get('api_key') or Config.OPENCODE_API_KEY
        if not api_key:
            raise ValueError("Opencode: api_key oder OPENCODE_API_KEY erforderlich")
        base_url = config.get('api_endpoint') or Config.OPENCODE_BASE_URL
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self._free_models: list[str] | None = None

    def get_models(self) -> list[str]:
        try:
            return sorted(m.id for m in self.client.models.list().data)
        except Exception as e:
            logger.warning(f'Opencode get_models failed: {e}')
            return []

    def get_free_models(self) -> list[str]:
        if self._free_models is None:
            self._free_models = _get_cached_free_models(self.client)
        return self._free_models

    @classmethod
    def try_refresh_free_models(cls) -> list[str]:
        """Proactive refresh: use system OPENCODE_API_KEY, refresh free model cache."""
        from config import Config
        api_key = Config.OPENCODE_API_KEY
        if not api_key:
            logger.warning('OPENCODE_API_KEY not set, cannot refresh free models')
            return []
        client = OpenAI(api_key=api_key, base_url=Config.OPENCODE_BASE_URL)
        return refresh_free_models(client)

    def _check_free_only(self, model: str) -> None:
        """Raise ValueError if this is a free-only session and model is paid."""
        if not self._free_only:
            return
        free_models = self.get_free_models()
        if model not in free_models:
            raise ValueError(
                f"'{model}' is a paid model — requires your own opencode API key. "
                f"Free models available: {', '.join(free_models)}"
            )

    def create_message(self, model: str, messages: list[dict], max_tokens: int = 600, *, tools: list[dict] | None = None) -> dict:
        clean = _MODEL_PREFIX_RE.sub('', model)
        if clean != model:
            logger.debug('Opencode model normalized: %s -> %s', model, clean)
        self._check_free_only(clean)

        try:
            r = self.client.chat.completions.create(
                model=clean, messages=messages, max_tokens=max_tokens
            )
            text = _extract_content(r.choices[0])
            return {
                'content': [{'text': text}],
                'usage': {
                    'input_tokens': r.usage.prompt_tokens,
                    'output_tokens': r.usage.completion_tokens,
                },
            }
        except AuthenticationError as e:
            err_body = str(e)
            if not _BALANCE_ERR_RE.search(err_body):
                raise

            tried_models = []
            if not clean.endswith('-free'):
                free_candidate = clean + '-free'
                tried_models.append(free_candidate)

            discovered = self.get_free_models()
            for fm in discovered:
                if fm not in tried_models and fm != clean:
                    tried_models.append(fm)

            for fallback_model in tried_models:
                try:
                    logger.warning(
                        'Balance insufficient for model=%s, trying free=%s',
                        clean, fallback_model,
                    )
                    r2 = self.client.chat.completions.create(
                        model=fallback_model, messages=messages, max_tokens=max_tokens
                    )
                    text2 = _extract_content(r2.choices[0])
                    _send_notification(
                        'opencode.ai: Auto-Failover zu Free-Modell',
                        f'Das bezahlte Modell "{clean}" hat kein Guthaben mehr.\n'
                        f'Automatischer Failover auf "{fallback_model}".\n\n'
                        f'Guthaben aufladen: https://opencode.ai/workspace/wrk_01KSKQJKEA4AQ3KV75MPTVNR3R/billing',
                    )
                    return {
                        'content': [{'text': text2}],
                        'usage': {
                            'input_tokens': r2.usage.prompt_tokens,
                            'output_tokens': r2.usage.completion_tokens,
                        },
                        'balance_failover': True,
                        'balance_failover_model': fallback_model,
                    }
                except AuthenticationError:
                    continue

            raise

    def health(self) -> bool:
        try:
            self.client.models.list()
            return True
        except Exception:
            return False
