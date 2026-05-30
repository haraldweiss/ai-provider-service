"""Shared test fixtures for ai-provider-service."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from app import create_app
from database import db
from config import Config

# Eager-import api.auth so its `Config` reference binds to the original
# Config class before any test reloads `config`. Otherwise tests that
# `importlib.reload(config)` leave api.auth referencing a stale Config
# object, and auth in unrelated tests breaks.
import api.auth  # noqa: F401, E402


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
