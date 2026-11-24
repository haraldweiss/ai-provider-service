"""OpenRouter Client — OpenAI-compatible hosted gateway with free model support.

OpenRouter inference requires a valid API key, including for free models.
This client discovers free models from the API and filters to free-only when
the dispatcher requests it for users without a personal key; inference then
uses the configured server key.
"""

from __future__ import annotations
import json
import logging
import os
import time
import openai
from openai import OpenAI
from providers.base import BaseClient
from providers.response_metadata import completion_metadata, reported_cost
from config import Config

logger = logging.getLogger(__name__)

_FREE_CACHE_FILE = '/tmp/openrouter_free_models.json'
_FREE_CACHE_TTL = 86400

# OpenRouter's catalog mixes API families: `:batch` variants only serve the
# asynchronous Batch API, and future families may add non-chat endpoints. This
# gateway only offers chat completions, so models that do not declare that
# endpoint are not advertised (`/v1/models` is what clients pick from).
_CHAT_ENDPOINT = '/api/v1/chat/completions'

# App attribution. HTTP-Referer is required for attribution; categories must be
# one of OpenRouter's marketplace values, since unknown values are dropped
# without an error (https://openrouter.ai/docs/app-attribution).
_ATTRIBUTION_CATEGORIES = 'general-chat'

# Free-tier models are served from a shared upstream pool, so a 429 is usually
# a momentary throttle rather than a hard failure. Retry a couple of times with
# backoff (honouring Retry-After when OpenRouter sends it) before letting the
# dispatcher fall back / surface the error.
_RATE_LIMIT_ATTEMPTS = 3
_RATE_LIMIT_BASE_DELAY = 2.0
_RATE_LIMIT_MAX_DELAY = 30.0


def _retry_after_seconds(exc: Exception) -> float | None:
    """Parse a Retry-After header from an OpenAI SDK error, if present."""
    try:
        headers = getattr(getattr(exc, 'response', None), 'headers', None)
        if headers:
            value = headers.get('retry-after') or headers.get('Retry-After')
            if value:
                return max(0.0, min(float(value), _RATE_LIMIT_MAX_DELAY))
    except (TypeError, ValueError):
        pass
    return None


def _is_free_model(api_model) -> bool:
    """Check if a model entry from OpenRouter is free."""
    pricing = getattr(api_model, 'pricing', None) or {}
    prompt = pricing.get('prompt') if isinstance(pricing, dict) else getattr(pricing, 'prompt', None)
    completion = pricing.get('completion') if isinstance(pricing, dict) else getattr(pricing, 'completion', None)
    try:
        return float(prompt) == 0.0 and float(completion) == 0.0
    except (ValueError, TypeError):
        return False


def _serves_chat(api_model) -> bool:
    """True unless the model declares a catalog without chat completions.

    Models from other OpenAI-compatible endpoints do not carry `endpoints`, so
    they are assumed to serve chat.
    """
    endpoints = getattr(api_model, 'endpoints', None)
    if isinstance(endpoints, (list, tuple)) and endpoints:
        return _CHAT_ENDPOINT in endpoints
    return True


class OpenRouterUpstreamError(RuntimeError):
    """OpenRouter answered HTTP 200 but the choice carries a failure.

    Such responses (rate-limited free pool, provider quota, filtered output)
    used to become an empty assistant message. Surfacing them lets dispatch()
    fall back to another provider instead.
    """

    def __init__(self, model: str, code, detail: str):
        if isinstance(code, bool) or not isinstance(code, int):
            code = None
        self.status_code = code
        message = f'OpenRouter upstream error for {model}'
        if code is not None:
            message += f' (code {code})'
        if detail:
            message += f': {detail[:300]}'
        super().__init__(message)


def _choice_error(choice):
    """Return (code, detail) when a 200 choice reports an upstream failure."""
    error = getattr(choice, 'error', None)
    if isinstance(error, dict):
        return error.get('code'), str(error.get('message') or '')
    if getattr(choice, 'finish_reason', None) == 'error':
        return None, 'upstream reported finish_reason=error'
    return None



def _get_cached_free_models(client: OpenAI) -> list[str]:
    """Fetch free models from cache or API."""
    now = time.time()
    stale_models: list[str] = []
    try:
        if os.path.exists(_FREE_CACHE_FILE):
            with open(_FREE_CACHE_FILE) as f:
                cached = json.load(f)
            stale_models = cached.get('models', [])
            if now - cached.get('ts', 0) < _FREE_CACHE_TTL:
                return stale_models
    except Exception:
        pass

    return _refresh_free_models(client, fallback=stale_models)


def _iter_model_entries(raw) -> list:
    return list(getattr(raw, 'data', raw))


def _refresh_free_models(client: OpenAI, fallback: list[str] | None = None) -> list[str]:
    """Fetch current free models from OpenRouter API, update cache."""
    try:
        raw = client.models.list()
        free_models = sorted(
            m.id for m in _iter_model_entries(raw)
            if _serves_chat(m) and _is_free_model(m)
        )
    except Exception as e:
        logger.warning('OpenRouter free model discovery failed: %s', e)
        return fallback or []

    try:
        os.makedirs(os.path.dirname(_FREE_CACHE_FILE) or '.', exist_ok=True)
        with open(_FREE_CACHE_FILE, 'w') as f:
            json.dump({'ts': time.time(), 'models': free_models}, f)
    except Exception:
        pass

    logger.info('Discovered %d OpenRouter free models', len(free_models))
    return free_models


def _make_openai_client(api_key: str | None, base_url: str) -> OpenAI:
    """Create an OpenAI client that works across SDK versions.

    The OpenAI SDK requires an api_key even to instantiate the client. Keep
    a placeholder when none is configured so public model discovery can still
    work; it is not a valid credential for inference, including free models.
    
    Includes app attribution headers for OpenRouter rankings.
    """
    if not api_key:
        api_key = 'sk-anonymous'
    return OpenAI(
        api_key=api_key,
        base_url=base_url,
        default_headers={
            'HTTP-Referer': 'https://ai-provider-service.wolfinisoftware.de',
            'X-OpenRouter-Title': 'ai-provider-service',
            'X-OpenRouter-Categories': _ATTRIBUTION_CATEGORIES,
        }
    )


class OpenRouterClient(BaseClient):
    def __init__(self, config: dict):
        self._free_only = config.get('_free_only', False)
        api_key = config.get('api_key') or Config.OPENROUTER_API_KEY or None
        base_url = config.get('api_endpoint') or Config.OPENROUTER_BASE_URL
        self.client = _make_openai_client(api_key, base_url)
        self._free_models: list[str] | None = None
        self._base_url = base_url

    def get_models(self) -> list[str]:
        try:
            all_models = sorted(
                m.id for m in _iter_model_entries(self.client.models.list())
                if _serves_chat(m)
            )
            if self._free_only:
                free_set = set(self.get_free_models())
                filtered = [m for m in all_models if m in free_set]
                logger.info('OpenRouter free-only mode: %d/%d models shown',
                            len(filtered), len(all_models))
                return filtered
            return all_models
        except Exception as e:
            logger.warning('OpenRouter get_models failed: %s', e)
            return []

    def get_free_models(self) -> list[str]:
        if self._free_models is None:
            self._free_models = _get_cached_free_models(self.client)
        return self._free_models

    @classmethod
    def try_refresh_free_models(cls) -> list[str]:
        """Proactive refresh using optional OPENROUTER_API_KEY."""
        client = _make_openai_client(
            Config.OPENROUTER_API_KEY or None,
            Config.OPENROUTER_BASE_URL,
        )
        return _refresh_free_models(client)

    def create_message(self, model: str, messages: list[dict], max_tokens: int = 600,
                       *, tools: list[dict] | None = None) -> dict:
        if self._free_only and model not in self.get_free_models():
            raise ValueError(f"'{model}' requires your own OpenRouter API key")
        kwargs = dict(model=model, messages=messages, max_tokens=max_tokens)
        if tools:
            kwargs['tools'] = tools

        r = None
        for attempt in range(_RATE_LIMIT_ATTEMPTS):
            try:
                r = self.client.chat.completions.create(**kwargs)
                break
            except openai.RateLimitError as e:
                if attempt >= _RATE_LIMIT_ATTEMPTS - 1:
                    raise
                delay = _retry_after_seconds(e)
                if delay is None:
                    delay = min(_RATE_LIMIT_BASE_DELAY * (2 ** attempt), _RATE_LIMIT_MAX_DELAY)
                logger.warning(
                    'OpenRouter 429 for %s — retry %d/%d in %.1fs',
                    model, attempt + 1, _RATE_LIMIT_ATTEMPTS - 1, delay,
                )
                time.sleep(delay)

        if r is None:  # pragma: no cover - loop always breaks or raises
            raise RuntimeError('OpenRouter: no response after retries')

        choices = getattr(r, 'choices', None) or []
        if not choices:
            raise OpenRouterUpstreamError(model, None, 'empty choices array')
        choice = choices[0]
        failure = _choice_error(choice)
        if failure is not None:
            code, detail = failure
            raise OpenRouterUpstreamError(model, code, detail)

        msg = getattr(choice, 'message', None) or {}
        text = ''
        if hasattr(msg, 'content'):
            text = msg.content or ''
        elif isinstance(msg, dict):
            text = msg.get('content', '')

        return {
            **completion_metadata(r),
            'content': [{'text': text}],
            'usage': {
                **reported_cost(r.usage),
                'input_tokens': getattr(r.usage, 'prompt_tokens', None),
                'output_tokens': getattr(r.usage, 'completion_tokens', None),
            },
        }

    def health(self) -> bool:
        try:
            self.client.models.list()
            return True
        except Exception:
            return False
