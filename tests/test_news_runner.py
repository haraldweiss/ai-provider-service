"""Tests for the news-agent runner — verifies tool-loop convergence and termination."""
from __future__ import annotations
import os
from unittest.mock import patch, MagicMock

import pytest

os.environ.setdefault('SERVICE_TOKEN', 'test-token')
os.environ.setdefault('ENCRYPTION_KEY', 'X' * 44)
os.environ.setdefault('NEWS_AGENT_PROVIDER', 'claude')
os.environ.setdefault('NEWS_AGENT_MODEL_CLAUDE', 'claude-sonnet-4-6')
os.environ.setdefault('NEWS_AGENT_MODEL_OLLAMA', 'qwen3.6:latest')
os.environ.setdefault('NEWS_AGENT_MAX_ITERATIONS', '10')


@pytest.fixture
def app():
    from app import create_app
    from database import db
    os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
    app = create_app()
    app.config['TESTING'] = True
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_runner_strips_empty_text_blocks_from_assistant_history(app):
    """Regression: Anthropic rejects empty text blocks on subsequent turns
    with 'text content blocks must be non-empty'. When the model returns
    only tool_use (no text), the runner must NOT echo an empty text block
    back into the message history."""
    from agents.news import runner

    responses = [
        # Iter 0: tool_use only, no text
        {'result': {'content': [{'text': ''}], 'stop_reason': 'tool_use',
                    'tool_calls': [{'id': 't1', 'name': 'web_search', 'input': {'query': 'x'}}],
                    'usage': {'input_tokens': 1, 'output_tokens': 1}},
         'via': 'claude', 'model': 'm', 'fallback_used': False},
        # Iter 1: end_turn
        {'result': {'content': [{'text': 'done'}], 'stop_reason': 'end_turn',
                    'tool_calls': [],
                    'usage': {'input_tokens': 1, 'output_tokens': 1}},
         'via': 'claude', 'model': 'm', 'fallback_used': False},
    ]
    captured_messages: list = []

    def _capture(**kwargs):
        captured_messages.append([dict(m) for m in kwargs['messages']])
        return responses[len(captured_messages) - 1]

    with patch('agents.news.runner.dispatch', side_effect=_capture), \
         patch('agents.news.runner.execute_tool', return_value=[]):
        runner.run_news_agent()

    # Second dispatch call must NOT contain an empty-text block in the
    # assistant message from iter 0.
    assistant_msg = [m for m in captured_messages[1] if m['role'] == 'assistant'][0]
    text_blocks = [b for b in assistant_msg['content'] if b.get('type') == 'text']
    for b in text_blocks:
        assert b['text'], f"empty text block leaked into assistant history: {assistant_msg}"


def test_runner_loop_converges_with_one_tool_call(app):
    """LLM does one web_search, then ends with publish_to_wordpress, then end_turn."""
    from agents.news import runner

    responses = [
        {'result': {'content': [{'text': ''}], 'stop_reason': 'tool_use',
                    'tool_calls': [{'id': 't1', 'name': 'web_search', 'input': {'query': 'x'}}],
                    'usage': {'input_tokens': 1, 'output_tokens': 1}},
         'via': 'claude', 'model': 'claude-sonnet-4-6', 'fallback_used': False},
        {'result': {'content': [{'text': ''}], 'stop_reason': 'tool_use',
                    'tool_calls': [{'id': 't2', 'name': 'publish_to_wordpress',
                                    'input': {'title': 'T', 'body_html': '<p>x</p>'}}],
                    'usage': {'input_tokens': 1, 'output_tokens': 1}},
         'via': 'claude', 'model': 'claude-sonnet-4-6', 'fallback_used': False},
        {'result': {'content': [{'text': 'Done.'}], 'stop_reason': 'end_turn',
                    'tool_calls': [],
                    'usage': {'input_tokens': 1, 'output_tokens': 1}},
         'via': 'claude', 'model': 'claude-sonnet-4-6', 'fallback_used': False},
    ]
    dispatch_mock = MagicMock(side_effect=responses)
    exec_mock = MagicMock(side_effect=[
        [{'title': 'h', 'url': 'http://x', 'snippet': 's'}],   # web_search result
        {'post_id': 42, 'url': 'http://x/42'},                  # publish result
    ])

    with patch('agents.news.runner.dispatch', dispatch_mock), \
         patch('agents.news.runner.execute_tool', exec_mock):
        summary = runner.run_news_agent()

    assert summary['iterations'] == 2
    assert summary['final_stop_reason'] == 'end_turn'
    assert dispatch_mock.call_count == 3
    assert exec_mock.call_count == 2
    assert exec_mock.call_args_list[0].args[0] == 'web_search'
    assert exec_mock.call_args_list[1].args[0] == 'publish_to_wordpress'


def test_runner_raises_on_divergence(app):
    """Loop must terminate via MAX_ITERATIONS if model keeps calling tools."""
    from agents.news import runner

    # Always return tool_use → never ends
    response = {'result': {'content': [{'text': ''}], 'stop_reason': 'tool_use',
                           'tool_calls': [{'id': 't', 'name': 'web_search', 'input': {'query': 'x'}}],
                           'usage': {'input_tokens': 1, 'output_tokens': 1}},
                'via': 'claude', 'model': 'm', 'fallback_used': False}

    with patch('agents.news.runner.dispatch', return_value=response), \
         patch('agents.news.runner.execute_tool', return_value=[]), \
         patch.dict(os.environ, {'NEWS_AGENT_MAX_ITERATIONS': '3'}):
        with pytest.raises(RuntimeError, match='did not converge'):
            runner.run_news_agent()


def test_runner_passes_fallback_when_configured(app):
    """When NEWS_AGENT_FALLBACK is set, dispatch must receive fallback_provider_override."""
    from agents.news import runner

    response = {'result': {'content': [{'text': 'Done'}], 'stop_reason': 'end_turn',
                           'tool_calls': [],
                           'usage': {'input_tokens': 1, 'output_tokens': 1}},
                'via': 'claude', 'model': 'm', 'fallback_used': False}
    dispatch_mock = MagicMock(return_value=response)

    with patch('agents.news.runner.dispatch', dispatch_mock), \
         patch.dict(os.environ, {'NEWS_AGENT_FALLBACK': 'ollama'}):
        runner.run_news_agent()

    assert dispatch_mock.call_args.kwargs['fallback_provider_override'] == 'ollama'
    assert dispatch_mock.call_args.kwargs['fallback_model_override'] == 'qwen3.6:latest'
