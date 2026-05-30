"""Model Lifecycle Manager for Ollama — load, unload, track models in memory."""

import logging
import subprocess
from typing import List, Optional
from datetime import datetime
from storage.models import OllamaLoadedModels, OllamaModelRegistry
from database import db
from providers.hardware import get_hardware_profile

logger = logging.getLogger(__name__)

# Max concurrent models to keep loaded (conservative default, respects hardware)
MAX_CONCURRENT_MODELS = 3


class ModelManager:
    """Manages model loading/unloading lifecycle in Ollama.

    Unloading Strategy (Option B - Passive/DB-only):
    - When VRAM pressure detected, mark model as unloaded in DB (not in Ollama)
    - Ollama's own memory management evicts unused models over time
    - Pros: No downtime, no restart needed, transparent to ongoing requests
    - Cons: Less aggressive control over VRAM (relies on Ollama's eviction)
    - Fallback: If memory exhaustion persists, could escalate to Option A (restart)
    """

    def __init__(self):
        self.max_concurrent = MAX_CONCURRENT_MODELS
    
    def load_model(self, model_name: str, force: bool = False) -> bool:
        """
        Load model into Ollama if not already loaded.
        
        Algorithm:
        1. Check if already loaded in DB
        2. If yes, update last_used, return True
        3. If no:
           a. Check available VRAM vs model size
           b. While not enough space: unload_least_recently_used()
           c. Execute: ollama pull <model_name>
           d. Mark as loaded in DB
           e. Return True
        
        Args:
            model_name: Model name (e.g., "mistral:7b")
            force: If True, unload other models aggressively to load this one
        
        Returns: True if loaded successfully, False otherwise
        """
        # Check if already loaded
        loaded = OllamaLoadedModels.query.filter_by(model_name=model_name).first()
        if loaded:
            # Update last_used timestamp
            loaded.last_used = datetime.utcnow()
            db.session.commit()
            logger.debug(f"Model {model_name} already loaded, updated timestamp")
            return True
        
        # Get model metadata from registry
        model_info = OllamaModelRegistry.query.filter_by(model_name=model_name).first()
        if not model_info:
            logger.warning(f"Model {model_name} not in registry; assuming 7GB")
            model_size_gb = 7.0
        else:
            model_size_gb = model_info.size_gb
        
        # Check hardware constraints
        hw = get_hardware_profile()
        available_vram = hw['gpu_vram_mb'] or (hw['system_ram_mb'] // 2)
        needed_vram = int(model_size_gb * 1024 * 1.1)  # +10% overhead
        
        logger.info(f"Loading {model_name} ({model_size_gb}GB) — need {needed_vram}MB, have {available_vram}MB")
        
        # Unload LRU models until we have space
        while needed_vram > available_vram and not self._try_unload_lru():
            logger.warning(f"Could not free space for {model_name}")
            return False
        
        # Try to pull the model
        try:
            logger.info(f"Executing: ollama pull {model_name}")
            result = subprocess.run(
                ['ollama', 'pull', model_name],
                capture_output=True,
                timeout=300,  # 5 min timeout
            )
            
            if result.returncode != 0:
                logger.error(f"ollama pull failed: {result.stderr.decode()}")
                return False
            
            logger.info(f"Successfully pulled {model_name}")
            
            # Mark as loaded in DB
            loaded_model = OllamaLoadedModels(
                model_name=model_name,
                size_gb=model_size_gb,
                loaded_at=datetime.utcnow(),
                last_used=datetime.utcnow(),
            )
            db.session.add(loaded_model)
            db.session.commit()
            
            logger.info(f"Model {model_name} marked as loaded in DB")
            return True
        
        except subprocess.TimeoutExpired:
            logger.error(f"ollama pull {model_name} timed out")
            return False
        except Exception as e:
            logger.error(f"Error loading model {model_name}: {e}")
            return False
    
    def unload_model(self, model_name: str) -> bool:
        """
        Unload a model from Ollama.
        
        Since Ollama has no /unload endpoint, we mark it as unloaded in DB.
        The model stays in Ollama's cache but is considered "available to evict".
        
        Args:
            model_name: Model name to unload
        
        Returns: True if unload was recorded, False if model not loaded
        """
        loaded = OllamaLoadedModels.query.filter_by(model_name=model_name).first()
        if not loaded:
            logger.debug(f"Model {model_name} not in loaded models")
            return False
        
        db.session.delete(loaded)
        db.session.commit()
        
        logger.info(f"Model {model_name} marked as unloaded")
        return True
    
    def _try_unload_lru(self) -> bool:
        """
        Unload the least recently used model.
        
        Returns: True if a model was unloaded, False if no more models to unload
        """
        # Find LRU model
        lru = OllamaLoadedModels.query.order_by(OllamaLoadedModels.last_used).first()
        
        if not lru:
            logger.debug("No models loaded; cannot free space")
            return False
        
        logger.info(f"Unloading LRU model: {lru.model_name}")
        return self.unload_model(lru.model_name)
    
    def unload_all_models(self) -> int:
        """Unload all models. Returns count unloaded."""
        count = OllamaLoadedModels.query.count()
        OllamaLoadedModels.query.delete()
        db.session.commit()
        logger.info(f"Unloaded all {count} models")
        return count
    
    def get_loaded_models(self) -> List[str]:
        """Get list of currently loaded model names."""
        models = OllamaLoadedModels.query.all()
        return [m.model_name for m in models]
    
    def get_model_status(self, model_name: str) -> Optional[dict]:
        """Get detailed status of a specific model."""
        loaded = OllamaLoadedModels.query.filter_by(model_name=model_name).first()
        if not loaded:
            return None
        
        return {
            'model_name': loaded.model_name,
            'size_gb': loaded.size_gb,
            'loaded_at': loaded.loaded_at.isoformat() if loaded.loaded_at else None,
            'last_used': loaded.last_used.isoformat() if loaded.last_used else None,
        }
    
    def get_all_status(self) -> dict:
        """
        Get comprehensive status: loaded models + hardware + space used.
        
        Returns:
        {
            "loaded": ["mistral:7b", "llama2:7b"],
            "count": 2,
            "total_size_gb": 14.0,
            "hardware": {
                "gpu_vram_mb": 24000,
                "system_ram_mb": 64000,
                "available_for_models_mb": 24000,
                ...
            },
            "utilization_pct": 64.4,  # total_size / available_vram * 100
        }
        """
        loaded = OllamaLoadedModels.query.all()
        hw = get_hardware_profile()
        available_vram = hw['gpu_vram_mb'] or (hw['system_ram_mb'] // 2)
        
        total_size_gb = sum(m.size_gb for m in loaded)
        total_size_mb = int(total_size_gb * 1024)
        utilization = (total_size_mb / available_vram * 100) if available_vram > 0 else 0
        
        return {
            "loaded": [m.model_name for m in loaded],
            "count": len(loaded),
            "total_size_gb": total_size_gb,
            "hardware": hw,
            "utilization_pct": round(utilization, 1),
        }
    
    def get_loadable_models(self, use_case: str = None, max_size_gb: float = None) -> List[str]:
        """
        Get list of models that could be loaded based on hardware and use_case.
        
        This is different from get_models_filtered on OllamaClient — this checks
        what COULD be loaded given current hardware constraints.
        """
        hw = get_hardware_profile()
        available_vram = hw['gpu_vram_mb'] or (hw['system_ram_mb'] // 2)
        
        query = OllamaModelRegistry.query.filter(OllamaModelRegistry.is_loaded == True)
        
        if use_case:
            query = query.filter_by(use_case=use_case)
        
        if max_size_gb:
            max_mb = int(max_size_gb * 1024)
            query = query.filter(OllamaModelRegistry.min_vram_mb <= max_mb)
        
        # Only include models that fit current hardware
        query = query.filter(OllamaModelRegistry.min_vram_mb <= available_vram)
        
        models = query.order_by(OllamaModelRegistry.size_gb).all()
        return [m.model_name for m in models]


# Global singleton instance
_model_manager = None


def get_model_manager() -> ModelManager:
    """Get or create singleton ModelManager instance."""
    global _model_manager
    if _model_manager is None:
        _model_manager = ModelManager()
    return _model_manager
