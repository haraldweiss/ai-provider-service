"""Tests for ModelManager — model loading/unloading lifecycle."""

import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock
from providers.model_manager import ModelManager, get_model_manager
from storage.models import OllamaLoadedModels, OllamaModelRegistry


@pytest.mark.unit
class TestModelManager:
    """Test ModelManager lifecycle methods."""

    def test_load_model_when_not_loaded(self, app, db_session, sample_model_metadata):
        """Test loading a new model into Ollama."""
        with app.app_context():
            # Add model to registry
            reg = OllamaModelRegistry(**sample_model_metadata)
            db_session.add(reg)
            db_session.commit()

            manager = ModelManager()

            # Mock ollama pull subprocess
            with patch('providers.model_manager.subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0)

                # Load the model
                result = manager.load_model('mistral:7b')

            assert result is True
            # Verify in DB
            loaded = OllamaLoadedModels.query.filter_by(model_name='mistral:7b').first()
            assert loaded is not None
            assert loaded.size_gb == 7.0

    def test_load_model_already_loaded(self, app, db_session, sample_loaded_model):
        """Test loading a model that's already loaded (should update timestamp)."""
        with app.app_context():
            # Add pre-loaded model
            loaded = OllamaLoadedModels(**sample_loaded_model)
            original_loaded_at = loaded.loaded_at
            db_session.add(loaded)
            db_session.commit()

            old_last_used = loaded.last_used
            import time
            time.sleep(0.01)  # Ensure timestamp is different

            manager = ModelManager()
            result = manager.load_model('mistral:7b')

            assert result is True
            # Verify timestamp was updated
            reloaded = OllamaLoadedModels.query.filter_by(model_name='mistral:7b').first()
            assert reloaded.loaded_at == original_loaded_at  # Not changed
            assert reloaded.last_used > old_last_used  # Updated

    def test_unload_model(self, app, db_session, sample_loaded_model):
        """Test unloading a model."""
        with app.app_context():
            loaded = OllamaLoadedModels(**sample_loaded_model)
            db_session.add(loaded)
            db_session.commit()

            manager = ModelManager()
            result = manager.unload_model('mistral:7b')

            assert result is True
            # Verify removed from DB
            reloaded = OllamaLoadedModels.query.filter_by(model_name='mistral:7b').first()
            assert reloaded is None

    def test_unload_model_not_loaded(self, app):
        """Test unloading a model that's not loaded."""
        with app.app_context():
            manager = ModelManager()
            result = manager.unload_model('mistral:7b')
            assert result is False

    def test_unload_lru(self, app, db_session):
        """Test unloading least-recently-used model."""
        with app.app_context():
            # Add 3 models with different last_used times
            models = [
                OllamaLoadedModels(model_name='model1', size_gb=7.0, last_used=datetime(2024, 1, 1, 10, 0)),
                OllamaLoadedModels(model_name='model2', size_gb=7.0, last_used=datetime(2024, 1, 1, 11, 0)),
                OllamaLoadedModels(model_name='model3', size_gb=7.0, last_used=datetime(2024, 1, 1, 12, 0)),
            ]
            for m in models:
                db_session.add(m)
            db_session.commit()

            manager = ModelManager()
            result = manager._try_unload_lru()

            assert result is True
            # Verify oldest was unloaded
            assert OllamaLoadedModels.query.filter_by(model_name='model1').first() is None
            assert OllamaLoadedModels.query.filter_by(model_name='model2').first() is not None
            assert OllamaLoadedModels.query.filter_by(model_name='model3').first() is not None

    def test_unload_all_models(self, app, db_session):
        """Test unloading all models."""
        with app.app_context():
            models = [
                OllamaLoadedModels(model_name=f'model{i}', size_gb=7.0)
                for i in range(3)
            ]
            for m in models:
                db_session.add(m)
            db_session.commit()

            manager = ModelManager()
            count = manager.unload_all_models()

            assert count == 3
            assert OllamaLoadedModels.query.count() == 0

    def test_get_loaded_models(self, app, db_session):
        """Test listing loaded models."""
        with app.app_context():
            models = [
                OllamaLoadedModels(model_name=f'model{i}', size_gb=7.0)
                for i in range(3)
            ]
            for m in models:
                db_session.add(m)
            db_session.commit()

            manager = ModelManager()
            loaded = manager.get_loaded_models()

            assert len(loaded) == 3
            assert 'model0' in loaded
            assert 'model1' in loaded
            assert 'model2' in loaded

    def test_get_model_status(self, app, db_session, sample_loaded_model):
        """Test getting status of a specific model."""
        with app.app_context():
            loaded = OllamaLoadedModels(**sample_loaded_model)
            db_session.add(loaded)
            db_session.commit()

            manager = ModelManager()
            status = manager.get_model_status('mistral:7b')

            assert status is not None
            assert status['model_name'] == 'mistral:7b'
            assert status['size_gb'] == 7.0
            assert 'loaded_at' in status
            assert 'last_used' in status

    def test_get_all_status(self, app, db_session, hardware_profile):
        """Test comprehensive status reporting."""
        with app.app_context():
            # Add loaded models
            models = [
                OllamaLoadedModels(model_name='model1', size_gb=7.0),
                OllamaLoadedModels(model_name='model2', size_gb=14.0),
            ]
            for m in models:
                db_session.add(m)
            db_session.commit()

            manager = ModelManager()
            with patch('providers.model_manager.get_hardware_profile') as mock_hw:
                mock_hw.return_value = hardware_profile
                status = manager.get_all_status()

            assert len(status['loaded']) == 2
            assert status['count'] == 2
            assert status['total_size_gb'] == 21.0
            assert status['utilization_pct'] > 0
            assert 'gpu_vram_mb' in status['hardware']

    def test_get_loadable_models(self, app, db_session, sample_model_metadata):
        """Test querying loadable models by constraints."""
        with app.app_context():
            # Add models to registry
            models = [
                OllamaModelRegistry(
                    model_name='mistral:7b',
                    size_gb=7.0,
                    use_case='chat',
                    is_multimodal=False,
                    pull_url='mistral:7b',
                    is_loaded=True,
                    min_vram_mb=8192,
                    min_ram_mb=16384,
                ),
                OllamaModelRegistry(
                    model_name='llava:7b',
                    size_gb=7.0,
                    use_case='vision',
                    is_multimodal=True,
                    pull_url='llava:7b',
                    is_loaded=True,
                    min_vram_mb=10240,
                    min_ram_mb=20480,
                ),
            ]
            for m in models:
                db_session.add(m)
            db_session.commit()

            manager = ModelManager()
            with patch('providers.model_manager.get_hardware_profile') as mock_hw:
                mock_hw.return_value = {'gpu_vram_mb': 24000, 'system_ram_mb': 64000}
                loadable = manager.get_loadable_models(use_case='chat')

            assert 'mistral:7b' in loadable
            assert 'llava:7b' not in loadable  # Different use_case


@pytest.mark.unit
def test_get_model_manager_singleton(app):
    """Test that get_model_manager returns singleton instance."""
    with app.app_context():
        mgr1 = get_model_manager()
        mgr2 = get_model_manager()
        assert mgr1 is mgr2
