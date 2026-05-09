"""Basis-Schnittstelle für alle Provider-Clients.

Antwort-Format ist Claude-kompatibel (für Drop-in-Migration):

  {
    "content": [{"text": "..."}],
    "usage": {"input_tokens": N, "output_tokens": M}
  }
"""

from abc import ABC, abstractmethod


class BaseClient(ABC):
    timeout: int = 30

    @abstractmethod
    def get_models(self) -> list[str]:
        """Liste verfügbarer Models. Leere Liste wenn nicht erreichbar."""

    @abstractmethod
    def create_message(self, model: str, messages: list[dict], max_tokens: int = 600) -> dict:
        """Sende eine Chat-Completion. Format siehe Modul-Doc."""

    @abstractmethod
    def health(self) -> bool:
        """Schneller Erreichbarkeits-Check."""
