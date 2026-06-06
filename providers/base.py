"""Basis-Schnittstelle für alle Provider-Clients.

Antwort-Format ist Claude-kompatibel (für Drop-in-Migration):

  {
    "content": [{"text": "..."}],
    "usage": {"input_tokens": N, "output_tokens": M}
  }
"""

from __future__ import annotations
from abc import ABC, abstractmethod


class BaseClient(ABC):
    timeout: int = 30

    @abstractmethod
    def get_models(self) -> list[str]:
        """Liste verfügbarer Models. Leere Liste wenn nicht erreichbar."""

    @abstractmethod
    def create_message(self, model: str, messages: list[dict], max_tokens: int = 600,
                       *, tools: list[dict] | None = None) -> dict:
        """Sende eine Chat-Completion. Format siehe Modul-Doc.

        `tools` ist optional und nur für Provider mit nativem Tool-Use-Support
        (aktuell Claude). Provider ohne Support ignorieren das Argument
        stillschweigend — der Caller bekommt eine normale Text-Antwort.
        """

    @abstractmethod
    def health(self) -> bool:
        """Schneller Erreichbarkeits-Check."""
