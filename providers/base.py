"""Basis-Schnittstelle für alle Provider-Clients.

Antwort-Format ist Claude-kompatibel (für Drop-in-Migration):

  {
    "content": [{"text": "..."}],
    "stop_reason": "end_turn" | "tool_use",   # NEW: present when tools= was passed
    "tool_calls": [                            # NEW: only when stop_reason == "tool_use"
      {"id": "...", "name": "...", "input": {...}},
      ...
    ],
    "usage": {"input_tokens": N, "output_tokens": M}
  }

Backward-compat: when `tools` is None or omitted, `stop_reason`/`tool_calls` MAY be
absent (existing callers ignore them).
"""

from __future__ import annotations
from abc import ABC, abstractmethod


class BaseClient(ABC):
    timeout: int = 30

    @abstractmethod
    def get_models(self) -> list[str]:
        """Liste verfügbarer Models. Leere Liste wenn nicht erreichbar."""

    @abstractmethod
    def create_message(
        self,
        model: str,
        messages: list[dict],
        max_tokens: int = 600,
        tools: list[dict] | None = None,
    ) -> dict:
        """Sende eine Chat-Completion. Format siehe Modul-Doc."""

    @abstractmethod
    def health(self) -> bool:
        """Schneller Erreichbarkeits-Check."""
