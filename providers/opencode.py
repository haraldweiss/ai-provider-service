"""opencode.ai clients — OpenAI-compatible hosted gateways.

``OpencodeClient``   — Zen / Console (pay-as-you-go, app-locked free tier).
``OpencodeGoClient`` — Go / Go Plus (subscription-backed catalog).

Shared by both: the client-specific User-Agent, the per-conversation
``x-opencode-session`` header (mandatory upstream, AGENTS §3.12) and the
reasoning_content fallback. Free-model discovery is Zen-only: Go serves the
plan's catalog, free variants included, so nothing is filtered there.
"""

from __future__ import annotations
import hashlib
import json
import logging
import os
import re
import subprocess
import time
from openai import OpenAI, AuthenticationError
from providers.base import BaseClient
from providers.response_metadata import completion_metadata, reported_cost
from config import Config

logger = logging.getLogger(__name__)

APP_NAME = 'ai-provider-service'

_MODEL_PREFIX_RE = re.compile(r'^(?:opencode-go/|opencode-)', re.IGNORECASE)
_BALANCE_ERR_RE = re.compile(r'insufficient balance|CreditsError', re.IGNORECASE)


def _user_agent() -> str:
    """Identify as our own coding agent, not the generic OpenAI SDK.

    OpenCode Go's docs require clients to send their own user agent
    (e.g. ``my-coding-agent/1.0``) rather than a generic SDK/HTTP-library name.
    """
    version = getattr(Config, 'SERVICE_VERSION', '') or '0.0.0'
    return f'{APP_NAME}/{version}'


def _default_headers() -> dict:
    return {'User-Agent': _user_agent()}


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return ' '.join(
            p.get('text', '') for p in content
            if isinstance(p, dict) and isinstance(p.get('text'), str)
        )
    return ''


def _conversation_session_id(messages: list[dict] | None) -> str:
    """Return an opaque session id that is stable per conversation.

    OpenCode Go uses ``x-opencode-session`` to pin consecutive turns of one
    conversation to the same backend (routing + prompt-cache affinity). The id
    must therefore stay identical across turns but differ between
    conversations, so we hash the stable prefix (system prompt + first user
    turn) instead of the ever-growing message history.
    """
    system_parts: list[str] = []
    first_user = ''
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = m.get('role', '')
        text = _content_text(m.get('content'))
        if role == 'system':
            system_parts.append(text)
        elif role == 'user' and not first_user:
            first_user = text
            break
    seed = '\n'.join(system_parts) + '\x1f' + first_user
    if first_user or system_parts:
        return hashlib.sha256(seed.encode('utf-8')).hexdigest()[:32]
    return APP_NAME

# 2026-09-18: opencode.ai locked its free tier to the OpenCode app —
# every free-model chat via this gateway gets
# `403 FreeTierError: "OpenCode's free tier can only be used from within
# OpenCode"`. Advertising the free models in /v1/models only produces
# dead selections in pi / Open WebUI, so free-only mode hides them.
# Paid models via a personal key are unaffected.
#
# 2026-09-23: live-tested UA spoofing (`opencode/latest`, `opencode/1.18.x`,
# `opencode/latest/1.3.15/cli`, with/without `x-opencode-client: cli`) against
# every discovered free model — all still return 403 FreeTierError, so no
# header workaround unlocks the free tier. Re-check daily via
# `flask check-provider-docs`; if the lock lifts, set
# OPENCODE_ADVERTISE_FREE_MODELS=1 to re-list the free models.
def _free_models_advertised() -> bool:
    return os.getenv('OPENCODE_ADVERTISE_FREE_MODELS', '').strip() == '1'

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
    """opencode.ai Zen / Console client.

    Class attributes select the Config entries that supply the defaults, so a
    variant endpoint only has to override those names (see OpencodeGoClient).
    """

    _base_url_attr = 'OPENCODE_BASE_URL'
    _api_key_attr = 'OPENCODE_API_KEY'
    #: Base the OpenAI SDK appends `/models` to. Empty = same client as chat.
    _models_base_url_attr = ''
    _label = 'Opencode'
    # Zen free-only sessions advertise nothing: the free tier is locked to the
    # OpenCode app upstream (2026-09-18) and dead model IDs in pi / Open WebUI
    # are worse than an absent model.
    _hide_locked_free_models = True
    # Zen retries a paid model with a free variant when the balance is gone.
    _free_model_failover = True

    def __init__(self, config: dict):
        self._free_only = config.get('_free_only', False)
        self._api_key = config.get('api_key') or getattr(Config, self._api_key_attr, '')
        if not self._api_key:
            raise ValueError(
                f"{self._label}: api_key oder {self._api_key_attr} erforderlich")
        base_url = config.get('api_endpoint') or getattr(Config, self._base_url_attr, '')
        self.client = OpenAI(
            api_key=self._api_key,
            base_url=base_url,
            default_headers=_default_headers()
        )
        # Some providers split the model-list endpoint from the chat endpoint
        # (OpenCode v2 Go: /inference/go/v1/models vs /inference/go/openai/v1),
        # so a second client is built only when the bases actually differ. A
        # caller-supplied chat endpoint wins: a custom Go-compatible endpoint is
        # assumed to serve both unless models_endpoint says otherwise.
        models_base = config.get('models_endpoint') or ''
        if not models_base and not config.get('api_endpoint'):
            models_base = (getattr(Config, self._models_base_url_attr, '')
                           if self._models_base_url_attr else '')
        self._models_client = self.client
        if models_base and models_base.rstrip('/') != base_url.rstrip('/'):
            self._models_client = OpenAI(
                api_key=self._api_key,
                base_url=models_base,
                default_headers=_default_headers()
            )
        self._free_models: list[str] | None = None

    def get_models(self) -> list[str]:
        try:
            all_models = sorted(m.id for m in self._models_client.models.list().data)
            if self._free_only:
                if self._hide_locked_free_models and not _free_models_advertised():
                    logger.info(
                        'Opencode free-only mode: hiding %d free models '
                        '(free tier client-locked upstream since 2026-09-18)',
                        len(all_models))
                    return []
                free_set = set(self.get_free_models())
                filtered = [m for m in all_models if m in free_set]
                logger.info('Opencode free-only mode: %d/%d models shown',
                            len(filtered), len(all_models))
                return filtered
            return all_models
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
        client = OpenAI(
            api_key=api_key, 
            base_url=Config.OPENCODE_BASE_URL,
            default_headers=_default_headers()
        )
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
            kwargs = dict(model=clean, messages=messages, max_tokens=max_tokens)
            if tools:
                kwargs['tools'] = tools
            extra_headers = {'x-opencode-session': _conversation_session_id(messages)}
            r = self.client.chat.completions.create(**kwargs, extra_headers=extra_headers)
            text = _extract_content(r.choices[0])
            return {
                **completion_metadata(r),
                'content': [{'text': text}],
                'usage': {
                    **reported_cost(r.usage),
                    'input_tokens': getattr(r.usage, 'prompt_tokens', None),
                    'output_tokens': getattr(r.usage, 'completion_tokens', None),
                },
            }
        except AuthenticationError as e:
            err_body = str(e)
            if not self._free_model_failover or not _BALANCE_ERR_RE.search(err_body):
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
                        model=fallback_model, messages=messages,
                        max_tokens=max_tokens, tools=tools if tools else None,
                        extra_headers=extra_headers,
                    )
                    text2 = _extract_content(r2.choices[0])
                    _send_notification(
                        'opencode.ai: Auto-Failover zu Free-Modell',
                        f'Das bezahlte Modell "{clean}" hat kein Guthaben mehr.\n'
                        f'Automatischer Failover auf "{fallback_model}".\n\n'
                        f'Guthaben aufladen: https://opencode.ai/workspace/wrk_01KSKQJKEA4AQ3KV75MPTVNR3R/billing',
                    )
                    return {
                        **completion_metadata(r2),
                        'content': [{'text': text2}],
                        'usage': {
                            **reported_cost(r2.usage),
                            'input_tokens': getattr(r2.usage, 'prompt_tokens', None),
                            'output_tokens': getattr(r2.usage, 'completion_tokens', None),
                        },
                        'balance_failover': True,
                        'balance_failover_model': fallback_model,
                    }
                except AuthenticationError:
                    continue

            raise

    def health(self) -> bool:
        try:
            self._models_client.models.list()
            return True
        except Exception:
            return False


class OpencodeGoClient(OpencodeClient):
    """opencode.ai Go / Go Plus client.

    Subscription-backed catalog, added 2026-10-18 after OpenCode invalidated the
    Console key with the breach response (AGENTS §7). Differences from Zen:

    - key/endpoint defaults come from ``OPENCODE_GO_API_KEY`` /
      ``OPENCODE_GO_BASE_URL`` (+ ``OPENCODE_GO_MODELS_BASE_URL``, because the
      v2 Go docs put chat and the model list on separate bases);
    - no free-only gating — the Go plan covers its whole catalog;
    - no balance→free-variant retry: an exhausted plan surfaces as 402/429 and
      the dispatcher routes it to the configured fallback (AGENTS §3.11).

    The inherited per-conversation ``x-opencode-session`` header and client
    User-Agent are mandatory here — a request without the session header fails
    upstream on both the v2 inference and the /zen/go/v1 route (verified
    2026-10-18: HTTP 500).
    """

    _base_url_attr = 'OPENCODE_GO_BASE_URL'
    _models_base_url_attr = 'OPENCODE_GO_MODELS_BASE_URL'
    _api_key_attr = 'OPENCODE_GO_API_KEY'
    _label = 'Opencode Go'
    _hide_locked_free_models = False
    _free_model_failover = False
