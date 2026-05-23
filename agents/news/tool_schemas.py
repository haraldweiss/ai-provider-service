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
