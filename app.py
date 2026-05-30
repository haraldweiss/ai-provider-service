"""ai-provider-service — Flask App Entry."""

import logging
import os
from flask import Flask, jsonify
from flask_cors import CORS

from config import Config
from database import db
import worker
from providers.lazy_init import mark_initialized, is_initialized


logger = logging.getLogger(__name__)


def _init_claude():
    """Initialize Claude provider (check API key availability)."""
    try:
        from providers import is_provider_available
        if is_provider_available('claude'):
            logger.info("Claude provider initialized")
            mark_initialized('claude')
        else:
            logger.warning("Claude provider not available (anthropic not installed)")
    except Exception as e:
        logger.warning(f"Failed to initialize Claude provider: {e}")


def _init_ollama():
    """Initialize Ollama provider (check connectivity)."""
    try:
        from providers import is_provider_available
        if is_provider_available('ollama'):
            from providers.ollama import OllamaClient
            client = OllamaClient({})
            if client.health():
                logger.info("Ollama provider initialized (healthy)")
                mark_initialized('ollama')
            else:
                logger.warning("Ollama provider initialized but not healthy")
        else:
            logger.warning("Ollama provider not available")
    except Exception as e:
        logger.warning(f"Failed to initialize Ollama provider: {e}")


def _init_openai():
    """Initialize OpenAI provider."""
    try:
        from providers import is_provider_available
        if is_provider_available('openai'):
            logger.info("OpenAI provider available")
            mark_initialized('openai')
        else:
            logger.warning("OpenAI provider not available (openai not installed)")
    except Exception as e:
        logger.warning(f"Failed to initialize OpenAI provider: {e}")


def _init_all_providers():
    """Initialize all available providers (eager mode)."""
    logger.info("Initializing all providers (eager mode)")
    _init_claude()
    _init_ollama()
    _init_openai()
    try:
        from providers import is_provider_available
        if is_provider_available('mammouth'):
            logger.info("Mammouth provider available")
            mark_initialized('mammouth')
        if is_provider_available('custom'):
            logger.info("Custom provider available")
            mark_initialized('custom')
    except Exception as e:
        logger.warning(f"Failed to initialize optional providers: {e}")


def create_app() -> Flask:
    Config.validate()
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    )

    app = Flask(__name__)
    app.config.from_object(Config)

    # CORS für Browser-direkt-Aufrufe (loganonymizer u.a.).
    # Wenn ALLOWED_ORIGINS leer ist, default `*` (lokale Dev).
    origins = Config.ALLOWED_ORIGINS or '*'
    CORS(app, resources={r'/*': {'origins': origins, 'supports_credentials': False}})

    db.init_app(app)
    with app.app_context():
        # Models importieren, damit Tables registriert werden.
        from storage.models import ProviderConfig, RequestQueue  # noqa: F401
        db.create_all()

    # Blueprints
    from api.providers_api import providers_bp
    from api.configs_api import configs_bp
    from api.chat_api import chat_bp
    from api.queue_api import queue_bp
    from api.health_api import health_bp
    from api.models_api import models_bp

    app.register_blueprint(providers_bp)
    app.register_blueprint(configs_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(queue_bp)
    app.register_blueprint(health_bp)
    app.register_blueprint(models_bp)

    @app.route('/')
    def index():
        return jsonify({
            'service': 'ai-provider-service',
            'version': '0.1.0',
            'endpoints': [
                'GET  /health',
                'GET  /providers?user_id=<id>',
                'GET  /providers/<id>/models?user_id=<id>',
                'GET  /providers/<id>/health',
                'POST /providers/<id>/test',
                'GET  /configs/<user_id>',
                'GET  /configs/<user_id>/<provider_id>',
                'POST /configs/<user_id>/<provider_id>',
                'DEL  /configs/<user_id>/<provider_id>',
                'POST /chat',
                'GET  /queue/<id>',
                'GET  /queue?user_id=<id>&status=<s>',
                'DEL  /queue/<id>',
            ],
        })

    # Initialize providers based on STARTUP_MODE
    startup_mode = Config.STARTUP_MODE
    logger.info(f"Starting with STARTUP_MODE={startup_mode}")

    if startup_mode == 'eager':
        _init_all_providers()
    elif startup_mode == 'minimal':
        _init_claude()
    elif startup_mode == 'lazy':
        logger.info("Lazy initialization enabled (providers load on first use)")
    else:
        logger.warning(f"Unknown STARTUP_MODE: {startup_mode}. Defaulting to lazy")

    # Worker nur im Main-Prozess starten (nicht im Reloader-Child).
    if os.getenv('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
        worker.start(app)

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(host=Config.HOST, port=Config.PORT, debug=False)
