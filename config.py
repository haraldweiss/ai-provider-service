"""Service-Konfiguration aus Umgebungsvariablen."""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    HOST = os.getenv('HOST', '127.0.0.1')
    PORT = int(os.getenv('PORT', '8767'))

    MASTER_KEY = os.getenv('MASTER_KEY', '')
    SERVICE_TOKEN = os.getenv('SERVICE_TOKEN', '')

    ALLOWED_ORIGINS = [
        o.strip() for o in os.getenv('ALLOWED_ORIGINS', '').split(',') if o.strip()
    ]

    ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '')
    OLLAMA_URL = os.getenv('OLLAMA_URL', 'http://127.0.0.1:11434')
    OLLAMA_URLS = os.getenv('OLLAMA_URLS', '')

    DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///storage.db')

    QUEUE_TTL_HOURS = int(os.getenv('QUEUE_TTL_HOURS', '24'))
    HEALTH_CHECK_INTERVAL_SEC = int(os.getenv('HEALTH_CHECK_INTERVAL_SEC', '30'))
    QUEUE_DRAIN_INTERVAL_SEC = int(os.getenv('QUEUE_DRAIN_INTERVAL_SEC', '60'))

    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False

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

    @classmethod
    def validate(cls):
        missing = []
        if not cls.MASTER_KEY:
            missing.append('MASTER_KEY')
        if not cls.SERVICE_TOKEN:
            missing.append('SERVICE_TOKEN')
        if missing:
            raise RuntimeError(
                f"Pflicht-Env-Vars fehlen: {', '.join(missing)}. "
                "Siehe .env.example"
            )
