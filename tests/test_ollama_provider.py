"""Tests for local Ollama provider response mapping."""

from unittest.mock import Mock


def test_create_message_exposes_length_done_reason(monkeypatch):
    from providers.ollama import OllamaClient

    client = OllamaClient({'api_endpoint': 'http://ollama.test'})

    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        'message': {'content': 'partial answer'},
        'prompt_eval_count': 12,
        'eval_count': 123,
        'done_reason': 'length',
    }

    monkeypatch.setattr('providers.ollama.requests.post', Mock(return_value=response))

    result = client.create_message(
        'ornith:latest',
        [{'role': 'user', 'content': 'ping'}],
        max_tokens=123,
    )

    assert result['content'] == [{'text': 'partial answer'}]
    assert result['usage']['output_tokens'] == 123
    assert result['stop_reason'] == 'length'
