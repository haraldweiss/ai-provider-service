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

    DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///storage.db')

    QUEUE_TTL_HOURS = int(os.getenv('QUEUE_TTL_HOURS', '24'))
    HEALTH_CHECK_INTERVAL_SEC = int(os.getenv('HEALTH_CHECK_INTERVAL_SEC', '30'))
    QUEUE_DRAIN_INTERVAL_SEC = int(os.getenv('QUEUE_DRAIN_INTERVAL_SEC', '60'))

    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False

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
