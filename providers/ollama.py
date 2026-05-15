"""Ollama Client (lokal, /api/tags + /api/chat).

Multi-endpoint support: if Config.OLLAMA_URLS (or config['api_endpoints']) lists
multiple endpoints, requests are round-robin-distributed across them. A failing
endpoint is retried against the next one in line (up to len(endpoints) attempts).
This is how we load-balance between Macbook (VPS:11434) and Mac mini (VPS:11435)
without requiring two separate provider IDs or a plugin-side change. Single-
endpoint mode is preserved for backward compatibility.
"""

from __future__ import annotations
import itertools
import logging
import threading
from typing import List

import requests

from providers.base import BaseClient
from config import Config

logger = logging.getLogger(__name__)


def _resolve_endpoints(config: dict | None) -> List[str]:
    """Endpoint priority: config['api_endpoints'] (list) > config['api_endpoint']
    (single) > Config.OLLAMA_URLS (comma-separated env) > Config.OLLAMA_URL (single).
    Always returns at least one endpoint."""
    cfg = config or {}

    if isinstance(cfg.get('api_endpoints'), list) and cfg['api_endpoints']:
        return [str(e).rstrip('/') for e in cfg['api_endpoints']]

    if cfg.get('api_endpoint'):
        return [str(cfg['api_endpoint']).rstrip('/')]

    urls_env = getattr(Config, 'OLLAMA_URLS', '')
    if urls_env:
        urls = [u.strip().rstrip('/') for u in urls_env.split(',') if u.strip()]
        if urls:
            return urls

    return [Config.OLLAMA_URL.rstrip('/')]


class OllamaClient(BaseClient):
    timeout = 180  # lokale Models können sehr lange brauchen (Cold-Start, große Modelle)

    def __init__(self, config: dict):
        self.endpoints = _resolve_endpoints(config)
        self.base_url = self.endpoints[0]  # backwards-compat for any caller reading .base_url
        # Atomic round-robin counter shared by all instances within one Python process.
        # Using itertools.count + modulo gives lock-free RR across the gunicorn worker
        # (each worker has its own counter — that's fine, balancing is statistical).
        if not hasattr(OllamaClient, '_rr_counter'):
            OllamaClient._rr_counter = itertools.count()
            OllamaClient._rr_lock = threading.Lock()
        if len(self.endpoints) > 1:
            logger.info(f'Ollama pool mode: {len(self.endpoints)} endpoints: {self.endpoints}')

    def _pick_order(self) -> List[str]:
        """Return endpoints in the order we should try them this request.
        Round-robin start, then sequential fallback through the rest."""
        if len(self.endpoints) == 1:
            return [self.endpoints[0]]
        with OllamaClient._rr_lock:
            i = next(OllamaClient._rr_counter) % len(self.endpoints)
        return self.endpoints[i:] + self.endpoints[:i]

    def get_models(self) -> list[str]:
        # Try endpoints in RR order, return first successful response
        for url in self._pick_order():
            try:
                r = requests.get(f'{url}/api/tags', timeout=5)
                r.raise_for_status()
                data = r.json()
                return [m['name'] for m in data.get('models', [])]
            except Exception as e:
                logger.warning(f'Ollama get_models on {url} failed: {e}')
        return []

    def create_message(self, model: str, messages: list[dict], max_tokens: int = 600) -> dict:
        # num_ctx: Ollama default ist 2048 — viel zu klein für CV+Job-Prompts.
        # Wenn der Prompt das Window überschreitet, schneidet Ollama still ab,
        # die Anweisung "Antworte mit JSON" geht verloren und das Modell
        # generiert nichts → leerer Output.
        # Schätze grob auf Basis der Prompt-Länge und runde auf eine Power-of-2,
        # mit Mindestwert 8192 für CV-Match-Prompts.
        try:
            char_count = sum(len(m.get('content', '') or '') for m in messages)
        except Exception:
            char_count = 0
        # 1 Token ≈ 4 Chars (deutsch) → +max_tokens für die Antwort + Puffer
        needed = max(8192, int(char_count / 3) + max_tokens + 1024)
        # Auf nächste Power-of-2 runden
        num_ctx = 1
        while num_ctx < needed:
            num_ctx *= 2

        payload = {
            'model': model,
            'messages': messages,
            'stream': False,
            'options': {
                'num_predict': max_tokens,
                'num_ctx': num_ctx,
            },
        }

        # Try each endpoint in RR order. If one fails with a connection-level
        # error (down, refused, timeout), fall through to the next. Application
        # errors (4xx/5xx with valid JSON) still bubble up — we don't retry on
        # those because they're deterministic for this request.
        last_exc: Exception | None = None
        order = self._pick_order()
        for idx, url in enumerate(order):
            try:
                r = requests.post(f'{url}/api/chat', json=payload, timeout=self.timeout)
                r.raise_for_status()
                data = r.json()
                # eval_count = output tokens, prompt_eval_count = input tokens (wenn verfügbar).
                # Wenn das Modell nichts generiert hat (eval_count=0), loggen wir die done_reason
                # damit man später debuggen kann (length, stop, load, etc.).
                text = data.get('message', {}).get('content', '')
                out_tokens = data.get('eval_count', 0)
                if out_tokens == 0 or not text:
                    logger.warning(
                        f'Ollama returned empty output ({url}): done_reason={data.get("done_reason")}, '
                        f'eval_count={out_tokens}, prompt_eval_count={data.get("prompt_eval_count")}, '
                        f'num_ctx={num_ctx}, model={model}'
                    )
                if len(self.endpoints) > 1 and idx > 0:
                    logger.info(f'Ollama call recovered on fallback endpoint #{idx}: {url}')
                return {
                    'content': [{'text': text}],
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
                # 5xx might be transient (model loading, OOM) — try next; 4xx is deterministic, give up.
                status = getattr(e.response, 'status_code', 0)
                if 500 <= status < 600 and len(order) > 1:
                    last_exc = e
                    logger.warning(f'Ollama endpoint {url} returned {status}; trying next')
                    continue
                raise

        # All endpoints exhausted
        raise last_exc if last_exc else RuntimeError('All Ollama endpoints failed')

    def health(self) -> bool:
        # Healthy if ANY endpoint responds.
        for url in self.endpoints:
            try:
                r = requests.get(f'{url}/api/tags', timeout=3)
                if r.ok:
                    return True
            except Exception:
                continue
        return False
