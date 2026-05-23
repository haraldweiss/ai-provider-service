"""Tests for BaseClient interface — verifies the tools parameter exists on the contract."""
from __future__ import annotations
import inspect

from providers.base import BaseClient


def test_create_message_signature_accepts_tools_param():
    sig = inspect.signature(BaseClient.create_message)
    assert 'tools' in sig.parameters, "BaseClient.create_message must accept a 'tools' kwarg"
    assert sig.parameters['tools'].default is None, "tools must default to None for backward compat"
