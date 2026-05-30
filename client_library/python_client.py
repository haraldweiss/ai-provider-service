"""Lightweight Python client for remote ai-provider-service."""

import os
import requests
from typing import Optional, List, Dict, Any


class AIProviderClient:
    """Client for connecting to a remote ai-provider-service instance.

    Minimal dependencies: only requires `requests`.

    Usage:
        from client_library import AIProviderClient

        client = AIProviderClient(service_url='http://localhost:8767')
        response = client.chat(
            messages=[{"role": "user", "content": "Hello"}],
            provider="ollama",
            model="mistral:7b"
        )
        print(response['result']['content'])
    """

    def __init__(self, service_url: str = "http://localhost:8767", token: Optional[str] = None):
        """Initialize client.

        Args:
            service_url: Base URL of ai-provider-service (default: localhost:8767)
            token: Auth token (default: read from SERVICE_TOKEN env var)
        """
        self.service_url = service_url.rstrip('/')
        self.token = token or os.getenv('SERVICE_TOKEN')

        if not self.token:
            raise ValueError(
                "SERVICE_TOKEN not provided and not in environment. "
                "Set SERVICE_TOKEN env var or pass token= to __init__()"
            )

    def _headers(self) -> dict:
        """Return request headers with auth token."""
        return {"Authorization": f"Bearer {self.token}"}

    def chat(
        self,
        messages: List[Dict[str, str]],
        provider: str = "ollama",
        model: str = "mistral:7b",
        max_tokens: int = 600,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send a chat request to the service.

        Model auto-loads if needed (transparent to caller).

        Args:
            messages: List of message dicts with 'role' and 'content'
            provider: Provider ID (default: ollama)
            model: Model name (default: mistral:7b)
            max_tokens: Max output tokens (default: 600)
            user_id: User identifier (default: 'anonymous')

        Returns:
            Response dict with 'result', 'via', 'fallback_used' keys

        Raises:
            requests.RequestException: If request fails
            ValueError: If response is invalid
        """
        if not user_id:
            user_id = "anonymous"

        payload = {
            "user_id": user_id,
            "provider": provider,
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
        }

        response = requests.post(
            f"{self.service_url}/chat",
            json=payload,
            headers=self._headers(),
            timeout=300  # 5 min timeout for long-running models
        )
        response.raise_for_status()
        return response.json()

    def load_model(self, model_name: str, force: bool = False) -> Dict[str, Any]:
        """Explicitly load a model into Ollama memory.

        Useful for pre-warming before requests or freeing VRAM from old models.

        Args:
            model_name: Model name (e.g., 'mistral:7b')
            force: Force reload even if already loaded

        Returns:
            Dict with 'loaded', 'model_name', 'status' keys

        Raises:
            requests.RequestException: If request fails
        """
        payload = {"model_name": model_name, "force": force}

        response = requests.post(
            f"{self.service_url}/models/load",
            json=payload,
            headers=self._headers(),
            timeout=300
        )
        response.raise_for_status()
        return response.json()

    def unload_model(self, model_name: str) -> Dict[str, Any]:
        """Explicitly unload a model to free VRAM.

        Args:
            model_name: Model name to unload

        Returns:
            Dict with 'unloaded', 'model_name', 'status' keys

        Raises:
            requests.RequestException: If request fails
        """
        payload = {"model_name": model_name}

        response = requests.post(
            f"{self.service_url}/models/unload",
            json=payload,
            headers=self._headers(),
            timeout=10
        )
        response.raise_for_status()
        return response.json()

    def get_status(self) -> Dict[str, Any]:
        """Check what models are currently loaded and hardware status.

        Returns:
            Dict with:
            - 'loaded': List of loaded model names
            - 'count': Number of loaded models
            - 'total_size_gb': Combined size of loaded models
            - 'hardware': Dict with gpu_vram_mb, system_ram_mb, gpu_type, cpu_cores
            - 'utilization_pct': VRAM utilization percentage
            - 'models': List of model detail dicts (name, size_gb, loaded_at, last_used)

        Raises:
            requests.RequestException: If request fails
        """
        response = requests.get(
            f"{self.service_url}/models/status",
            headers=self._headers(),
            timeout=10
        )
        response.raise_for_status()
        return response.json()

    def unload_all(self) -> Dict[str, Any]:
        """Unload all models to reclaim VRAM.

        Returns:
            Dict with 'unloaded_count', 'models', 'status' keys

        Raises:
            requests.RequestException: If request fails
        """
        response = requests.post(
            f"{self.service_url}/models/unload-all",
            json={},
            headers=self._headers(),
            timeout=60
        )
        response.raise_for_status()
        return response.json()

    def list_loadable_models(
        self,
        use_case: Optional[str] = None,
        max_size_gb: Optional[float] = None
    ) -> Dict[str, Any]:
        """List models that fit current hardware and match optional filters.

        Args:
            use_case: Optional filter (e.g., 'reasoning', 'vision', 'chat', 'embedding')
            max_size_gb: Optional max model size in GB

        Returns:
            Dict with 'models' list (model names sorted by size)

        Raises:
            requests.RequestException: If request fails
        """
        params = {}
        if use_case:
            params["use_case"] = use_case
        if max_size_gb:
            params["max_size_gb"] = max_size_gb

        response = requests.get(
            f"{self.service_url}/models/available",
            params=params,
            headers=self._headers(),
            timeout=10
        )
        response.raise_for_status()
        return response.json()
