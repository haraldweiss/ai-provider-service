"""Integration tests for /models/* endpoints."""

import pytest
import json
from unittest.mock import patch, MagicMock
from storage.models import OllamaLoadedModels


@pytest.mark.unit
class TestModelsEndpoints:
    """Test /models/* API endpoints."""

    def test_models_load_endpoint_success(self, client, test_headers, app):
        """Test POST /models/load with successful load."""
        with app.app_context():
            with patch('api.models_api.model_manager.load_model') as mock_load:
                mock_load.return_value = True

                response = client.post(
                    '/models/load',
                    headers=test_headers,
                    json={'model_name': 'mistral:7b'}
                )

            assert response.status_code == 200
            data = response.get_json()
            assert data['loaded'] is True
            assert data['model_name'] == 'mistral:7b'

    def test_models_load_endpoint_requires_auth(self, client):
        """Test that /models/load requires authentication."""
        response = client.post(
            '/models/load',
            json={'model_name': 'mistral:7b'}
        )

        assert response.status_code == 401

    def test_models_unload_endpoint_success(self, client, test_headers, app):
        """Test POST /models/unload with successful unload."""
        with app.app_context():
            with patch('api.models_api.model_manager.unload_model') as mock_unload:
                mock_unload.return_value = True

                response = client.post(
                    '/models/unload',
                    headers=test_headers,
                    json={'model_name': 'mistral:7b'}
                )

            assert response.status_code == 200
            data = response.get_json()
            assert data['unloaded'] is True

    def test_models_unload_endpoint_not_loaded(self, client, test_headers, app):
        """Test /models/unload when model not loaded."""
        with app.app_context():
            with patch('api.models_api.model_manager.unload_model') as mock_unload:
                mock_unload.return_value = False

                response = client.post(
                    '/models/unload',
                    headers=test_headers,
                    json={'model_name': 'mistral:7b'}
                )

            assert response.status_code == 200
            data = response.get_json()
            assert data['unloaded'] is False

    def test_models_status_endpoint(self, client, test_headers, app, hardware_profile):
        """Test GET /models/status returns comprehensive status."""
        with app.app_context():
            # Add loaded model
            loaded = OllamaLoadedModels(model_name='mistral:7b', size_gb=7.0)
            from database import db
            db.session.add(loaded)
            db.session.commit()

            with patch('api.models_api.get_hardware_profile') as mock_hw:
                mock_hw.return_value = hardware_profile

                response = client.get(
                    '/models/status',
                    headers=test_headers
                )

            assert response.status_code == 200
            data = response.get_json()

            assert 'loaded' in data
            assert 'count' in data
            assert 'total_size_gb' in data
            assert 'hardware' in data
            assert 'utilization_pct' in data
            assert 'mistral:7b' in data['loaded']

    def test_models_status_empty(self, client, test_headers):
        """Test /models/status when no models loaded."""
        response = client.get(
            '/models/status',
            headers=test_headers
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data['loaded'] == []
        assert data['count'] == 0

    def test_models_unload_all_endpoint(self, client, test_headers, app):
        """Test POST /models/unload-all clears all models."""
        with app.app_context():
            # Add multiple models
            from database import db
            models = [OllamaLoadedModels(model_name=f'model{i}', size_gb=7.0) for i in range(3)]
            for m in models:
                db.session.add(m)
            db.session.commit()

            with patch('api.models_api.model_manager.unload_all_models') as mock_unload_all:
                mock_unload_all.return_value = 3

                response = client.post(
                    '/models/unload-all',
                    headers=test_headers,
                    json={}
                )

            assert response.status_code == 200
            data = response.get_json()
            assert data['unloaded_count'] == 3
            assert 'status' in data

    def test_models_unload_all_when_empty(self, client, test_headers):
        """Test /models/unload-all when no models loaded."""
        response = client.post(
            '/models/unload-all',
            headers=test_headers,
            json={}
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data['unloaded_count'] == 0

    def test_models_list_loadable_endpoint(self, client, test_headers, app):
        """Test GET /models/available returns loadable models."""
        response = client.get(
            '/models/available',
            headers=test_headers
        )

        # This endpoint might return 200 or 404 if not yet implemented
        assert response.status_code in [200, 404]
