"""Pytest configuration and shared fixtures."""

import os
import tempfile
import pytest
from datetime import datetime

os.environ['STARTUP_MODE'] = 'minimal'
os.environ['MASTER_KEY'] = 'test-master-key-32-bytes-min'
os.environ['SERVICE_TOKEN'] = 'test-service-token'
os.environ['OLLAMA_URL'] = 'http://localhost:11434'


@pytest.fixture
def temp_db():
    """Create a temporary SQLite database for tests."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def app(temp_db):
    """Create Flask test app with test database."""
    from app import create_app

    os.environ['DATABASE_URL'] = f'sqlite:///{temp_db}'

    app = create_app()
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{temp_db}'

    with app.app_context():
        from database import db
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()


@pytest.fixture
def db_session(app):
    """Database session for test."""
    from database import db
    with app.app_context():
        yield db.session


@pytest.fixture
def test_user_id():
    """Standard test user ID."""
    return "test-user-123"


@pytest.fixture
def test_headers():
    """Standard test headers with auth token."""
    return {
        'Authorization': f'Bearer test-service-token',
        'Content-Type': 'application/json',
    }


@pytest.fixture
def mock_ollama_client(mocker):
    """Mock OllamaClient for offline testing."""
    from providers.ollama import OllamaClient

    mock = mocker.MagicMock(spec=OllamaClient)
    mock.get_models.return_value = ['mistral:7b', 'llama2:7b']
    mock.health.return_value = True
    mock.create_message.return_value = {
        'content': [{'text': 'Mock response'}],
        'usage': {'input_tokens': 10, 'output_tokens': 5}
    }
    return mock


@pytest.fixture
def sample_model_metadata():
    """Sample Ollama model metadata."""
    return {
        'model_name': 'mistral:7b',
        'size_gb': 7.0,
        'use_case': 'chat',
        'is_multimodal': False,
        'description': 'Mistral 7B chat model',
        'pull_url': 'mistral:7b',
        'is_loaded': False,
        'min_vram_mb': 8192,
        'min_ram_mb': 16384,
    }


@pytest.fixture
def sample_loaded_model():
    """Sample loaded model entry."""
    return {
        'model_name': 'mistral:7b',
        'size_gb': 7.0,
        'loaded_at': datetime.utcnow(),
        'last_used': datetime.utcnow(),
    }


@pytest.fixture
def hardware_profile():
    """Sample hardware profile."""
    return {
        'gpu_vram_mb': 24000,
        'system_ram_mb': 64000,
        'cpu_cores': 8,
        'has_gpu': True,
        'gpu_type': 'nvidia',
    }
