"""API endpoints for model discovery, filtering, and lifecycle management."""

from flask import Blueprint, request, jsonify
from api.auth import require_token
from storage.models import OllamaModelRegistry
from providers.hardware import get_hardware_profile
from providers.model_manager import get_model_manager
import logging

logger = logging.getLogger(__name__)

models_bp = Blueprint('models', __name__, url_prefix='/providers/ollama')
model_manager = get_model_manager()


@models_bp.route('/models', methods=['GET'])
def list_ollama_models():
    """
    List all available (loaded) Ollama models with metadata.
    
    Returns:
    {
        "models": [
            {
                "name": "mistral:7b",
                "size_gb": 7.0,
                "use_case": "chat",
                "is_multimodal": false
            },
            ...
        ]
    }
    """
    try:
        models = OllamaModelRegistry.query.filter_by(is_loaded=True).all()
        return jsonify({
            "models": [
                {
                    "name": m.model_name,
                    "size_gb": m.size_gb,
                    "use_case": m.use_case,
                    "is_multimodal": m.is_multimodal,
                }
                for m in models
            ]
        }), 200
    except Exception as e:
        logger.error(f"Error listing models: {e}")
        return jsonify({"error": str(e)}), 500


@models_bp.route('/models/by-capability', methods=['GET'])
def list_models_by_capability():
    """
    List models filtered by capability and hardware constraints.
    
    Query parameters:
    - use_case: "reasoning", "chat", "vision", "embedding" (optional)
    - max_size_gb: max model size in GB (optional)
    - include_hardware: true/false to include hardware info in response (default: true)
    
    Returns:
    {
        "hardware": {
            "gpu_vram_mb": 24000,
            "system_ram_mb": 64000,
            "available_for_models_mb": 24000
        },
        "models": [
            {
                "name": "mistral:7b",
                "size_gb": 7.0,
                "use_case": "chat",
                "is_multimodal": false,
                "min_vram_mb": 7700
            },
            ...
        ]
    }
    """
    try:
        use_case = request.args.get('use_case')
        max_size_gb = request.args.get('max_size_gb', type=float)
        include_hardware = request.args.get('include_hardware', 'true').lower() == 'true'
        
        hw = get_hardware_profile()
        available_vram = hw['gpu_vram_mb'] or (hw['system_ram_mb'] // 2)
        
        query = OllamaModelRegistry.query.filter_by(is_loaded=True)
        
        if use_case:
            query = query.filter_by(use_case=use_case)
        
        max_mb = int((max_size_gb or 999) * 1024)
        query = query.filter(OllamaModelRegistry.min_vram_mb <= min(max_mb, available_vram))
        
        models = query.order_by(OllamaModelRegistry.size_gb).all()
        
        response = {
            "models": [
                {
                    "name": m.model_name,
                    "size_gb": m.size_gb,
                    "use_case": m.use_case,
                    "is_multimodal": m.is_multimodal,
                    "min_vram_mb": m.min_vram_mb,
                }
                for m in models
            ]
        }
        
        if include_hardware:
            response["hardware"] = {
                "gpu_vram_mb": hw['gpu_vram_mb'],
                "system_ram_mb": hw['system_ram_mb'],
                "gpu_type": hw['gpu_type'],
                "cpu_cores": hw['cpu_cores'],
                "available_for_models_mb": available_vram,
            }
        
        return jsonify(response), 200
    
    except Exception as e:
        logger.error(f"Error filtering models: {e}")
        return jsonify({"error": str(e)}), 500


@models_bp.route('/models/all', methods=['GET'])
def list_all_models():
    """
    List all models in registry (loaded and not loaded).
    Useful for discovering available models before pulling them.
    
    Returns:
    {
        "total": 150,
        "loaded": 12,
        "models": [
            {
                "name": "mistral:7b",
                "size_gb": 7.0,
                "use_case": "chat",
                "is_multimodal": false,
                "is_loaded": true,
                "description": "..."
            },
            ...
        ]
    }
    """
    try:
        models = OllamaModelRegistry.query.all()
        loaded_count = sum(1 for m in models if m.is_loaded)
        
        return jsonify({
            "total": len(models),
            "loaded": loaded_count,
            "models": [
                {
                    "name": m.model_name,
                    "size_gb": m.size_gb,
                    "use_case": m.use_case,
                    "is_multimodal": m.is_multimodal,
                    "is_loaded": m.is_loaded,
                    "description": m.description,
                    "min_vram_mb": m.min_vram_mb,
                }
                for m in sorted(models, key=lambda x: (-x.is_loaded, x.size_gb))
            ]
        }), 200
    except Exception as e:
        logger.error(f"Error listing all models: {e}")
        return jsonify({"error": str(e)}), 500


@models_bp.route('/hardware', methods=['GET'])
def get_hardware_info():
    """
    Get hardware profile of the Ollama host.
    
    Returns:
    {
        "gpu_vram_mb": 24000 or null,
        "system_ram_mb": 64000,
        "gpu_type": "nvidia" or "amd" or "metal" or null,
        "cpu_cores": 12,
        "has_gpu": true
    }
    """
    try:
        hw = get_hardware_profile()
        return jsonify(hw), 200
    except Exception as e:
        logger.error(f"Error getting hardware info: {e}")
        return jsonify({"error": str(e)}), 500


@models_bp.route('/models/load', methods=['POST'])
@require_token
def load_model():
    """
    Explicitly load a model into Ollama memory.
    
    Request body:
    {
        "model_name": "mistral:7b",
        "force": false  # If true, unload other models aggressively
    }
    
    Returns:
    {
        "loaded": true,
        "model_name": "mistral:7b",
        "status": "Model loaded successfully"
    }
    """
    try:
        body = request.get_json() or {}
        model_name = body.get('model_name')
        
        if not model_name:
            return jsonify({'error': 'model_name is required'}), 400
        
        force = body.get('force', False)
        success = model_manager.load_model(model_name, force=force)
        
        return jsonify({
            'loaded': success,
            'model_name': model_name,
            'status': 'Model loaded successfully' if success else 'Failed to load model'
        }), 200 if success else 400
    
    except Exception as e:
        logger.error(f"Error loading model: {e}")
        return jsonify({'error': str(e)}), 500


@models_bp.route('/models/unload', methods=['POST'])
@require_token
def unload_model():
    """
    Explicitly unload a model from Ollama memory.
    
    Request body:
    {
        "model_name": "mistral:7b"
    }
    
    Returns:
    {
        "unloaded": true,
        "model_name": "mistral:7b",
        "status": "Model unloaded successfully"
    }
    """
    try:
        body = request.get_json() or {}
        model_name = body.get('model_name')
        
        if not model_name:
            return jsonify({'error': 'model_name is required'}), 400
        
        success = model_manager.unload_model(model_name)
        
        return jsonify({
            'unloaded': success,
            'model_name': model_name,
            'status': 'Model unloaded successfully' if success else 'Model was not loaded'
        }), 200
    
    except Exception as e:
        logger.error(f"Error unloading model: {e}")
        return jsonify({'error': str(e)}), 500


@models_bp.route('/models/status', methods=['GET'])
@require_token
def get_model_status():
    """
    Get detailed status of all loaded models and hardware.
    
    Returns:
    {
        "loaded": ["mistral:7b", "llama2:7b"],
        "count": 2,
        "total_size_gb": 14.0,
        "hardware": {
            "gpu_vram_mb": 24000,
            "system_ram_mb": 64000,
            "gpu_type": "nvidia",
            "cpu_cores": 12,
            "has_gpu": true,
            "available_for_models_mb": 24000
        },
        "utilization_pct": 64.4,
        "models": [
            {
                "name": "mistral:7b",
                "size_gb": 7.0,
                "loaded_at": "2026-05-13T12:34:56",
                "last_used": "2026-05-13T14:22:10"
            },
            ...
        ]
    }
    """
    try:
        status = model_manager.get_all_status()
        
        # Add detailed model info
        loaded_models = model_manager.get_loaded_models()
        models_detail = []
        for model_name in loaded_models:
            model_status = model_manager.get_model_status(model_name)
            if model_status:
                models_detail.append(model_status)
        
        status['models'] = models_detail
        
        return jsonify(status), 200
    
    except Exception as e:
        logger.error(f"Error getting model status: {e}")
        return jsonify({'error': str(e)}), 500


@models_bp.route('/models/unload-all', methods=['POST'])
@require_token
def unload_all_models():
    """
    Unload all currently loaded models.
    
    Returns:
    {
        "unloaded_count": 3,
        "models": ["mistral:7b", "llama2:7b", "phi:2.7b"],
        "status": "All models unloaded"
    }
    """
    try:
        loaded_before = model_manager.get_loaded_models()
        count = model_manager.unload_all_models()
        
        return jsonify({
            'unloaded_count': count,
            'models': loaded_before,
            'status': f'Unloaded {count} models'
        }), 200
    
    except Exception as e:
        logger.error(f"Error unloading all models: {e}")
        return jsonify({'error': str(e)}), 500
