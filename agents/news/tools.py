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
import trafilatura

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
