"""dispatcher.dispatch() and _execute() forward the `tools` kwarg to the
provider client. The news-agent runner relies on this for native tool-use."""

from unittest.mock import patch, MagicMock


def _build_messages():
    return [{'role': 'user', 'content': 'hi'}]


_FAKE_RESPONSE = {'content': 'ok', 'usage': {'input_tokens': 1, 'output_tokens': 1}}


def test_dispatch_forwards_tools_to_client(app):
    """When tools=[…] is passed to dispatch, the underlying provider client
    must receive it as a kwarg on create_message."""
    sample_tools = [
        {'name': 'publish_to_wordpress', 'description': 'publish a draft',
         'input_schema': {'type': 'object'}},
    ]
    with app.app_context():
        with patch('dispatcher.get_client') as gc:
            client = MagicMock()
            client.create_message.return_value = _FAKE_RESPONSE
            gc.return_value = client
            with patch('dispatcher._load_config', return_value={}):
                with patch('dispatcher.health_tracker.is_healthy', return_value=True):
                    with patch('dispatcher.health_tracker.set_status'):
                        from dispatcher import dispatch
                        dispatch(
                            user_id='news-agent', provider_id='claude',
                            model='claude-haiku', messages=_build_messages(),
                            max_tokens=100, tools=sample_tools,
                            origin_app='news-agent',
                        )
        # The client must have been called with tools=sample_tools.
        _, kwargs = client.create_message.call_args
        assert kwargs.get('tools') == sample_tools, (
            f'client.create_message was called with kwargs={kwargs}; '
            f'tools should equal {sample_tools}'
        )


def test_dispatch_without_tools_passes_none(app):
    """Existing callers that do not pass `tools` must continue to work —
    client.create_message receives tools=None (or not at all)."""
    with app.app_context():
        with patch('dispatcher.get_client') as gc:
            client = MagicMock()
            client.create_message.return_value = _FAKE_RESPONSE
            gc.return_value = client
            with patch('dispatcher._load_config', return_value={}):
                with patch('dispatcher.health_tracker.is_healthy', return_value=True):
                    with patch('dispatcher.health_tracker.set_status'):
                        from dispatcher import dispatch
                        dispatch(
                            user_id='u', provider_id='claude', model='m',
                            messages=_build_messages(), max_tokens=10,
                        )
        _, kwargs = client.create_message.call_args
        assert kwargs.get('tools') is None, (
            f'expected tools=None for the tools-less call path; got {kwargs}'
        )


def test_fallback_path_also_forwards_tools(app):
    """When primary fails and dispatch falls back to the fallback provider,
    `tools` must still propagate to the fallback client."""
    sample_tools = [{'name': 't', 'input_schema': {'type': 'object'}}]
    with app.app_context():
        with patch('dispatcher.get_client') as gc:
            primary_client = MagicMock()
            primary_client.create_message.side_effect = RuntimeError('primary down')
            fallback_client = MagicMock()
            fallback_client.create_message.return_value = _FAKE_RESPONSE

            def client_factory(provider_id, cfg):
                return primary_client if provider_id == 'claude' else fallback_client
            gc.side_effect = client_factory

            with patch('dispatcher._load_config', return_value={}):
                with patch('dispatcher.health_tracker.is_healthy', return_value=True):
                    with patch('dispatcher.health_tracker.set_status'):
                        from dispatcher import dispatch
                        dispatch(
                            user_id='u', provider_id='claude', model='m',
                            messages=_build_messages(), max_tokens=10,
                            tools=sample_tools,
                            fallback_provider_override='ollama',
                            fallback_model_override='mistral',
                            fallback_config_override={},
                        )
        _, fb_kwargs = fallback_client.create_message.call_args
        assert fb_kwargs.get('tools') == sample_tools


def test_claude_provider_passes_tools_to_anthropic_sdk():
    """Unit test on the Claude provider directly: tools is forwarded into
    the Anthropic SDK call kwargs."""
    from providers.claude import ClaudeClient
    sample_tools = [{'name': 'x', 'input_schema': {'type': 'object'}}]
    client = ClaudeClient({'api_key': 'fake-key'})
    with patch.object(client.client.messages, 'create') as mock_create:
        # Anthropic SDK returns a Message with content, usage, stop_reason
        m = MagicMock()
        m.content = []
        m.usage = MagicMock(input_tokens=0, output_tokens=0,
                             cache_creation_input_tokens=0,
                             cache_read_input_tokens=0)
        m.stop_reason = 'end_turn'
        mock_create.return_value = m
        client.create_message(
            'claude-haiku-4-5-20251001',
            [{'role': 'user', 'content': 'hi'}],
            100,
            tools=sample_tools,
        )
        _, sdk_kwargs = mock_create.call_args
        assert sdk_kwargs.get('tools') == sample_tools


def test_claude_provider_response_includes_stop_reason_and_tool_calls():
    """Phase 2: Claude's response shape must expose stop_reason and tool_calls
    so the news-agent runner can drive a tool-use loop."""
    from providers.claude import ClaudeClient

    class FakeText:
        type = 'text'
        text = 'thinking...'

    class FakeToolUse:
        type = 'tool_use'
        id = 'tool_abc'
        name = 'publish_to_wordpress'
        input = {'title': 'demo', 'body': '...'}

    client = ClaudeClient({'api_key': 'fake-key'})
    with patch.object(client.client.messages, 'create') as mock_create:
        m = MagicMock()
        m.content = [FakeText(), FakeToolUse()]
        m.usage = MagicMock(input_tokens=10, output_tokens=20,
                             cache_creation_input_tokens=0,
                             cache_read_input_tokens=0)
        m.stop_reason = 'tool_use'
        mock_create.return_value = m
        result = client.create_message(
            'claude-haiku-4-5-20251001',
            [{'role': 'user', 'content': 'hi'}],
            100,
            tools=[{'name': 'publish_to_wordpress', 'input_schema': {'type': 'object'}}],
        )

    assert result['stop_reason'] == 'tool_use'
    # content: at least the text block survives; tool_use also represented
    assert any(b.get('type') == 'text' and b.get('text') == 'thinking...'
               for b in result['content'])
    assert any(b.get('type') == 'tool_use' and b.get('id') == 'tool_abc'
               for b in result['content'])
    # tool_calls convenience list mirrors tool_use blocks
    assert result['tool_calls'] == [
        {'id': 'tool_abc', 'name': 'publish_to_wordpress',
         'input': {'title': 'demo', 'body': '...'}},
    ]


def test_claude_provider_text_only_response_backward_compatible():
    """When no tools are used the response still has content[0].text so
    existing chat-API callers keep working."""
    from providers.claude import ClaudeClient

    class FakeText:
        type = 'text'
        text = 'hello world'

    client = ClaudeClient({'api_key': 'fake-key'})
    with patch.object(client.client.messages, 'create') as mock_create:
        m = MagicMock()
        m.content = [FakeText()]
        m.usage = MagicMock(input_tokens=1, output_tokens=2,
                             cache_creation_input_tokens=0,
                             cache_read_input_tokens=0)
        m.stop_reason = 'end_turn'
        mock_create.return_value = m
        result = client.create_message(
            'claude-haiku-4-5-20251001',
            [{'role': 'user', 'content': 'hi'}],
            100,
        )

    # Legacy callers do result['content'][0]['text'] — must still work.
    assert result['content'][0]['text'] == 'hello world'
    assert result['stop_reason'] == 'end_turn'
    assert result['tool_calls'] == []
