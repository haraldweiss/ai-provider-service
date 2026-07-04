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
import json
import logging
import re
import threading
import time
from typing import List, Optional, Set

import requests

from providers.base import BaseClient
from config import Config

logger = logging.getLogger(__name__)


def _response_excerpt(response: requests.Response | None, limit: int = 500) -> str:
    if response is None:
        return ''
    text = response.text or ''
    text = ' '.join(text.split())
    if len(text) > limit:
        return text[:limit] + '...'
    return text


def _tool_input(arguments):
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            decoded = json.loads(arguments)
            if isinstance(decoded, dict):
                return decoded
        except json.JSONDecodeError:
            pass
        return {'arguments': arguments}
    return {}


def _allowed_tool_names(tools: list[dict] | None) -> set[str]:
    names = set()
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        fn = tool.get('function') if isinstance(tool.get('function'), dict) else {}
        name = fn.get('name') or tool.get('name')
        if name:
            names.add(str(name))
    return names


def _normalize_tool_calls(message: dict, tools: list[dict] | None = None) -> list[dict]:
    allowed = _allowed_tool_names(tools)
    calls = []
    for idx, call in enumerate(message.get('tool_calls') or []):
        if not isinstance(call, dict):
            continue
        fn = call.get('function') if isinstance(call.get('function'), dict) else {}
        name = fn.get('name') or call.get('name') or ''
        if not name or (allowed and name not in allowed):
            continue
        arguments = fn.get('arguments', call.get('arguments', call.get('input', {})))
        calls.append({
            'id': call.get('id') or f'call_{idx}',
            'name': name,
            'input': _tool_input(arguments),
        })
    return calls


_DSML_TOOL_CALLS_RE = re.compile(
    r'<\s*｜｜DSML｜｜tool_calls\s*>(?P<body>.*?)'
    r'</\s*｜｜DSML｜｜tool_calls\s*>',
    re.DOTALL,
)
_DSML_INVOKE_RE = re.compile(
    r'<\s*｜｜DSML｜｜invoke\s+name="(?P<name>[^"]+)"\s*>'
    r'(?P<body>.*?)</\s*｜｜DSML｜｜invoke\s*>',
    re.DOTALL,
)
_DSML_PARAMETER_RE = re.compile(
    r'<\s*｜｜DSML｜｜parameter\s+name="(?P<name>[^"]+)"'
    r'(?:\s+string="(?P<string>[^"]+)")?\s*>'
    r'(?P<value>.*?)</\s*｜｜DSML｜｜parameter\s*>',
    re.DOTALL,
)


def _dsml_param_value(raw: str, string_flag: str | None):
    value = raw.strip()
    if string_flag == 'false':
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _extract_dsml_tool_calls(
    text: str, tools: list[dict] | None = None,
) -> tuple[str, list[dict]]:
    allowed = _allowed_tool_names(tools)
    calls: list[dict] = []

    def replace_block(match: re.Match) -> str:
        before = len(calls)
        block = match.group('body')
        for invoke in _DSML_INVOKE_RE.finditer(block):
            name = invoke.group('name').strip()
            if not name or (allowed and name not in allowed):
                continue
            params = {}
            for param in _DSML_PARAMETER_RE.finditer(invoke.group('body')):
                params[param.group('name')] = _dsml_param_value(
                    param.group('value'), param.group('string'),
                )
            calls.append({
                'id': f'call_{len(calls)}',
                'name': name,
                'input': params,
            })
        return '' if len(calls) > before else match.group(0)

    if not allowed:
        return text, []
    cleaned = _DSML_TOOL_CALLS_RE.sub(replace_block, text).strip()
    return cleaned, calls


def _is_tool_grammar_error(status: int, body: str) -> bool:
    if status != 400:
        return False
    lowered = body.replace("\\'", "'").lower()
    return (
        'value looks like object' in lowered
        and "can't find closing" in lowered
    )


def _result_from_ollama_data(
    data: dict, url: str, num_ctx: int, model: str, tools: list[dict] | None,
) -> dict:
    message = data.get('message', {}) or {}
    text = message.get('content', '')
    tool_calls = _normalize_tool_calls(message, tools=tools)
    if not tool_calls and text:
        text, tool_calls = _extract_dsml_tool_calls(text, tools=tools)
    out_tokens = data.get('eval_count', 0)
    if (out_tokens == 0 or not text) and not tool_calls:
        logger.warning(
            f'Ollama returned empty output ({url}): done_reason={data.get("done_reason")}, '
            f'eval_count={out_tokens}, prompt_eval_count={data.get("prompt_eval_count")}, '
            f'num_ctx={num_ctx}, model={model}'
        )
    stop_reason = data.get('done_reason') or 'stop'
    if tool_calls and stop_reason == 'stop':
        stop_reason = 'tool_use'
    return {
        'content': [{'text': text}],
        'tool_calls': tool_calls,
        'usage': {
            'input_tokens': data.get('prompt_eval_count', 0),
            'output_tokens': out_tokens,
        },
        'stop_reason': stop_reason,
    }


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
    timeout = 120  # Max-Wartezeit pro Ollama-Call. Muss < Gunicorn --timeout (180s)
                  # sein, sonst killt Gunicorn den Worker bevor Ollama den Fehler
                  # zurückgibt.

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

    def create_message(self, model: str, messages: list[dict], max_tokens: int = 600, *, tools: list[dict] | None = None) -> dict:
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
        # Auf nächste Power-of-2 runden, gedeckelt auf 65536.
        # Extrem große Kontexte (>65k) sind auf lokalen Macs selten nötig
        # und zwingen Ollama in den Swap (OOM/Thrashing) → Timeout.
        num_ctx = 1
        while num_ctx < needed:
            num_ctx *= 2
        num_ctx = min(num_ctx, 65536)

        payload = {
            'model': model,
            'messages': messages,
            'stream': False,
            'options': {
                'num_predict': max_tokens,
                'num_ctx': num_ctx,
            },
        }
        if tools:
            payload['tools'] = tools

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
                if len(self.endpoints) > 1 and idx > 0:
                    logger.info(f'Ollama call recovered on fallback endpoint #{idx}: {url}')
                return _result_from_ollama_data(data, url, num_ctx, model, tools)
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
                # 400 can be endpoint-specific in pool mode (model/load/options
                # mismatch on one Mac), so try the rest before giving up.
                # Auth-style 4xx are deterministic and should fail immediately.
                status = getattr(e.response, 'status_code', 0)
                body = _response_excerpt(e.response)
                if tools and _is_tool_grammar_error(status, body):
                    retry_payload = dict(payload)
                    retry_payload.pop('tools', None)
                    logger.warning(
                        'Ollama native tool call failed for model=%s on %s; '
                        'retrying without native tools so DSML text can be parsed: %s',
                        model, url, body,
                    )
                    try:
                        retry = requests.post(
                            f'{url}/api/chat', json=retry_payload, timeout=self.timeout,
                        )
                        retry.raise_for_status()
                        data = retry.json()
                        if len(self.endpoints) > 1 and idx > 0:
                            logger.info(
                                f'Ollama call recovered on fallback endpoint #{idx}: {url}'
                            )
                        return _result_from_ollama_data(data, url, num_ctx, model, tools)
                    except (requests.ConnectionError, requests.Timeout) as retry_exc:
                        last_exc = retry_exc
                        logger.warning(
                            'Ollama endpoint %s retry without native tools unreachable (%s); trying next',
                            url, type(retry_exc).__name__,
                        )
                        continue
                    except requests.HTTPError as retry_exc:
                        e = retry_exc
                        status = getattr(e.response, 'status_code', 0)
                        body = _response_excerpt(e.response)
                if status == 404:
                    # Update our model-map: this endpoint definitely does NOT
                    # have this model. Saves us re-trying it on subsequent calls
                    # for the same model until the next TTL refresh.
                    # Lock protects against concurrent _refresh_model_map replacement.
                    with OllamaClient._model_map_lock:
                        OllamaClient._endpoint_models.setdefault(url, set()).discard(model)
                if (500 <= status < 600 or status in (400, 404)) and len(order) > 1:
                    last_exc = e
                    logger.warning(
                        'Ollama endpoint %s returned %s for model=%s; trying next: %s',
                        url, status, model, body,
                    )
                    continue
                logger.warning(
                    'Ollama endpoint %s returned %s for model=%s: %s',
                    url, status, model, body,
                )
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
