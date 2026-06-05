"""Shared test fixtures for ai-provider-service."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from app import create_app
from database import db
from config import Config

# Eager-import every module that does `from config import Config` so each
# captures its reference to the original Config class before any test runs
# `importlib.reload(config)` (see test_config_access_control). Reload
# rebinds `sys.modules['config'].Config` to a new class object — modules
# imported AFTER the reload would pick up the new class while modules
# imported BEFORE keep the old one, and the `app` fixture mutates the old
# one. Without these eager imports, the resulting split-brain Config can
# silently flake auth, gate, and provider tests depending on collection
# order. See conftest line for `api.auth` (original tripwire from Task 2).
import api.auth          # noqa: F401, E402
import api.gate          # noqa: F401, E402
import api.admin_api     # noqa: F401, E402
import api.admin_ui      # noqa: F401, E402
import providers.opencode  # noqa: F401, E402
import providers.claude    # noqa: F401, E402
import providers.ollama    # noqa: F401, E402


@pytest.fixture
def app():
    """Flask-App + In-Memory SQLite für isolierte Tests.

    Sets MASTER_KEY and SERVICE_TOKEN on Config directly because
    config.py calls load_dotenv() at import time — os.environ overrides
    are ignored for already-loaded values.
    """
    os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
    Config.MASTER_KEY = '8hbXucPt-LumWh0Ul9f9wka6VHzAHE29LvU52R3pEDA='
    Config.SERVICE_TOKEN = 'test-token'
    Config.MEMORY_ENABLED = True
    os.environ['MASTER_KEY'] = Config.MASTER_KEY
    os.environ['SERVICE_TOKEN'] = Config.SERVICE_TOKEN

    app = create_app()
    app.config['TESTING'] = True
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()
