"""Shared test fixtures for ai-provider-service."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy.pool import StaticPool
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
import providers.zai       # noqa: F401, E402
import pricing            # noqa: F401, E402
import model_cache        # noqa: F401, E402


@pytest.fixture(autouse=True)
def _reset_model_cache():
    """Leert den /v1/models-Async-Cache vor+nach jedem Test, damit Tests,
    die get_client/_load_config monkeypatchen, nicht durch Cache-Einträge
    früherer Tests kontaminiert werden."""
    model_cache.invalidate()
    yield
    model_cache.invalidate()


@pytest.fixture(autouse=True)
def _reset_pricing_cache():
    """Clear the in-memory pricing cache before each test so that tests
    which monkeypatch override paths see fresh data."""
    pricing._reset_pricing_cache()
    yield
    pricing._reset_pricing_cache()


@pytest.fixture
def app():
    """Flask-App + In-Memory SQLite für isolierte Tests.

    Sets MASTER_KEY and SERVICE_TOKEN on Config directly because
    config.py calls load_dotenv() at import time — os.environ overrides
    are ignored for already-loaded values.

    The database URI is forced on the Config CLASS for the same reason:
    SQLALCHEMY_DATABASE_URI is bound at import time, so the env var alone is
    ineffective. Without this, the session silently ran against the persistent
    instance/storage.db, and test_config_access_control's importlib.reload()
    flipped later Config objects to in-memory mid-session — the split brain
    produced order-dependent failures ("table user_access_tokens already
    exists" in the fixture, data leaking between tests). StaticPool pins every
    connection to the same in-memory database.
    """
    os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
    Config.MASTER_KEY = '8hbXucPt-LumWh0Ul9f9wka6VHzAHE29LvU52R3pEDA='
    Config.SERVICE_TOKEN = 'test-token'
    Config.MEMORY_ENABLED = True
    os.environ['MASTER_KEY'] = Config.MASTER_KEY
    os.environ['SERVICE_TOKEN'] = Config.SERVICE_TOKEN
    Config.DATABASE_URL = 'sqlite:///:memory:'
    Config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    Config.SQLALCHEMY_ENGINE_OPTIONS = {
        'poolclass': StaticPool,
        'connect_args': {'check_same_thread': False},
    }

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
