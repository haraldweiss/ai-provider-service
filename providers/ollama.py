"""Ollama Client (lokal, /api/tags + /api/chat)."""

from __future__ import annotations
import logging
import requests
from providers.base import BaseClient
from config import Config

logger = logging.getLogger(__name__)


class OllamaClient(BaseClient):
    timeout = 180  # lokale Models können sehr lange brauchen (Cold-Start, große Modelle)

    def __init__(self, config: dict):
        # Wenn config keine URL hat → fallback auf system-default (OLLAMA_URL).
        # Ollama läuft typischerweise lokal, daher meist 127.0.0.1:11434.
        url = (config or {}).get('api_endpoint') or Config.OLLAMA_URL
        self.base_url = url.rstrip('/')

    def get_models(self) -> list[str]:
        try:
            r = requests.get(f'{self.base_url}/api/tags', timeout=5)
            r.raise_for_status()
            data = r.json()
            return [m['name'] for m in data.get('models', [])]
        except Exception as e:
            logger.warning(f'Ollama get_models failed: {e}')
            return []

    def create_message(self, model: str, messages: list[dict], max_tokens: int = 600) -> dict:
        # num_ctx: Ollama default ist 2048 — viel zu klein für CV+Job-Prompts.
        # Wenn der Prompt das Window überschreitet, schneidet Ollama still ab,
        # die Anweisung "Antworte mit JSON" geht verloren und das Modell
        # generiert nichts → leerer Output.
        # Schätze grob auf Basis der Prompt-Länge und runde auf eine Power-of-2,
        # mit Mindestwert 8192 für CV-Match-Prompts.
        try:
            char_count = sum(len(m.get('content', '') or '') for m in messages)
        except Exception:
            char_count = 0
        # 1 Token ≈ 4 Chars (deutsch) → +max_tokens für die Antwort + Puffer
        needed = max(8192, int(char_count / 3) + max_tokens + 1024)
        # Auf nächste Power-of-2 runden
        num_ctx = 1
        while num_ctx < needed:
            num_ctx *= 2

        payload = {
            'model': model,
            'messages': messages,
            'stream': False,
            'options': {
                'num_predict': max_tokens,
                'num_ctx': num_ctx,
            },
        }
        r = requests.post(f'{self.base_url}/api/chat', json=payload, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        # eval_count = output tokens, prompt_eval_count = input tokens (wenn verfügbar).
        # Wenn das Modell nichts generiert hat (eval_count=0), loggen wir die done_reason
        # damit man später debuggen kann (length, stop, load, etc.).
        text = data.get('message', {}).get('content', '')
        out_tokens = data.get('eval_count', 0)
        if out_tokens == 0 or not text:
            logger.warning(
                f'Ollama returned empty output: done_reason={data.get("done_reason")}, '
                f'eval_count={out_tokens}, prompt_eval_count={data.get("prompt_eval_count")}, '
                f'num_ctx={num_ctx}, model={model}'
            )
        return {
            'content': [{'text': text}],
            'usage': {
                'input_tokens': data.get('prompt_eval_count', 0),
                'output_tokens': out_tokens,
            }
        }

    def health(self) -> bool:
        try:
            r = requests.get(f'{self.base_url}/api/tags', timeout=3)
            return r.ok
        except Exception:
            return False


    def get_models_filtered(self, use_case: str = None, max_size_gb: float = None) -> list[str]:
        """
        Return available models filtered by capabilities and hardware constraints.
        
        Args:
            use_case: Optional filter ("reasoning", "vision", "chat", "embedding")
            max_size_gb: Optional max model size in GB
        
        Returns: List of model names sorted by size (smallest first).
                 Only includes models that are currently loaded and fit hardware.
        """
        from storage.models import OllamaModelRegistry
        from providers.hardware import get_hardware_profile
        
        hw = get_hardware_profile()
        available_vram = hw['gpu_vram_mb'] or (hw['system_ram_mb'] // 2)
        
        query = OllamaModelRegistry.query.filter(OllamaModelRegistry.is_loaded == True)
        
        if use_case:
            query = query.filter(OllamaModelRegistry.use_case == use_case)
        
        if max_size_gb:
            max_mb = int(max_size_gb * 1024)
            query = query.filter(OllamaModelRegistry.min_vram_mb <= max_mb)
        else:
            # Only models that fit current hardware
            query = query.filter(OllamaModelRegistry.min_vram_mb <= available_vram)
        
        models = query.order_by(OllamaModelRegistry.size_gb).all()
        return [m.model_name for m in models]

    def get_model_with_fallback(self, requested_model: str, use_case: str = None) -> str:
        """
        Return the requested model if available and fits hardware.
        Otherwise, fallback to the largest model that fits.
        
        Args:
            requested_model: Model name to try first
            use_case: Optional use-case filter for fallback
        
        Returns: Model name to use (either requested or fallback)
        
        Raises ValueError if no suitable model found.
        """
        from storage.models import OllamaModelRegistry
        from providers.hardware import get_hardware_profile
        
        hw = get_hardware_profile()
        available_vram = hw['gpu_vram_mb'] or (hw['system_ram_mb'] // 2)
        
        # Check if requested model exists and is loaded
        model = OllamaModelRegistry.query.filter_by(
            model_name=requested_model,
            is_loaded=True
        ).first()
        
        if model and model.min_vram_mb <= available_vram:
            return requested_model
        
        # Fallback: get largest model that fits (prefer same use-case)
        if not use_case and model:
            use_case = model.use_case
        
        fallback_query = OllamaModelRegistry.query.filter(
            OllamaModelRegistry.is_loaded == True,
            OllamaModelRegistry.min_vram_mb <= available_vram
        )
        
        if use_case:
            fallback_query = fallback_query.filter(OllamaModelRegistry.use_case == use_case)
        
        fallback = fallback_query.order_by(OllamaModelRegistry.size_gb.desc()).first()
        
        if fallback:
            logger.warning(
                f"Model {requested_model} unavailable or too large ({available_vram}MB VRAM). "
                f"Falling back to {fallback.model_name} ({fallback.size_gb}GB)"
            )
            return fallback.model_name
        
        raise ValueError(
            f"No suitable model found for {requested_model} "
            f"(available VRAM: {available_vram}MB)"
        )
