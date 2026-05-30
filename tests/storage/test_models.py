"""Tests for storage models — encryption, serialization, timestamps."""

import pytest
import json
from storage.models import ProviderConfig, RequestQueue, OllamaModelRegistry, OllamaLoadedModels
from storage.encryption import encrypt, decrypt


@pytest.mark.unit
class TestProviderConfig:
    """Test ProviderConfig encryption and serialization."""

    def test_provider_config_set_get_config(self, app, db_session):
        """Test encrypt/decrypt config roundtrip."""
        with app.app_context():
            config_dict = {
                'api_key': 'sk-test-key-123',
                'api_endpoint': 'https://api.example.com',
                'organization_id': 'org-123',
            }

            cfg = ProviderConfig(user_id='user1', provider_id='openai')
            cfg.set_config(config_dict)
            db_session.add(cfg)
            db_session.commit()

            # Retrieve and decrypt
            retrieved = ProviderConfig.query.filter_by(user_id='user1', provider_id='openai').first()
            decrypted = retrieved.get_config()

            assert decrypted['api_key'] == 'sk-test-key-123'
            assert decrypted['api_endpoint'] == 'https://api.example.com'
            assert decrypted['organization_id'] == 'org-123'

    def test_provider_config_safe_dict_hides_api_key(self, app, db_session):
        """Test that safe_dict() never exposes API keys."""
        with app.app_context():
            config_dict = {
                'api_key': 'sk-secret-key',
                'api_endpoint': 'https://api.example.com',
                'name': 'My LLM',
            }

            cfg = ProviderConfig(user_id='user1', provider_id='openai')
            cfg.set_config(config_dict)
            db_session.add(cfg)
            db_session.commit()

            safe = cfg.to_safe_dict()

            assert 'api_key' not in safe  # Should not include key itself
            assert safe['has_api_key'] is True  # But should indicate it's present
            assert safe['api_endpoint'] == 'https://api.example.com'
            assert safe['name'] == 'My LLM'
            assert safe['provider_id'] == 'openai'
            assert safe['user_id'] == 'user1'

    def test_provider_config_safe_dict_missing_api_key(self, app, db_session):
        """Test safe_dict when config has no API key."""
        with app.app_context():
            config_dict = {
                'api_endpoint': 'https://api.example.com',
                'name': 'My LLM',
            }

            cfg = ProviderConfig(user_id='user1', provider_id='custom')
            cfg.set_config(config_dict)
            db_session.add(cfg)
            db_session.commit()

            safe = cfg.to_safe_dict()

            assert safe['has_api_key'] is False
            assert safe['api_endpoint'] == 'https://api.example.com'


@pytest.mark.unit
class TestRequestQueue:
    """Test RequestQueue serialization and state tracking."""

    def test_request_queue_to_dict(self, app, db_session):
        """Test queue item serialization."""
        with app.app_context():
            payload = {
                'provider': 'ollama',
                'model': 'mistral:7b',
                'messages': [{'role': 'user', 'content': 'Hello'}],
            }

            queue = RequestQueue(
                user_id='user1',
                primary_provider='ollama',
                payload=json.dumps(payload),
                status='pending',
            )
            db_session.add(queue)
            db_session.commit()

            d = queue.to_dict()

            assert d['user_id'] == 'user1'
            assert d['primary_provider'] == 'ollama'
            assert d['status'] == 'pending'
            assert d['attempts'] == 0
            assert 'created_at' in d
            assert 'id' in d

    def test_request_queue_get_payload(self, app, db_session):
        """Test payload deserialization."""
        with app.app_context():
            payload_dict = {
                'provider': 'ollama',
                'model': 'mistral:7b',
                'messages': [{'role': 'user', 'content': 'Test'}],
            }

            queue = RequestQueue(
                user_id='user1',
                primary_provider='ollama',
                payload=json.dumps(payload_dict),
            )
            db_session.add(queue)
            db_session.commit()

            retrieved = RequestQueue.query.first()
            deserialized = retrieved.get_payload()

            assert deserialized['provider'] == 'ollama'
            assert deserialized['model'] == 'mistral:7b'
            assert len(deserialized['messages']) == 1


@pytest.mark.unit
class TestOllamaLoadedModels:
    """Test OllamaLoadedModels tracking."""

    def test_loaded_models_to_dict(self, app, db_session, sample_loaded_model):
        """Test loaded model serialization."""
        with app.app_context():
            loaded = OllamaLoadedModels(**sample_loaded_model)
            db_session.add(loaded)
            db_session.commit()

            d = loaded.to_dict()

            assert d['model_name'] == 'mistral:7b'
            assert d['size_gb'] == 7.0
            assert 'loaded_at' in d
            assert 'last_used' in d

    def test_loaded_models_tracks_timestamps(self, app, db_session):
        """Test that timestamps are tracked correctly."""
        with app.app_context():
            loaded = OllamaLoadedModels(model_name='mistral:7b', size_gb=7.0)
            db_session.add(loaded)
            db_session.commit()

            retrieved = OllamaLoadedModels.query.first()

            assert retrieved.loaded_at is not None
            assert retrieved.last_used is not None
            assert retrieved.loaded_at <= retrieved.last_used


@pytest.mark.unit
class TestOllamaModelRegistry:
    """Test OllamaModelRegistry metadata tracking."""

    def test_model_registry_to_dict(self, app, db_session, sample_model_metadata):
        """Test model registry serialization."""
        with app.app_context():
            model = OllamaModelRegistry(**sample_model_metadata)
            db_session.add(model)
            db_session.commit()

            d = model.to_dict()

            assert d['model_name'] == 'mistral:7b'
            assert d['size_gb'] == 7.0
            assert d['use_case'] == 'chat'
            assert d['is_multimodal'] is False
            assert d['is_loaded'] is False
            assert d['min_vram_mb'] == 8192
            assert d['min_ram_mb'] == 16384

    def test_model_registry_unique_constraint(self, app, db_session, sample_model_metadata):
        """Test that model_name is unique."""
        with app.app_context():
            model1 = OllamaModelRegistry(**sample_model_metadata)
            db_session.add(model1)
            db_session.commit()

            # Try to add duplicate
            model2 = OllamaModelRegistry(**sample_model_metadata)
            db_session.add(model2)

            with pytest.raises(Exception):  # SQLAlchemy IntegrityError
                db_session.commit()
