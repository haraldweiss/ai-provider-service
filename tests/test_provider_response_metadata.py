"""Provider-only responses must preserve tools and billing metadata."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.mark.parametrize('provider', [
    'openai', 'openrouter', 'opencode', 'zai', 'cline', 'custom', 'mammouth', 'omlx',
])
def test_tool_only_response_survives_adapter(provider, monkeypatch):
    from providers import get_client
    import openai
    import requests
    import httpx
    call = {'id': 'call_1', 'type': 'function',
            'function': {'name': 'lookup', 'arguments': '{"query":"weather"}'}}
    data = {'model': 'actual-model', 'choices': [{'message': {'content': None, 'tool_calls': [call]}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'cost': 0.123}}
    sdk = MagicMock()
    sdk.chat.completions.create.return_value = SimpleNamespace(
        model=data['model'], choices=[SimpleNamespace(message=SimpleNamespace(
            content=None, tool_calls=[SimpleNamespace(id='call_1', function=SimpleNamespace(**call['function']))]))],
        usage=SimpleNamespace(**data['usage']))
    monkeypatch.setattr(openai, 'OpenAI', lambda **kwargs: sdk)
    for module in ('openrouter', 'opencode', 'zai'):
        monkeypatch.setattr(f'providers.{module}.OpenAI', lambda **kwargs: sdk)
    response = MagicMock()
    response.json.return_value = data
    monkeypatch.setattr(requests, 'post', lambda *args, **kwargs: response)
    http = MagicMock()
    http.__enter__.return_value.post.return_value = response
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: http)
    client = get_client(provider, {'api_key': 'test-key', 'api_endpoint': 'https://example.test/v1'})
    result = client.create_message('requested-model', [], tools=[{'type': 'function'}])
    assert result['model'] == 'actual-model'
    assert result['usage']['cost_usd'] == 0.123
    assert result['tool_calls'] == [{'id': 'call_1', 'name': 'lookup', 'input': '{"query":"weather"}'}]
    assert result['stop_reason'] == 'tool_use'


def test_invalid_reported_costs_do_not_override_estimates():
    from providers.response_metadata import reported_cost
    for value in (None, -1, float('nan'), float('inf'), True, '0.1'):
        assert reported_cost({'cost': value}) == {}


def test_cache_tokens_are_counted_and_costed(app):
    from dispatcher import _execute
    from storage.models import UsageEvent
    from unittest.mock import patch
    with patch('dispatcher.get_client') as factory:
        factory.return_value.create_message.return_value = {
            'content': [{'text': 'ok'}], 'usage': {
                'input_tokens': 0, 'output_tokens': 0,
                'cache_creation_input_tokens': 1000,
                'cache_creation_1h_input_tokens': 500,
                'cache_read_input_tokens': 1000,
            }}
        _execute('u1', 'claude', 'claude-haiku-4-5', [], 100, config_override={})
    event = UsageEvent.query.one()
    assert event.input_tokens == 2000
    assert float(event.cost_usd) == pytest.approx(0.001725)
