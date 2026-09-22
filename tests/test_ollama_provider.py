"""Tests for local Ollama provider response mapping."""

from unittest.mock import Mock
import requests


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


def test_create_message_maps_json_text_tool_call_when_tool_was_offered(monkeypatch):
    from providers.ollama import OllamaClient

    client = OllamaClient({'api_endpoint': 'http://ollama.test'})
    tools = [{
        'type': 'function',
        'function': {
            'name': 'get_weather',
            'parameters': {'type': 'object'},
        },
    }]

    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        'message': {
            'content': '{"name":"get_weather","arguments":{"city":"Berlin"}}',
        },
        'prompt_eval_count': 12,
        'eval_count': 8,
        'done_reason': 'stop',
    }
    monkeypatch.setattr('providers.ollama.requests.post', Mock(return_value=response))

    result = client.create_message(
        'dev-coder:latest',
        [{'role': 'user', 'content': 'use weather'}],
        max_tokens=123,
        tools=tools,
    )

    assert result['content'] == [{'text': ''}]
    assert result['tool_calls'] == [{
        'id': 'call_0',
        'name': 'get_weather',
        'input': {'city': 'Berlin'},
    }]
    assert result['stop_reason'] == 'tool_use'


def test_create_message_maps_xml_text_tool_call_when_tool_was_offered(monkeypatch):
    from providers.ollama import OllamaClient

    client = OllamaClient({'api_endpoint': 'http://ollama.test'})
    tools = [{
        'type': 'function',
        'function': {
            'name': 'read',
            'parameters': {'type': 'object'},
        },
    }]
    text = (
        'Let me read the file.\n\n'
        '<read>\n'
        '<path>/Users/haraldweiss/projects/ai-provider-service/dispatcher.py</path>\n'
        '</read>'
    )

    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        'message': {'content': text},
        'prompt_eval_count': 12,
        'eval_count': 8,
        'done_reason': 'stop',
    }
    monkeypatch.setattr('providers.ollama.requests.post', Mock(return_value=response))

    result = client.create_message(
        'dev-coder:latest',
        [{'role': 'user', 'content': 'inspect dispatcher'}],
        max_tokens=123,
        tools=tools,
    )

    assert result['content'] == [{'text': 'Let me read the file.'}]
    assert result['tool_calls'] == [{
        'id': 'call_0',
        'name': 'read',
        'input': {
            'path': '/Users/haraldweiss/projects/ai-provider-service/dispatcher.py',
        },
    }]
    assert result['stop_reason'] == 'tool_use'


def test_create_message_leaves_xml_text_when_tool_was_not_offered(monkeypatch):
    from providers.ollama import OllamaClient

    client = OllamaClient({'api_endpoint': 'http://ollama.test'})
    text = '<read><path>/tmp/secret.txt</path></read>'

    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        'message': {'content': text},
        'prompt_eval_count': 12,
        'eval_count': 8,
        'done_reason': 'stop',
    }
    monkeypatch.setattr('providers.ollama.requests.post', Mock(return_value=response))

    result = client.create_message(
        'dev-coder:latest',
        [{'role': 'user', 'content': 'show xml'}],
        max_tokens=123,
        tools=[{'type': 'function', 'function': {'name': 'get_weather'}}],
    )

    assert result['content'] == [{'text': text}]
    assert result['tool_calls'] == []
    assert result['stop_reason'] == 'stop'


def test_create_message_leaves_json_text_when_tool_was_not_offered(monkeypatch):
    from providers.ollama import OllamaClient

    client = OllamaClient({'api_endpoint': 'http://ollama.test'})
    text = '{"name":"delete_file","arguments":{"path":"/tmp/a"}}'

    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        'message': {'content': text},
        'prompt_eval_count': 12,
        'eval_count': 8,
        'done_reason': 'stop',
    }
    monkeypatch.setattr('providers.ollama.requests.post', Mock(return_value=response))

    result = client.create_message(
        'dev-coder:latest',
        [{'role': 'user', 'content': 'show json'}],
        max_tokens=123,
        tools=[{'type': 'function', 'function': {'name': 'get_weather'}}],
    )

    assert result['content'] == [{'text': text}]
    assert result['tool_calls'] == []
    assert result['stop_reason'] == 'stop'


def test_create_message_maps_bare_dsml_invoke_with_codefence_param(monkeypatch):
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
        '<｜｜DSML｜｜invoke name="ctx_batch_execute">\n'
        '<｜｜DSML｜｜parameter name="cmds" string="false">\n'
        '```json\n'
        '[{"cmd": "git status"}]\n'
        '```\n'
        '</｜｜DSML｜｜parameter>\n'
        '</｜｜DSML｜｜invoke>'
    )

    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        'message': {'content': dsml},
        'prompt_eval_count': 12,
        'eval_count': 8,
        'done_reason': 'stop',
    }
    monkeypatch.setattr('providers.ollama.requests.post', Mock(return_value=response))

    result = client.create_message(
        'ornith:latest',
        [{'role': 'user', 'content': 'status'}],
        max_tokens=123,
        tools=tools,
    )

    assert result['content'] == [{'text': ''}]
    assert result['tool_calls'] == [{
        'id': 'call_0',
        'name': 'ctx_batch_execute',
        'input': {'cmds': [{'cmd': 'git status'}]},
    }]
    assert result['stop_reason'] == 'tool_use'


def test_create_message_retries_without_native_tools_after_ollama_tool_grammar_error(
    monkeypatch,
):
    from providers.ollama import OllamaClient

    client = OllamaClient({'api_endpoint': 'http://ollama.test'})
    tools = [{
        'type': 'function',
        'function': {
            'name': 'ctx_execute',
            'parameters': {'type': 'object'},
        },
    }]
    failed = Mock()
    failed.status_code = 400
    failed.text = '{"error":"Value looks like object, but can\\\'t find closing \\\'}\\\' symbol"}'
    failed_error = requests.HTTPError(response=failed)

    first = Mock()
    first.raise_for_status.side_effect = failed_error

    dsml = (
        '<｜｜DSML｜｜tool_calls>'
        '<｜｜DSML｜｜invoke name="ctx_execute">'
        '<｜｜DSML｜｜parameter name="cmd" string="true">git status'
        '</｜｜DSML｜｜parameter>'
        '</｜｜DSML｜｜invoke>'
        '</｜｜DSML｜｜tool_calls>'
    )
    second = Mock()
    second.raise_for_status.return_value = None
    second.json.return_value = {
        'message': {'content': dsml},
        'prompt_eval_count': 12,
        'eval_count': 4,
        'done_reason': 'stop',
    }
    post = Mock(side_effect=[first, second])
    monkeypatch.setattr('providers.ollama.requests.post', post)

    result = client.create_message(
        'ornith:latest',
        [{'role': 'user', 'content': 'status'}],
        max_tokens=123,
        tools=tools,
    )

    assert post.call_count == 2
    assert post.call_args_list[0].kwargs['json']['tools'] == tools
    assert 'tools' not in post.call_args_list[1].kwargs['json']
    assert result['tool_calls'] == [{
        'id': 'call_0',
        'name': 'ctx_execute',
        'input': {'cmd': 'git status'},
    }]
    assert result['stop_reason'] == 'tool_use'

def test_endpoints_hosting_matches_bare_name_to_tagged_map(monkeypatch):
    """A bare model name like `mistral-nemo-cc` must route to the endpoint whose
    /api/tags map reports `mistral-nemo-cc:latest` (tag-insensitive predictive routing).
    Regression for the AI-categorizer 120s-timeout incident (blind round-robin)."""
    from providers.ollama import OllamaClient

    client = OllamaClient({'api_endpoints': [
        'http://mini:11434', 'http://studio:11434', 'http://oracle:11441', 'http://m3:11434',
    ]})
    OllamaClient._endpoint_models = {
        'http://mini:11434': {'dev-coder:latest'},
        'http://studio:11434': {'ornith:latest'},
        'http://oracle:11441': {'llama3.2:3b'},
        'http://m3:11434': {'mistral-nemo-cc:latest', 'qwen3.6:latest'},
    }
    try:
        # bare request -> only the host that has the model (M3) is eligible
        assert client._endpoints_hosting('mistral-nemo-cc') == ['http://m3:11434']
        # tagged request matches directly too
        assert client._endpoints_hosting('mistral-nemo-cc:latest') == ['http://m3:11434']
        # other models still resolve to their own host
        assert client._endpoints_hosting('dev-coder') == ['http://mini:11434']
        assert client._endpoints_hosting('llama3.2:3b') == ['http://oracle:11441']
        # unknown model -> we do not know where it lives
        assert client._endpoints_hosting('does-not-exist') == []
    finally:
        OllamaClient._endpoint_models = {}


def _ok_response():
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        'message': {'content': 'ok'},
        'prompt_eval_count': 5,
        'eval_count': 1,
        'done_reason': 'stop',
    }
    return response


def test_create_message_converts_openai_string_tool_arguments_to_objects(monkeypatch):
    """OpenAI clients replay tool history with ``arguments`` as a JSON string.
    Ollama's /api/chat requires an object; forwarding the string 400s the whole
    request ('Value looks like object, but can't find closing }') on every
    follow-up turn. Regression for the pi search_web 400 incident (2026-09-22)."""
    from providers.ollama import OllamaClient

    client = OllamaClient({'api_endpoint': 'http://ollama.test'})
    history = [
        {'role': 'user', 'content': 'Wie ist das Wetter in Dortmund?'},
        {
            'role': 'assistant',
            'content': '',
            'tool_calls': [{
                'id': 'call_1',
                'type': 'function',
                'function': {
                    'name': 'search_web',
                    'arguments': '{"query": "wetter Dortmund heute", "count": 1}',
                },
            }],
        },
        {'role': 'tool', 'tool_call_id': 'call_1', 'name': 'search_web', 'content': '[]'},
    ]
    post = Mock(return_value=_ok_response())
    monkeypatch.setattr('providers.ollama.requests.post', post)

    client.create_message('dev-coder:q4', history, max_tokens=64)

    sent = post.call_args.kwargs['json']['messages']
    assert sent[1]['tool_calls'][0]['function']['arguments'] == {
        'query': 'wetter Dortmund heute', 'count': 1,
    }
    # caller's history must not be mutated
    assert history[1]['tool_calls'][0]['function']['arguments'] == (
        '{"query": "wetter Dortmund heute", "count": 1}'
    )


def test_create_message_coerces_unparseable_tool_arguments_to_empty_object(monkeypatch):
    """A truncated tool-call argument string must never poison the request:
    it degrades to {} (Ollama accepts that) instead of a grammar 400."""
    from providers.ollama import OllamaClient

    client = OllamaClient({'api_endpoint': 'http://ollama.test'})
    history = [
        {'role': 'user', 'content': 'status'},
        {
            'role': 'assistant',
            'content': '',
            'tool_calls': [{
                'id': 'call_1',
                'type': 'function',
                'function': {'name': 'run', 'arguments': '{"cmd": "git statu'},
            }],
        },
    ]
    post = Mock(return_value=_ok_response())
    monkeypatch.setattr('providers.ollama.requests.post', post)

    client.create_message('dev-coder:q4', history, max_tokens=64)

    sent = post.call_args.kwargs['json']['messages']
    assert sent[1]['tool_calls'][0]['function']['arguments'] == {}


def test_create_message_keeps_object_tool_arguments_unchanged(monkeypatch):
    """Native-format history (arguments already an object) passes through as-is."""
    from providers.ollama import OllamaClient

    client = OllamaClient({'api_endpoint': 'http://ollama.test'})
    args = {'path': '/tmp/x'}
    history = [
        {'role': 'user', 'content': 'read'},
        {
            'role': 'assistant',
            'content': '',
            'tool_calls': [{'function': {'name': 'read_file', 'arguments': args}}],
        },
    ]
    post = Mock(return_value=_ok_response())
    monkeypatch.setattr('providers.ollama.requests.post', post)

    client.create_message('dev-coder:q4', history, max_tokens=64)

    sent = post.call_args.kwargs['json']['messages']
    assert sent[1]['tool_calls'][0]['function']['arguments'] is args
