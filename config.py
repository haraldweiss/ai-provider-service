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

    # Access control (provider gating)
    ADMIN_TOKEN = os.getenv('ADMIN_TOKEN', '')
    ADMIN_USER_ID = os.getenv('ADMIN_USER_ID', 'harald')
    UNGATED_PROVIDERS = set(
        p.strip() for p in (os.getenv('UNGATED_PROVIDERS') or 'ollama').split(',') if p.strip()
    )
    GATE_ENABLED = os.getenv('GATE_ENABLED', 'false').lower() == 'true'

    # opencode.ai provider
    OPENCODE_BASE_URL = os.getenv('OPENCODE_BASE_URL', 'https://opencode.ai/zen/v1')
    OPENCODE_API_KEY = os.getenv('OPENCODE_API_KEY', '')

    # Flask sessions (admin UI cookie)
    SECRET_KEY = os.getenv('SECRET_KEY', '')

    # Markdown memory (Phase 1)
    VAULT_PATH = os.getenv('VAULT_PATH', os.path.join(os.path.dirname(__file__), 'vault'))
    MEMORY_ENABLED = os.getenv('MEMORY_ENABLED', 'false').lower() == 'true'
    SUMMARY_PROFILE = os.getenv('SUMMARY_PROFILE', 'cheap-first')
    SUMMARY_MAX_NOTES_PER_DAY = int(os.getenv('SUMMARY_MAX_NOTES_PER_DAY', '200'))
    MEMORY_FREE_MODELS = [
        m.strip() for m in os.getenv('MEMORY_FREE_MODELS', '').split(',') if m.strip()
    ]

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
