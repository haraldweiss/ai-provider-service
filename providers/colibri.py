"""Authenticated REST and native-SSE client for the local Colibri gateway."""

from __future__ import annotations

import json
import threading
from typing import Iterator

import requests

from config import Config
from providers.base import BaseClient, ProviderStream


class ColibriOverloaded(RuntimeError):
    """The single Colibri inference slot or its bounded waiting room is full."""


class ColibriProtocolError(RuntimeError):
    """The upstream native SSE stream did not follow the expected protocol."""


_active_request = threading.Semaphore(1)
_waiter_lock = threading.Lock()
_waiter_count = 0


def _acquire_admission() -> None:
    """Acquire Colibri's one active request slot with one bounded waiter."""
    global _waiter_count

    if _active_request.acquire(blocking=False):
        return

    with _waiter_lock:
        if _waiter_count >= Config.COLIBRI_MAX_WAITING:
            raise ColibriOverloaded('colibri_queue_full')
        _waiter_count += 1

    try:
        if not _active_request.acquire(timeout=Config.COLIBRI_QUEUE_TIMEOUT_SEC):
            raise ColibriOverloaded('colibri_queue_timeout')
    finally:
        with _waiter_lock:
            _waiter_count -= 1


def _raise_for_inference_status(response) -> None:
    if getattr(response, 'status_code', None) == 429:
        raise ColibriOverloaded('colibri_queue_full')
    response.raise_for_status()


def _offered_tool_names(tools: list[dict] | None) -> set[str]:
    names = set()
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get('function')
        if not isinstance(function, dict):
            continue
        name = function.get('name')
        if isinstance(name, str) and name:
            names.add(name)
    return names


def _validate_tool_calls(
    tool_calls, offered_names: set[str], *, allow_missing_name: bool,
) -> list[dict]:
    if tool_calls is None:
        return []
    if not isinstance(tool_calls, list):
        raise ColibriProtocolError('Colibri response has invalid tool calls')
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            raise ColibriProtocolError('Colibri response has invalid tool call')
        function = tool_call.get('function')
        if allow_missing_name and function is None:
            continue
        if not isinstance(function, dict):
            raise ColibriProtocolError('Colibri response has invalid tool call')
        name = function.get('name')
        if allow_missing_name and (name is None or name == ''):
            continue
        if not isinstance(name, str) or not name:
            raise ColibriProtocolError('Colibri response has invalid tool call name')
        if name not in offered_names:
            raise ColibriProtocolError('unoffered tool in Colibri response')
    return tool_calls


def _result_tool_calls(tool_parts: dict[int, dict]) -> list[dict]:
    calls = []
    for index in sorted(tool_parts):
        part = tool_parts[index]
        arguments = part['arguments']
        try:
            parsed_arguments = json.loads(arguments) if arguments else {}
        except (TypeError, ValueError):
            parsed_arguments = arguments
        calls.append({
            'id': part['id'] or f'call_{index}',
            'name': part['name'],
            'input': parsed_arguments,
        })
    return calls


class ColibriClient(BaseClient):
    """Server-key-only adapter for the private Colibri OpenAI-compatible API."""

    def __init__(self, config: dict):
        del config  # Personal provider keys are deliberately unsupported.
        if not Config.COLIBRI_ENABLED or not Config.COLIBRI_API_KEY:
            raise ValueError('Colibri is disabled or has no API key')
        self._base_url = Config.COLIBRI_BASE_URL.rstrip('/')
        self._model_id = Config.COLIBRI_MODEL_ID
        self._headers = {'Authorization': f'Bearer {Config.COLIBRI_API_KEY}'}

    @property
    def _inference_timeout(self) -> tuple[int, int]:
        return (
            Config.COLIBRI_CONNECT_TIMEOUT_SEC,
            Config.COLIBRI_STREAM_READ_TIMEOUT_SEC,
        )

    def _chat_payload(
        self, messages: list[dict], max_tokens: int, stream: bool,
        tools: list[dict] | None, tool_choice,
    ) -> dict:
        payload = {
            'model': self._model_id,
            'messages': messages,
            'max_completion_tokens': max_tokens,
            'stream': stream,
        }
        if tools:
            payload['tools'] = tools
        if tool_choice is not None:
            payload['tool_choice'] = tool_choice
        return payload

    def get_models(self) -> list[str]:
        try:
            response = requests.get(
                f'{self._base_url}/v1/models', headers=self._headers, timeout=5,
            )
            response.raise_for_status()
            response.json()
            return [self._model_id]
        except Exception:
            return []

    def health(self) -> bool:
        try:
            response = requests.get(
                f'{self._base_url}/health', headers=self._headers, timeout=5,
            )
            response.raise_for_status()
            return response.json() == {'status': 'ok'}
        except Exception:
            return False

    def create_message(
        self, model: str, messages: list[dict], max_tokens: int = 600,
        *, tools: list[dict] | None = None, tool_choice=None,
    ) -> dict:
        del model
        _acquire_admission()
        response = None
        try:
            response = requests.post(
                f'{self._base_url}/v1/chat/completions',
                json=self._chat_payload(messages, max_tokens, False, tools, tool_choice),
                headers=self._headers,
                timeout=self._inference_timeout,
            )
            _raise_for_inference_status(response)
            data = response.json()
            choices = data.get('choices') if isinstance(data, dict) else None
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise ColibriProtocolError('Colibri response has no choices')
            choice = choices[0]
            message = choice.get('message') if isinstance(choice.get('message'), dict) else {}
            content = message.get('content') if isinstance(message.get('content'), str) else ''
            finish_reason = choice.get('finish_reason')
            stop_reason = finish_reason if isinstance(finish_reason, str) and finish_reason else 'stop'
            usage = data.get('usage') if isinstance(data.get('usage'), dict) else {}
            tool_calls = []
            validated_calls = _validate_tool_calls(
                message.get('tool_calls'), _offered_tool_names(tools),
                allow_missing_name=False,
            )
            for index, call in enumerate(validated_calls):
                function = call['function']
                name = function['name']
                arguments = function.get('arguments', '{}')
                try:
                    parsed_arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
                except ValueError:
                    parsed_arguments = arguments
                tool_calls.append({
                    'id': call.get('id') or f'call_{index}',
                    'name': name,
                    'input': parsed_arguments,
                })
            return {
                'content': [{'text': content}],
                'tool_calls': tool_calls,
                'usage': {
                    'input_tokens': usage.get('prompt_tokens', 0),
                    'output_tokens': usage.get('completion_tokens', 0),
                },
                'stop_reason': stop_reason,
            }
        finally:
            try:
                if response is not None:
                    response.close()
            finally:
                _active_request.release()

    def open_stream(
        self, model: str, messages: list[dict], max_tokens: int = 600,
        *, tools: list[dict] | None = None, tool_choice=None,
    ) -> ProviderStream:
        del model
        _acquire_admission()
        response = None
        try:
            response = requests.post(
                f'{self._base_url}/v1/chat/completions',
                json=self._chat_payload(messages, max_tokens, True, tools, tool_choice),
                headers=self._headers,
                stream=True,
                timeout=self._inference_timeout,
            )
            _raise_for_inference_status(response)
        except Exception:
            try:
                if response is not None:
                    response.close()
            finally:
                _active_request.release()
            raise

        result = {
            'content': [], 'tool_calls': [], 'usage': {}, 'stop_reason': 'stop',
        }
        close_lock = threading.Lock()
        closed = False

        def close() -> None:
            nonlocal closed
            with close_lock:
                if closed:
                    return
                closed = True
            try:
                response.close()
            finally:
                _active_request.release()

        offered_names = _offered_tool_names(tools)
        text_parts: list[str] = []
        tool_parts: dict[int, dict] = {}

        def events() -> Iterator[dict]:
            terminal_seen = False
            try:
                for raw_line in response.iter_lines(decode_unicode=True):
                    if not raw_line:
                        continue
                    if isinstance(raw_line, bytes):
                        try:
                            raw_line = raw_line.decode('utf-8')
                        except UnicodeDecodeError as exc:
                            raise ColibriProtocolError('invalid SSE encoding') from exc
                    if not isinstance(raw_line, str) or not raw_line.startswith('data:'):
                        continue
                    payload = raw_line[5:].lstrip()
                    if payload == '[DONE]':
                        return
                    try:
                        frame = json.loads(payload)
                    except (TypeError, ValueError) as exc:
                        raise ColibriProtocolError('invalid SSE JSON') from exc
                    choices = frame.get('choices') if isinstance(frame, dict) else None
                    if not isinstance(choices, list) or not choices:
                        raise ColibriProtocolError('SSE frame has no choices')

                    for choice in choices:
                        if not isinstance(choice, dict):
                            raise ColibriProtocolError('SSE frame has invalid choice')
                        delta = choice.get('delta')
                        if delta is None:
                            delta = {}
                        if not isinstance(delta, dict):
                            raise ColibriProtocolError('SSE frame has invalid delta')
                        _validate_tool_calls(
                            delta.get('tool_calls'), offered_names,
                            allow_missing_name=True,
                        )

                    for choice in choices:
                        delta = choice.get('delta') or {}
                        content = delta.get('content')
                        if isinstance(content, str) and content:
                            text_parts.append(content)
                        for position, tool_call in enumerate(delta.get('tool_calls') or []):
                            index = tool_call.get('index', position)
                            if not isinstance(index, int):
                                raise ColibriProtocolError('SSE tool call has invalid index')
                            part = tool_parts.setdefault(index, {
                                'id': '', 'name': '', 'arguments': '',
                            })
                            if tool_call.get('id'):
                                part['id'] = tool_call['id']
                            function = tool_call.get('function') or {}
                            if function.get('name'):
                                part['name'] = function['name']
                            arguments = function.get('arguments')
                            if isinstance(arguments, str):
                                part['arguments'] += arguments
                        finish_reason = choice.get('finish_reason')
                        if isinstance(finish_reason, str) and finish_reason:
                            terminal_seen = True
                            result['stop_reason'] = finish_reason

                    if text_parts:
                        result['content'] = [{'text': ''.join(text_parts)}]
                    result['tool_calls'] = _result_tool_calls(tool_parts)
                    usage = frame.get('usage')
                    if isinstance(usage, dict):
                        result['usage'] = {
                            'input_tokens': usage.get('prompt_tokens', 0),
                            'output_tokens': usage.get('completion_tokens', 0),
                        }
                    yield frame
                if not terminal_seen:
                    raise ColibriProtocolError(
                        'Colibri SSE stream ended without a terminal marker',
                    )
            finally:
                close()

        return ProviderStream(events=events(), close=close, result=result)
