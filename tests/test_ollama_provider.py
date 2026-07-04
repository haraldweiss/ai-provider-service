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


def test_create_message_forwards_tools_and_maps_tool_calls(monkeypatch):
    from providers.ollama import OllamaClient

    client = OllamaClient({'api_endpoint': 'http://ollama.test'})
    tools = [{
        'type': 'function',
        'function': {
            'name': 'read_file',
            'parameters': {'type': 'object'},
        },
    }]

    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        'message': {
            'content': '',
            'tool_calls': [{
                'function': {
                    'name': 'read_file',
                    'arguments': {'path': '/tmp/example.txt'},
                },
            }],
        },
        'prompt_eval_count': 12,
        'eval_count': 4,
        'done_reason': 'stop',
    }
    post = Mock(return_value=response)
    monkeypatch.setattr('providers.ollama.requests.post', post)

    result = client.create_message(
        'ornith:latest',
        [{'role': 'user', 'content': 'read it'}],
        max_tokens=123,
        tools=tools,
    )

    payload = post.call_args.kwargs['json']
    assert payload['tools'] == tools
    assert result['tool_calls'] == [{
        'id': 'call_0',
        'name': 'read_file',
        'input': {'path': '/tmp/example.txt'},
    }]
    assert result['stop_reason'] == 'tool_use'


def test_create_message_maps_dsml_tool_call_text_when_tool_was_offered(monkeypatch):
    from providers.ollama import OllamaClient

    client = OllamaClient({'api_endpoint': 'http://ollama.test'})
    tools = [{
        'type': 'function',
        'function': {
            'name': 'ctx_batch_execute',
            'parameters': {'type': 'object'},
        },
    }]
    dsml = (
        '<｜｜DSML｜｜tool_calls>\n'
        '<｜｜DSML｜｜invoke name="ctx_batch_execute">\n'
        '<｜｜DSML｜｜parameter name="cmds" string="false">'
        '[{"cmd": "find /tmp -type f"}]'
        '</｜｜DSML｜｜parameter>\n'
        '</｜｜DSML｜｜invoke>\n'
        '</｜｜DSML｜｜tool_calls>'
    )

    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        'message': {'content': dsml},
        'prompt_eval_count': 12,
        'eval_count': 4,
        'done_reason': 'stop',
    }
    monkeypatch.setattr('providers.ollama.requests.post', Mock(return_value=response))

    result = client.create_message(
        'ornith:latest',
        [{'role': 'user', 'content': 'scan it'}],
        max_tokens=123,
        tools=tools,
    )

    assert result['content'] == [{'text': ''}]
    assert result['tool_calls'] == [{
        'id': 'call_0',
        'name': 'ctx_batch_execute',
        'input': {'cmds': [{'cmd': 'find /tmp -type f'}]},
    }]
    assert result['stop_reason'] == 'tool_use'


def test_create_message_leaves_dsml_text_when_tool_was_not_offered(monkeypatch):
    from providers.ollama import OllamaClient

    client = OllamaClient({'api_endpoint': 'http://ollama.test'})
    dsml = (
        '<｜｜DSML｜｜tool_calls>'
        '<｜｜DSML｜｜invoke name="ctx_batch_execute">'
        '<｜｜DSML｜｜parameter name="cmds" string="false">[]'
        '</｜｜DSML｜｜parameter>'
        '</｜｜DSML｜｜invoke>'
        '</｜｜DSML｜｜tool_calls>'
    )

    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        'message': {'content': dsml},
        'prompt_eval_count': 12,
        'eval_count': 4,
        'done_reason': 'stop',
    }
    monkeypatch.setattr('providers.ollama.requests.post', Mock(return_value=response))

    result = client.create_message(
        'ornith:latest',
        [{'role': 'user', 'content': 'scan it'}],
        max_tokens=123,
        tools=[{'type': 'function', 'function': {'name': 'other_tool'}}],
    )

    assert result['content'] == [{'text': dsml}]
    assert result['tool_calls'] == []
    assert result['stop_reason'] == 'stop'
