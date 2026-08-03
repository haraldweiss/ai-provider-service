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
    OMLX_BASE_URL = os.getenv('OMLX_BASE_URL', 'http://host.docker.internal:11442/v1')
    OMLX_API_KEY = os.getenv('OMLX_API_KEY', '')

    DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///storage.db')

    QUEUE_TTL_HOURS = int(os.getenv('QUEUE_TTL_HOURS', '24'))
    HEALTH_CHECK_INTERVAL_SEC = int(os.getenv('HEALTH_CHECK_INTERVAL_SEC', '30'))
    QUEUE_DRAIN_INTERVAL_SEC = int(os.getenv('QUEUE_DRAIN_INTERVAL_SEC', '60'))

    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Access control (provider gating)
    ADMIN_TOKEN = os.getenv('ADMIN_TOKEN', '')
    ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', '')
    ADMIN_USER_ID = os.getenv('ADMIN_USER_ID', 'harald')
    UNGATED_PROVIDERS = set(
        p.strip() for p in (os.getenv('UNGATED_PROVIDERS') or 'ollama').split(',') if p.strip()
    )
    GATE_ENABLED = os.getenv('GATE_ENABLED', 'false').lower() == 'true'

    # opencode.ai provider
    OPENCODE_BASE_URL = os.getenv('OPENCODE_BASE_URL', 'https://opencode.ai/zen/v1')
    OPENCODE_API_KEY = os.getenv('OPENCODE_API_KEY', '')

    # z.ai (Zhipu / GLM) provider — OpenAI-compatible endpoint.
    # The central ZAI_API_KEY is restricted to the owner (see
    # ZAI_SERVER_KEY_ALLOWED_USERS); all other users must configure their own
    # key. Leave the allowlist empty to default to ADMIN_USER_ID only.
    ZAI_BASE_URL = os.getenv('ZAI_BASE_URL', 'https://api.z.ai/api/paas/v4')
    ZAI_API_KEY = os.getenv('ZAI_API_KEY', '')
    ZAI_SERVER_KEY_ALLOWED_USERS = os.getenv('ZAI_SERVER_KEY_ALLOWED_USERS', '')

    # openrouter.ai provider — OpenAI-compatible endpoint
    OPENROUTER_BASE_URL = os.getenv('OPENROUTER_BASE_URL', 'https://openrouter.ai/api/v1')
    OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY', '')

    # Cline provider — OpenAI-compatible hosted gateway (https://api.cline.bot).
    # Cline has no shared server key: each user configures their own Bearer
    # API key via ProviderConfig. The base URL is overridable for self-hosted
    # or proxied Cline-compatible endpoints.
    CLINE_BASE_URL = os.getenv('CLINE_BASE_URL', 'https://api.cline.bot/api/v1')
    CLINE_API_KEY = os.getenv('CLINE_API_KEY', '')

    # Claude model list (comma-separated, overrides static KNOWN_MODELS)
    CLAUDE_MODEL_LIST = os.getenv('CLAUDE_MODEL_LIST', '')

    # Flask sessions (admin UI cookie)
    SECRET_KEY = os.getenv('SECRET_KEY', '')
    # Email notifications (admin alerts)
    SMTP_HOST = os.getenv('SMTP_HOST', '')
    SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
    SMTP_USER = os.getenv('SMTP_USER', '')
    SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
    SMTP_FROM = os.getenv('SMTP_FROM', '')
    SMTP_TLS = os.getenv('SMTP_TLS', 'true').lower() == 'true'
    ADMIN_EMAIL = os.getenv('ADMIN_EMAIL', '')
    NOTIFY_ON_GRANT_REQUEST = os.getenv('NOTIFY_ON_GRANT_REQUEST', 'true').lower() == 'true'
    NOTIFY_ON_USER_REGISTER = os.getenv('NOTIFY_ON_USER_REGISTER', 'true').lower() == 'true'


    # Admin UI auto-auth via X-Forwarded-User (set by Apache Basic Auth).
    # Must be explicitly enabled.  The source address must also be in the
    # explicit proxy allowlist; Docker delivers host-Apache traffic via its
    # bridge gateway rather than as loopback inside the container.
    TRUST_FORWARDED_USER = os.getenv('TRUST_FORWARDED_USER', 'false').lower() == 'true'
    TRUSTED_PROXY_IPS = {
        address.strip()
        for address in os.getenv('TRUSTED_PROXY_IPS', '127.0.0.1,::1').split(',')
        if address.strip()
    }

    # Markdown memory (Phase 1)
    VAULT_PATH = os.getenv('VAULT_PATH', os.path.join(os.path.dirname(__file__), 'vault'))
    MEMORY_ENABLED = os.getenv('MEMORY_ENABLED', 'false').lower() == 'true'
    SUMMARY_PROFILE = os.getenv('SUMMARY_PROFILE', 'cheap-first')
    SUMMARY_MAX_NOTES_PER_DAY = int(os.getenv('SUMMARY_MAX_NOTES_PER_DAY', '200'))
    MEMORY_FREE_MODELS = [
        m.strip() for m in os.getenv('MEMORY_FREE_MODELS', '').split(',') if m.strip()
    ]

    # Region-locked model exclusion — comma-separated entries. Each entry may
    # be a bare model prefix (applies to every provider) or a `provider/prefix`
    # pair (applies only to that provider). Hidden from /v1/models and blocked
    # from dispatch. Default excludes the z.ai (Zhipu) China-hosted endpoint
    # models; GLM models served via global gateways (opencode/openrouter)
    # remain available. Set EXCLUDE_REGION_LOCKED_MODELS= to disable.
    EXCLUDE_REGION_LOCKED_MODELS = [
        m.strip() for m in os.getenv('EXCLUDE_REGION_LOCKED_MODELS',
            'zai/glm-5,zai/glm-4.7,zai/glm-4.6,zai/glm-4.5,zai/glm-4-32b,'
            'zai/glm-5v,zai/glm-4.6v,zai/glm-4.5v,zai/glm-ocr').split(',') if m.strip()
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
