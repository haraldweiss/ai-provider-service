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
import time
from typing import List, Optional, Set

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

    # Class-level per-gunicorn-worker model map for predictive routing.
    # Filled lazily on first request and refreshed every _MODEL_MAP_TTL_SEC.
    # When two pool endpoints host different subsets of models (e.g. the Mac
    # mini has dev-coder + mistral-nemo, the Macbook additionally has the
    # big qwen3.6:latest), routing a qwen3.6 request to Mini would 404. With
    # the map populated we can pre-filter the candidate endpoints to those
    # that actually have the model — saving the 404 round-trip and keeping
    # output deterministic (qwen3.6 always served by Macbook).
    _endpoint_models: dict = {}        # {endpoint_url: set(model_name)}
    _model_map_lock = threading.Lock()
    _model_map_last_refresh: float = 0.0
    _MODEL_MAP_TTL_SEC = 300            # refresh /api/tags every 5 min

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

    def _refresh_model_map(self) -> None:
        """Re-fetch /api/tags from every endpoint. Best-effort: on failure
        keep the previous entry (so a temporarily-down endpoint doesn't
        disappear from the map mid-flight)."""
        new_map: dict = {}
        for ep in self.endpoints:
            try:
                r = requests.get(f'{ep}/api/tags', timeout=3)
                r.raise_for_status()
                new_map[ep] = {m['name'] for m in r.json().get('models', [])}
            except Exception as e:
                prev = OllamaClient._endpoint_models.get(ep, set())
                new_map[ep] = prev
                logger.debug(f'model-map refresh {ep} failed: {type(e).__name__} (keeping prev set of {len(prev)})')
        OllamaClient._endpoint_models = new_map
        OllamaClient._model_map_last_refresh = time.monotonic()
        # Summary log so operators can see the routing table
        summary = ', '.join(f'{ep.split("//")[-1]}={len(s)}' for ep, s in new_map.items())
        logger.info(f'Ollama model-map refreshed: {summary}')

    def _maybe_refresh_model_map(self) -> None:
        # Cheap unlocked check, then locked check (double-checked locking).
        if time.monotonic() - OllamaClient._model_map_last_refresh <= OllamaClient._MODEL_MAP_TTL_SEC:
            return
        with OllamaClient._model_map_lock:
            if time.monotonic() - OllamaClient._model_map_last_refresh > OllamaClient._MODEL_MAP_TTL_SEC:
                self._refresh_model_map()

    def _endpoints_hosting(self, model: str) -> List[str]:
        """Endpoints that the model-map says have the given model.
        Empty list = unknown (map not populated for that model, or model
        does not exist anywhere)."""
        if not model:
            return []
        return [ep for ep in self.endpoints if model in OllamaClient._endpoint_models.get(ep, set())]

    def _pick_order(self, model: Optional[str] = None) -> List[str]:
        """Return endpoints in the order we should try them this request.

        - Single endpoint: just that one.
        - With model + populated map: round-robin only across endpoints
          that host the model, append the rest as last-resort fallback
          (so a stale map can still self-heal).
        - Otherwise: blind round-robin across all endpoints (legacy)."""
        if len(self.endpoints) == 1:
            return [self.endpoints[0]]
        self._maybe_refresh_model_map()
        eligible = self._endpoints_hosting(model) if model else []
        if eligible and len(eligible) < len(self.endpoints):
            with OllamaClient._rr_lock:
                i = next(OllamaClient._rr_counter) % len(eligible)
            rest = [ep for ep in self.endpoints if ep not in eligible]
            return eligible[i:] + eligible[:i] + rest
        # All endpoints have it (or we don't know) → blind RR across all
        with OllamaClient._rr_lock:
            i = next(OllamaClient._rr_counter) % len(self.endpoints)
        return self.endpoints[i:] + self.endpoints[:i]

    def get_models(self) -> list[str]:
        # Query EVERY pool endpoint and return the union of their loaded models.
        # Pool members can host different model sets (e.g. Macbook has qwen3.6
        # but the Mac mini doesn't) — round-robin-picking just one endpoint
        # would hide whatever the other one has. Sorted+deduped for stability.
        seen: Set[str] = set()
        any_success = False
        for url in self.endpoints:
            try:
                r = requests.get(f'{url}/api/tags', timeout=5)
                r.raise_for_status()
                data = r.json()
                for m in data.get('models', []):
                    name = m.get('name')
                    if name:
                        seen.add(name)
                any_success = True
            except Exception as e:
                logger.warning(f'Ollama get_models on {url} failed: {e}')
        if not any_success:
            return []
        return sorted(seen)

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

        # Try each endpoint in RR order. Endpoints that the model-map says
        # host this model are preferred; the rest go in as last-resort
        # fallback (lets a stale map self-heal). Failover triggers on
        # ConnectionError/Timeout/5xx (transient) and on 404 (model truly
        # missing on that endpoint — perfect signal to refresh the map
        # next time around).
        last_exc: Exception | None = None
        order = self._pick_order(model)
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
                # 5xx might be transient (model loading, OOM) — try next.
                # 404 means "this endpoint doesn't have this model" — perfect
                # case for per-model failover when machines in the pool host
                # different subsets of models (e.g. Mini has dev-coder but
                # not qwen3.6:latest, Macbook has both). Retry on 404 too.
                # Other 4xx (400, 401, 403) are deterministic bugs — give up.
                status = getattr(e.response, 'status_code', 0)
                if status == 404:
                    # Update our model-map: this endpoint definitely does NOT
                    # have this model. Saves us re-trying it on subsequent calls
                    # for the same model until the next TTL refresh.
                    OllamaClient._endpoint_models.setdefault(url, set()).discard(model)
                if (500 <= status < 600 or status == 404) and len(order) > 1:
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
