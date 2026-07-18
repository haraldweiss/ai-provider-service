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
from datetime import datetime, timezone
from typing import Any

import requests
import trafilatura

from agents.news.tool_schemas import TAG_ALLOWLIST, DEFAULT_CATEGORY

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
