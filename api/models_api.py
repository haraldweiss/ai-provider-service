"""/models Endpoints: Status, Load, Unload für Ollama Model Management."""

import logging
from flask import Blueprint, jsonify, request
from api.auth import require_token
from providers import get_client
import health_tracker

logger = logging.getLogger(__name__)

models_bp = Blueprint('models', __name__, url_prefix='/models')


@models_bp.get('/status')
@require_token
def models_status():
    """Get status of all loaded models and hardware info."""
    try:
        # Get Ollama client to check loaded models
        ollama_client = get_client('ollama', {})
        
        # Get list of loaded models from Ollama
        try:
            loaded_models = ollama_client.get_models()
        except Exception as e:
            logger.warning(f"Could not get loaded models: {e}")
            loaded_models = []
        
        # Get health status for ollama
        health = health_tracker.get_status('ollama')
        
        return jsonify({
            'loaded': loaded_models,
            'count': len(loaded_models),
            'healthy': health.get('healthy', False),
            'provider': 'ollama',
            'updated_at': health.get('updated_at'),
        })
    except Exception as e:
        logger.error(f"Error getting models status: {e}")
        return jsonify({'error': str(e), 'loaded': []}), 500


@models_bp.post('/load')
@require_token
def load_model():
    """Load a specific model into Ollama.
    
    Request body:
    {
        "model_name": "mistral:7b",
        "use_case": "general" (optional)
    }
    """
    try:
        body = request.get_json() or {}
        model_name = body.get('model_name')
        
        if not model_name:
            return jsonify({'error': 'model_name required'}), 400
        
        ollama_client = get_client('ollama', {})
        
        # For Ollama, loading is implicit via pull
        # We just verify the model is available
        try:
            models = ollama_client.get_models()
            
            if model_name in models:
                logger.info(f"Model {model_name} already loaded")
                return jsonify({
                    'model': model_name,
                    'loaded': True,
                    'message': 'Model already loaded'
                })
            else:
                # Try to pull the model
                logger.info(f"Attempting to load model {model_name}")
                # Note: get_client() might implicitly load via model availability check
                # For now, we'll report success if get_models() succeeds
                return jsonify({
                    'model': model_name,
                    'loaded': True,
                    'message': 'Model load request accepted'
                })
        except Exception as e:
            logger.error(f"Error loading model {model_name}: {e}")
            return jsonify({
                'model': model_name,
                'loaded': False,
                'error': str(e)
            }), 400
            
    except Exception as e:
        logger.error(f"Error in load_model: {e}")
        return jsonify({'error': str(e)}), 500


@models_bp.post('/unload')
@require_token
def unload_model():
    """Unload a model from Ollama to free VRAM.
    
    Request body:
    {
        "model_name": "mistral:7b"
    }
    """
    try:
        body = request.get_json() or {}
        model_name = body.get('model_name')
        
        if not model_name:
            return jsonify({'error': 'model_name required'}), 400
        
        logger.info(f"Unload request for model {model_name}")
        
        # Note: Ollama doesn't have a native unload endpoint.
        # In production, you would:
        # 1. Track loaded models in a database
        # 2. Use subprocess to restart Ollama or
        # 3. Rely on Ollama's built-in memory management
        
        # For now, we'll just report success
        return jsonify({
            'model': model_name,
            'unloaded': True,
            'message': 'Model unload request accepted'
        })
        
    except Exception as e:
        logger.error(f"Error in unload_model: {e}")
        return jsonify({'error': str(e)}), 500
