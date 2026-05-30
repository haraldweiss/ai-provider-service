"""ai-provider-service — Flask App Entry."""

import logging
import os
from flask import Flask, jsonify
from flask_cors import CORS

from config import Config
from database import db
import worker


def create_app() -> Flask:
    Config.validate()
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    )

    app = Flask(__name__)
    app.config.from_object(Config)

    # ProxyFix: trust X-Forwarded-Proto and X-Forwarded-Prefix from Apache.
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_prefix=1)

    # Required for admin UI session cookie. Fail-fast if missing.
    if not app.config.get('SECRET_KEY'):
        logging.getLogger(__name__).warning(
            'SECRET_KEY is not set — admin UI sessions will not work.'
        )

    # CORS für Browser-direkt-Aufrufe (loganonymizer u.a.).
    # Wenn ALLOWED_ORIGINS leer ist, default `*` (lokale Dev).
    origins = Config.ALLOWED_ORIGINS or '*'
    CORS(app, resources={r'/*': {'origins': origins, 'supports_credentials': False}})

    db.init_app(app)
    with app.app_context():
        # Models importieren, damit Tables registriert werden.
        from storage.models import ProviderConfig, RequestQueue, UsageEvent, ProviderGrant, UserProfile  # noqa: F401
        db.create_all()

    # Blueprints
    from api.providers_api import providers_bp
    from api.configs_api import configs_bp
    from api.chat_api import chat_bp
    from api.queue_api import queue_bp
    from api.health_api import health_bp
    from api.models_api import models_bp
    from api.usage_api import bp as usage_bp
    from api.admin_api import admin_bp
    from api.admin_ui import admin_ui_bp

    app.register_blueprint(providers_bp)
    app.register_blueprint(configs_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(queue_bp)
    app.register_blueprint(health_bp)
    app.register_blueprint(models_bp)
    app.register_blueprint(usage_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(admin_ui_bp)

    from cli import grants_bootstrap_command, update_opencode_pricing_command
    app.cli.add_command(grants_bootstrap_command)
    app.cli.add_command(update_opencode_pricing_command)

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
                'GET  /models/status',
                'POST /models/load',
                'POST /models/unload',
                'POST /chat',
                'GET  /queue/<id>',
                'GET  /queue?user_id=<id>&status=<s>',
                'DEL  /queue/<id>',
            ],
        })

    # Worker nur im Main-Prozess starten (nicht im Reloader-Child).
    if os.getenv('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
        worker.start(app)

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(host=Config.HOST, port=Config.PORT, debug=False)
