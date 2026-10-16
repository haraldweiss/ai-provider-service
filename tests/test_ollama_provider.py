"""Tests for local Ollama provider response mapping."""

from unittest.mock import Mock
import pytest
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


def _http_error(status, body='{"error":"model not found"}'):
    response = Mock()
    response.status_code = status
    response.text = body
    response.raise_for_status.side_effect = requests.HTTPError(response=response)
    return response


def _pool_client(monkeypatch, endpoints, model_map):
    """OllamaClient with a pinned model map (no /api/tags refresh, no network)."""
    import time

    from providers.ollama import OllamaClient

    OllamaClient._endpoint_models = dict(model_map)
    OllamaClient._model_map_last_refresh = time.monotonic()  # skip the TTL refresh
    return OllamaClient({'api_endpoints': endpoints})


def test_pool_timeout_is_not_masked_by_expected_404(monkeypatch):
    """A timeout on the endpoint that hosts the model must not be reported as a
    404 from the endpoints that do not host it.

    Regression for the 2026-10-16 Open WebUI web-search incident: the local 3B
    model timed out on a ~180-document prompt, the pool then 404'd on the two
    Macs, and the client saw `Provider ollama rejected the request (HTTP 404)`
    instead of the timeout that actually happened."""
    from providers.ollama import OllamaClient

    endpoints = ['http://oracle:11441', 'http://m3:11434', 'http://mini:11434']
    client = _pool_client(monkeypatch, endpoints, {
        'http://oracle:11441': {'oracle-llama3.2:3b'},
        'http://m3:11434': {'dev-coder:latest'},
        'http://mini:11434': {'other:latest'},
    })

    def fake_post(url, **kwargs):
        if url.startswith('http://oracle'):
            raise requests.Timeout('Read timed out. (read timeout=120)')
        return _http_error(404)

    monkeypatch.setattr('providers.ollama.requests.post', fake_post)
    try:
        with pytest.raises(requests.Timeout):
            client.create_message('oracle-llama3.2:3b', [{'role': 'user', 'content': 'hi'}])
        # the expected misses still prune the model map (self-heal)
        assert 'oracle-llama3.2:3b' not in OllamaClient._endpoint_models.get('http://m3:11434', set())
        assert 'oracle-llama3.2:3b' not in OllamaClient._endpoint_models.get('http://mini:11434', set())
    finally:
        OllamaClient._endpoint_models = {}
        OllamaClient._model_map_last_refresh = 0.0


def test_pool_404_from_hosting_endpoint_still_raises_404(monkeypatch):
    """When the endpoint that the map says hosts the model answers 404, that is a
    real 'model not found' and must still surface as an HTTP 404."""
    from providers.ollama import OllamaClient

    endpoints = ['http://oracle:11441', 'http://m3:11434']
    client = _pool_client(monkeypatch, endpoints, {
        'http://oracle:11441': {'oracle-llama3.2:3b'},
        'http://m3:11434': {'dev-coder:latest'},
    })

    monkeypatch.setattr('providers.ollama.requests.post', lambda url, **kwargs: _http_error(404))
    try:
        with pytest.raises(requests.HTTPError) as excinfo:
            client.create_message('oracle-llama3.2:3b', [{'role': 'user', 'content': 'hi'}])
        assert excinfo.value.response.status_code == 404
    finally:
        OllamaClient._endpoint_models = {}
        OllamaClient._model_map_last_refresh = 0.0


def test_pool_unknown_model_reports_404_when_nothing_hosts_it(monkeypatch):
    """No endpoint in the pool hosts the model: the 404 is the truthful answer."""
    from providers.ollama import OllamaClient

    endpoints = ['http://oracle:11441', 'http://m3:11434']
    client = _pool_client(monkeypatch, endpoints, {
        'http://oracle:11441': {'oracle-llama3.2:3b'},
        'http://m3:11434': {'dev-coder:latest'},
    })

    monkeypatch.setattr('providers.ollama.requests.post', lambda url, **kwargs: _http_error(404))
    try:
        with pytest.raises(requests.HTTPError) as excinfo:
            client.create_message('does-not-exist', [{'role': 'user', 'content': 'hi'}])
        assert excinfo.value.response.status_code == 404
    finally:
        OllamaClient._endpoint_models = {}
        OllamaClient._model_map_last_refresh = 0.0


def test_resolve_timeout_config_env_and_default(monkeypatch):
    from providers.ollama import OllamaClient, _resolve_timeout

    monkeypatch.delenv('OLLAMA_TIMEOUT', raising=False)
    assert _resolve_timeout({}) == 120
    assert _resolve_timeout({'timeout': 30}) == 30
    assert _resolve_timeout({'timeout': '45'}) == 45
    # invalid / non-positive values fall back to the default
    assert _resolve_timeout({'timeout': 'nope'}) == 120
    assert _resolve_timeout({'timeout': 0}) == 120

    monkeypatch.setenv('OLLAMA_TIMEOUT', '170')
    assert _resolve_timeout({}) == 170
    # an explicit config value still wins over the env
    assert _resolve_timeout({'timeout': 90}) == 90
    assert OllamaClient({'api_endpoint': 'http://ollama.test'}).timeout == 170


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
