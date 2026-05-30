"""DB-Modelle: ProviderConfig (per User+Provider) und RequestQueue."""

import json as _json
import uuid
from datetime import datetime
from database import db


class ProviderConfig(db.Model):
    """Provider-Konfiguration pro user_id + provider_id.

    `user_id` ist ein freier String — Konsumenten-Apps entscheiden, was sie
    da reinschreiben (UUID, Username, App-Konstante).

    `config_encrypted` enthält ein JSON-Objekt mit allen Provider-Feldern,
    Fernet-verschlüsselt. Beispiel-Inhalt vor Encryption:

      {
        "api_key": "sk-...",
        "api_endpoint": "https://...",
        "organization_id": "org-...",
        "name": "LM Studio Local"
      }

    `fallback_provider`: Provider-ID, an die im Fehlerfall delegiert wird.
    `queue_when_unavailable`: Falls Primary down + kein Fallback → Request
    landet in der Queue, statt zu failen.
    """
    __tablename__ = 'provider_configs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(255), nullable=False, index=True)
    provider_id = db.Column(db.String(32), nullable=False)
    config_encrypted = db.Column(db.Text, nullable=False)
    fallback_provider = db.Column(db.String(32), nullable=True)
    queue_when_unavailable = db.Column(db.Boolean, default=True, nullable=False)
    queue_ttl_hours = db.Column(db.Integer, default=24, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'provider_id', name='uq_user_provider'),
    )

    def to_safe_dict(self) -> dict:
        """Repräsentation ohne sensible Felder (kein api_key)."""
        from storage.encryption import decrypt
        try:
            cfg = _json.loads(decrypt(self.config_encrypted))
        except Exception:
            cfg = {}
        # API-Keys nie zurückgeben
        safe = {
            'user_id': self.user_id,
            'provider_id': self.provider_id,
            'configured': True,
            'fallback_provider': self.fallback_provider,
            'queue_when_unavailable': self.queue_when_unavailable,
            'queue_ttl_hours': self.queue_ttl_hours,
            'has_api_key': bool(cfg.get('api_key')),
        }
        for key in ('api_endpoint', 'organization_id', 'name'):
            if cfg.get(key):
                safe[key] = cfg[key]
        return safe

    def get_config(self) -> dict:
        """Entschlüsselt das Config-JSON. Nur intern verwenden."""
        from storage.encryption import decrypt
        return _json.loads(decrypt(self.config_encrypted))

    def set_config(self, config_dict: dict) -> None:
        from storage.encryption import encrypt
        self.config_encrypted = encrypt(_json.dumps(config_dict))


class RequestQueue(db.Model):
    """Queue für Requests an temporär nicht erreichbare Provider.

    `payload`: Original-Request als JSON (provider, model, messages, max_tokens).
    `status`: pending | processing | done | failed | expired
    `result`: Antwort vom Provider (bei status=done) oder Fehler.
    """
    __tablename__ = 'request_queue'

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.String(255), nullable=False, index=True)
    primary_provider = db.Column(db.String(32), nullable=False, index=True)
    payload = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(16), default='pending', nullable=False, index=True)
    attempts = db.Column(db.Integer, default=0, nullable=False)
    last_error = db.Column(db.Text, nullable=True)
    result = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self, include_result: bool = True) -> dict:
        d = {
            'id': self.id,
            'user_id': self.user_id,
            'primary_provider': self.primary_provider,
            'status': self.status,
            'attempts': self.attempts,
            'last_error': self.last_error,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
        }
        if include_result and self.result:
            try:
                d['result'] = _json.loads(self.result)
            except Exception:
                d['result'] = None
        return d

    def get_payload(self) -> dict:
        return _json.loads(self.payload)


class OllamaModelRegistry(db.Model):
    """Catalog of Ollama models from ollama.com/search + local metadata.
    
    Synced daily. Tracks model name, size, capabilities, and whether 
    it's currently loaded in the local Ollama instance.
    """
    __tablename__ = 'ollama_models_registry'
    
    id = db.Column(db.Integer, primary_key=True)
    model_name = db.Column(db.String(128), unique=True, nullable=False, index=True)
    
    # Metadata from ollama.com/search
    size_gb = db.Column(db.Float, nullable=False)
    use_case = db.Column(db.String(32), nullable=False)  # "chat", "reasoning", "embedding", "vision"
    is_multimodal = db.Column(db.Boolean, default=False)
    description = db.Column(db.Text, nullable=True)
    pull_url = db.Column(db.String(255), nullable=False)
    
    # Local state
    is_loaded = db.Column(db.Boolean, default=False, nullable=False)
    loaded_at = db.Column(db.DateTime, nullable=True)
    last_sync = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Hardware requirements (inferred from size_gb + use_case)
    min_vram_mb = db.Column(db.Integer, nullable=False)
    min_ram_mb = db.Column(db.Integer, nullable=False)
    
    __table_args__ = (
        db.Index('idx_use_case_size', 'use_case', 'size_gb'),
        db.Index('idx_loaded', 'is_loaded'),
    )
    
    def to_dict(self) -> dict:
        """Representation for API responses."""
        return {
            'model_name': self.model_name,
            'size_gb': self.size_gb,
            'use_case': self.use_case,
            'is_multimodal': self.is_multimodal,
            'description': self.description,
            'is_loaded': self.is_loaded,
            'min_vram_mb': self.min_vram_mb,
            'min_ram_mb': self.min_ram_mb,
        }


class OllamaLoadedModels(db.Model):
    """Track which models are currently loaded in Ollama memory.
    
    This is different from OllamaModelRegistry:
    - Registry: All discovered models from ollama.com/search (loaded=False by default)
    - LoadedModels: Models currently in Ollama memory, with last_used tracking
    """
    __tablename__ = 'ollama_loaded_models'
    
    id = db.Column(db.Integer, primary_key=True)
    model_name = db.Column(db.String(128), unique=True, nullable=False, index=True)
    size_gb = db.Column(db.Float, nullable=False)
    loaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_used = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)
    
    def to_dict(self) -> dict:
        return {
            'model_name': self.model_name,
            'size_gb': self.size_gb,
            'loaded_at': self.loaded_at.isoformat() if self.loaded_at else None,
            'last_used': self.last_used.isoformat() if self.last_used else None,
        }
