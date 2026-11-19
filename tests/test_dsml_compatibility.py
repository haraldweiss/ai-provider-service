"""Leaked model markup must become offered tool calls, never invented actions."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

@pytest.fixture(autouse=True)
def no_background_worker(monkeypatch):
    monkeypatch.setattr('worker.start', lambda app: None)


TOOLS = [{'type': 'function', 'function': {
    'name': 'run_commands', 'parameters': {'type': 'object',
        'properties': {'commands': {'type': 'array'}}, 'required': ['commands']}}}]


def dsml(*, marker='｜DSML｜', wrapper='calls', value='["pwd"]',
         name='run_commands', flag='false'):
    return (f'<{marker} {wrapper}>'
            f'<{marker} invoke name="{name}">'
            f'<{marker} parameter name="commands" string="{flag}">{value}'
            f'</{marker} parameter></{marker} invoke></{marker} {wrapper}>')


def execute(monkeypatch, *, text, tools=TOOLS, native=None, reason='stop'):
    import dispatcher
    response = {'content': [{'text': text}], 'usage': {
        'input_tokens': 31, 'output_tokens': 17, 'cost_usd': 0},
        'model': 'actual-deepseek', 'stop_reason': reason}
    if native is not None:
        response['tool_calls'] = native
    original = deepcopy(response)
    monkeypatch.setattr(dispatcher, 'get_client', lambda *_: SimpleNamespace(
        create_message=lambda *a, **kw: response))
    result = dispatcher._execute('u1', 'cline', 'requested', [], 4096,
                                 config_override={}, tools=tools)
    assert response == original  # normalization must not mutate provider-owned data
    assert result['usage'] == original['usage']
    assert result['model'] == 'actual-deepseek'
    return result


@pytest.mark.parametrize('marker', ['｜DSML｜', '｜｜DSML｜｜', '|DSML|', '||DSML||'])
@pytest.mark.parametrize('wrapper', ['calls', 'tool_calls'])
def test_recovers_offered_dsml_calls(app, monkeypatch, marker, wrapper):
    result = execute(monkeypatch, text='Checking.\n'+dsml(marker=marker, wrapper=wrapper))
    assert result['content'] == [{'text': 'Checking.'}]
    assert result['stop_reason'] == 'tool_use'
    assert [(c['name'], c['input']) for c in result['tool_calls']] == [
        ('run_commands', {'commands': ['pwd']})]
    assert result['tool_calls'][0]['id']


@pytest.mark.parametrize('tools', [None, [], [{'name': 'other'}]])
def test_unoffered_calls_remain_text(app, monkeypatch, tools):
    text = dsml()
    result = execute(monkeypatch, text=text, tools=tools)
    assert result['content'] == [{'text': text}]
    assert not result.get('tool_calls')


@pytest.mark.parametrize('text', [
    dsml()[:-12],
    dsml(value='["pwd"'),
    dsml().replace('</｜DSML｜ parameter>', ''),
    dsml().replace('string="false"', 'string="maybe"'),
    dsml().replace('name="commands"', 'name="missing"'),
    dsml().replace('</｜DSML｜ invoke>', 'unexpected</｜DSML｜ invoke>'),
    'Example:\n```xml\n'+dsml()+'\n```',
])
def test_malformed_or_example_calls_remain_text(app, monkeypatch, text):
    result = execute(monkeypatch, text=text)
    assert result['content'] == [{'text': text}]
    assert not result.get('tool_calls')


def test_complete_bare_invoke_and_fenced_json_argument(app, monkeypatch):
    text = dsml(value='```json\n["pwd"]\n```')
    text = text.removeprefix('<｜DSML｜ calls>').removesuffix('</｜DSML｜ calls>')
    result = execute(monkeypatch, text=text)
    assert result['content'] == [{'text': ''}]
    assert result['tool_calls'][0]['input'] == {'commands': ['pwd']}


def test_unknown_calls_in_mixed_wrapper_are_preserved(app, monkeypatch):
    unknown = dsml(name='not_offered').removeprefix('<｜DSML｜ calls>').removesuffix('</｜DSML｜ calls>')
    text = dsml().replace('</｜DSML｜ calls>', unknown+'</｜DSML｜ calls>')
    result = execute(monkeypatch, text=text)
    assert unknown in result['content'][0]['text']
    assert [c['name'] for c in result['tool_calls']] == ['run_commands']


def test_multiple_invocations_have_distinct_ids_in_original_order(app, monkeypatch):
    result = execute(monkeypatch, text=dsml()+dsml(value='["date"]'))
    assert [c['input'] for c in result['tool_calls']] == [
        {'commands': ['pwd']}, {'commands': ['date']}]
    assert len({c['id'] for c in result['tool_calls']}) == 2


@pytest.mark.parametrize('arguments', [{'commands': ['pwd']}, '{"commands":["pwd"]}', '{}', ''])
def test_matching_native_call_keeps_id_without_duplicate(app, monkeypatch, arguments):
    result = execute(monkeypatch, text=dsml(), native=[{
        'id': 'upstream-id', 'name': 'run_commands', 'input': arguments}])
    assert len(result['tool_calls']) == 1
    assert result['tool_calls'][0]['id'] == 'upstream-id'
    arguments = result['tool_calls'][0]['input']
    if isinstance(arguments, str):
        arguments = json.loads(arguments)
    assert arguments == {'commands': ['pwd']}
    assert result['content'] == [{'text': ''}]


def test_conflicting_native_call_is_not_replaced_or_duplicated(app, monkeypatch):
    native = [{'id': 'native', 'name': 'run_commands', 'input': {'commands': ['date']}}]
    result = execute(monkeypatch, text=dsml(), native=native)
    assert result['tool_calls'] == native
    assert result['content'] == [{'text': dsml()}]


def test_truncated_response_does_not_promote_even_complete_markup(app, monkeypatch):
    result = execute(monkeypatch, text=dsml(), reason='length')
    assert result['content'] == [{'text': dsml()}]
    assert not result.get('tool_calls')


@pytest.mark.parametrize('stream', [False, True])
def test_cline_dsml_reaches_openai_json_and_sse(app, client, monkeypatch, stream):
    import dispatcher
    import providers.cline
    from config import Config
    monkeypatch.setattr(Config, 'ADMIN_TOKEN', 'admin-test-token')
    monkeypatch.setattr(Config, 'ADMIN_USER_ID', 'harald')
    monkeypatch.setattr(dispatcher, '_load_config', lambda *_: {'api_key': 'fake-test-key'})
    monkeypatch.setattr(dispatcher.health_tracker, 'is_healthy', lambda *_: True)
    raw = {'model': 'deepseek-v4.1', 'choices': [{'message': {'content': dsml()},
        'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 31, 'completion_tokens': 17}}
    class HTTP:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, *args, **kw):
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: raw)
    monkeypatch.setattr(providers.cline.httpx, 'Client', HTTP)
    response = client.post('/v1/chat/completions', headers={
        'Authorization': 'Bearer admin-test-token'}, json={
        'model': 'cline/cline-pass/deepseek-v4.1-flash-1017',
        'messages': [{'role': 'user', 'content': 'Use run_commands'}],
        'tools': TOOLS, 'stream': stream})
    assert response.status_code == 200
    if stream:
        chunks = [json.loads(line[6:]) for line in response.text.splitlines()
                  if line.startswith('data: ') and line != 'data: [DONE]']
        calls = [c for chunk in chunks for c in chunk['choices'][0]['delta'].get('tool_calls', [])]
        assert chunks[-1]['choices'][0]['finish_reason'] == 'tool_calls'
        assert 'DSML' not in response.text
    else:
        choice = response.json['choices'][0]
        calls = choice['message']['tool_calls']
        assert choice['finish_reason'] == 'tool_calls'
        assert choice['message']['content'] == ''
    assert calls[0]['function']['name'] == 'run_commands'
    assert json.loads(calls[0]['function']['arguments']) == {'commands': ['pwd']}


@pytest.mark.parametrize('text', [
    '<｜DSML｜ calls>\n```xml\n'+dsml().removeprefix('<｜DSML｜ calls>').removesuffix('</｜DSML｜ calls>')+'\n```\n</｜DSML｜ calls>',
    dsml(value='[NaN]'),
])
def test_fenced_nested_examples_and_nonfinite_json_remain_text(app, monkeypatch, text):
    result = execute(monkeypatch, text=text)
    assert result['content'] == [{'text': text}]
    assert not result.get('tool_calls')


def test_cline_length_finish_reason_prevents_dsml_promotion(app, client, monkeypatch):
    import dispatcher
    import providers.cline
    from config import Config
    monkeypatch.setattr(Config, 'ADMIN_TOKEN', 'admin-test-token')
    monkeypatch.setattr(Config, 'ADMIN_USER_ID', 'harald')
    monkeypatch.setattr(dispatcher, '_load_config', lambda *_: {'api_key': 'fake-test-key'})
    monkeypatch.setattr(dispatcher.health_tracker, 'is_healthy', lambda *_: True)
    raw = {'choices': [{'message': {'content': dsml()}, 'finish_reason': 'length'}]}
    class HTTP:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, *args, **kw):
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: raw)
    monkeypatch.setattr(providers.cline.httpx, 'Client', HTTP)
    response = client.post('/v1/chat/completions', headers={
        'Authorization': 'Bearer admin-test-token'}, json={
        'model': 'cline/cline-pass/deepseek-v4.1-flash-1017',
        'messages': [{'role': 'user', 'content': 'Use run_commands'}], 'tools': TOOLS})
    choice = response.json['choices'][0]
    assert 'tool_calls' not in choice['message']
    assert choice['message']['content'] == dsml()
    assert choice['finish_reason'] == 'length'


@pytest.mark.parametrize('text', [
    '<｜DSML｜ calls>'+dsml(),
    '<｜DSML｜ calls>'+dsml()+'</｜DSML｜ calls>',
    '> ```xml\n> '+dsml().removeprefix('<｜DSML｜ calls>').removesuffix('</｜DSML｜ calls>')+'\n> ```',
    '```xml\n```json\n'+dsml()+'\n```',
])
def test_broken_nesting_and_markdown_examples_never_execute(app, monkeypatch, text):
    result = execute(monkeypatch, text=text)
    assert result['content'] == [{'text': text}]
    assert not result.get('tool_calls')


def test_filtered_output_does_not_become_tool_calls(app, monkeypatch):
    result = execute(monkeypatch, text=dsml(), reason='content_filter')
    assert result['content'] == [{'text': dsml()}]
    assert result['stop_reason'] == 'content_filter'
    assert not result.get('tool_calls')


def test_native_anthropic_content_is_consistent_after_placeholder_repair():
    from providers.dsml import normalize_dsml_result
    source = {'content': [{'text': dsml()}, {'type': 'tool_use', 'id': 'native',
        'name': 'run_commands', 'input': {}}],
        'tool_calls': [{'id': 'native', 'name': 'run_commands', 'input': {}}]}
    result = normalize_dsml_result(source, TOOLS)
    assert result['content'][1]['input'] == {'commands': ['pwd']}
    assert result['tool_calls'][0]['input'] == {'commands': ['pwd']}
    assert source['content'][1]['input'] == {}


@pytest.mark.parametrize('text', [
    '> ```xml\n> '+dsml()+'\n> ```',
    dsml(value='["pwd"'),
])
def test_ollama_uses_same_conservative_dsml_rules(monkeypatch, text):
    from providers.ollama import _result_from_ollama_data
    result = _result_from_ollama_data({'message': {'content': text}, 'eval_count': 20},
        'http://local.test', 4096, 'deepseek', TOOLS)
    assert result['content'] == [{'text': text}]
    assert not result.get('tool_calls')


def test_list_contained_fenced_example_remains_text(app, monkeypatch):
    text = '- ```xml\n  '+dsml()+'\n  ```'
    result = execute(monkeypatch, text=text)
    assert result['content'] == [{'text': text}]
    assert not result.get('tool_calls')
