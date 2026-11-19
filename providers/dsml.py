"""Recover complete, explicitly offered DSML calls at the gateway boundary.

Only visible response text is inspected. This module returns structured calls;
it never executes tools. Invalid or ambiguous markup stays visible for diagnosis.
"""
from __future__ import annotations

import json
import re
import uuid

_TOKEN = r'(?:｜DSML｜|｜｜DSML｜｜|\|DSML\||\|\|DSML\|\|)'
_START = re.compile(rf'<\s*(?P<marker>{_TOKEN})\s*(?P<tag>tool_calls|calls|invoke)\b(?P<attrs>[^>]*)>')
_INVOKE = re.compile(
    rf'<\s*(?P<marker>{_TOKEN})\s*invoke\b(?P<attrs>[^>]*)>'
    r'(?P<body>.*?)</\s*(?P=marker)\s*invoke\s*>', re.DOTALL)
_PARAMETER = re.compile(
    rf'<\s*(?P<marker>{_TOKEN})\s*parameter\b(?P<attrs>[^>]*)>'
    r'(?P<value>.*?)</\s*(?P=marker)\s*parameter\s*>', re.DOTALL)
_ATTR = re.compile(r'''\s+(\w+)\s*=\s*(["'])(.*?)\2''', re.DOTALL)
_FENCE = re.compile(r'(?m)^[ \t]*(?:>[ \t]*)*(?:(?:[-+*]|\d+[.)])[ \t]+)?(`{3,}|~{3,})([^\n]*)')


def _attributes(raw: str) -> dict | None:
    attrs = {}
    pos = 0
    for match in _ATTR.finditer(raw):
        if raw[pos:match.start()].strip() or match[1] in attrs:
            return None
        attrs[match[1]] = match[3]
        pos = match.end()
    return None if raw[pos:].strip() else attrs


def _offered_tools(tools: list | None) -> dict:
    offered = {}
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get('function', tool)
        if not isinstance(function, dict):
            continue
        name = function.get('name')
        if isinstance(name, str) and name:
            offered[name] = function.get('parameters', function.get('input_schema', {}))
    return offered


def _parse_invoke(match: re.Match, offered: dict) -> dict | None:
    attrs = _attributes(match['attrs'])
    if not attrs or set(attrs) != {'name'} or attrs['name'] not in offered:
        return None
    params = {}
    pos = 0
    for parameter in _PARAMETER.finditer(match['body']):
        if match['body'][pos:parameter.start()].strip():
            return None
        attributes = _attributes(parameter['attrs'])
        if (not attributes or not attributes.get('name')
                or set(attributes) - {'name', 'string'}
                or attributes.get('string', 'true') not in ('true', 'false')
                or attributes['name'] in params):
            return None
        value = parameter['value']
        if attributes.get('string') == 'false':
            value = value.strip()
            fence = re.fullmatch(r'```(?:json)?\s*\n?(.*?)\s*```', value, re.DOTALL)
            if fence:
                value = fence[1]
            try:
                value = json.loads(value)
                json.dumps(value, allow_nan=False)
            except (ValueError, RecursionError):
                return None
        params[attributes['name']] = value
        pos = parameter.end()
    if match['body'][pos:].strip():
        return None
    schema = offered[attrs['name']]
    if isinstance(schema, dict):
        required = schema.get('required', [])
        if isinstance(required, list) and any(name not in params for name in required):
            return None
    return {'name': attrs['name'], 'input': params}


def _fenced_ranges(text: str) -> list[tuple[int, int]]:
    ranges = []
    opening = None
    for match in _FENCE.finditer(text):
        if opening is None:
            opening = match
        elif (match[1][0] == opening[1][0] and len(match[1]) >= len(opening[1])
              and not match[2].strip()):
            ranges.append((opening.start(), match.end()))
            opening = None
    if opening is not None:
        ranges.append((opening.start(), len(text)))
    return ranges


def _invocations(text: str, offered: dict) -> list[tuple[int, int, dict]]:
    """Find complete invocations, without rescuing children of broken wrappers."""
    candidates = []
    fences = _fenced_ranges(text)
    pos = 0
    while (start := _START.search(text, pos)) is not None:
        pos = start.end()
        if any(a <= start.start() < b for a, b in fences):
            continue
        if start['tag'] == 'invoke':
            match = _INVOKE.match(text, start.start())
            if match is None:
                break
            pos = match.end()
            call = _parse_invoke(match, offered)
            if call is not None:
                candidates.append((match.start(), match.end(), call))
            continue
        close = re.compile(rf'</\s*{re.escape(start["marker"])}\s*{start["tag"]}\s*>').search(text, pos)
        if close is None:
            break
        if not start['attrs'].strip():
            siblings = []
            cursor = start.end()
            while cursor < close.start():
                if text[cursor:close.start()].isspace():
                    cursor = close.start()
                    break
                cursor += len(text[cursor:close.start()]) - len(text[cursor:close.start()].lstrip())
                match = _INVOKE.match(text, cursor, close.start())
                if match is None:
                    break
                if any(a <= match.start() < b for a, b in fences):
                    break
                call = _parse_invoke(match, offered)
                if call is not None:
                    siblings.append((match.start(), match.end(), call))
                cursor = match.end()
            if cursor == close.start():
                candidates.extend(siblings)
        pos = close.end()
    return candidates


def _arguments(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, RecursionError):
            return value
    return value


def normalize_dsml_result(result: dict, tools: list | None) -> dict:
    """Copy a provider result only when complete visible DSML was recovered.

    Native calls win over conflicting text. Empty native placeholders are repaired
    only when one complete matching invocation makes the intended arguments clear.
    """
    offered = _offered_tools(tools)
    if not offered or result.get('stop_reason') in ('length', 'max_tokens', 'content_filter'):
        return result
    content = result.get('content')
    blocks = [{'text': content}] if isinstance(content, str) else content
    if not isinstance(blocks, list):
        return result
    candidates = []
    for index, block in enumerate(blocks):
        text = block.get('text') if isinstance(block, dict) else None
        if isinstance(text, str) and 'DSML' in text and block.get('type', 'text') == 'text':
            candidates.extend((index, a, b, call) for a, b, call in _invocations(text, offered))
    native = result.get('tool_calls') or []
    calls = [dict(call) for call in native]
    removals = {}
    for index, start, end, call in candidates:
        matches = [i for i, item in enumerate(native) if item.get('name') == call['name']]
        same_name = [c for _, _, _, c in candidates if c['name'] == call['name']]
        if matches:
            if len(matches) != 1 or len(same_name) != 1:
                continue
            target = matches[0]
            arguments = _arguments(native[target].get('input'))
            if arguments == call['input']:
                pass
            elif arguments in ({}, '', None):
                calls[target] = {**calls[target], 'input': call['input']}
            else:
                continue
        else:
            calls.append({'id': 'call_dsml_'+uuid.uuid4().hex, **call})
        removals.setdefault(index, []).append((start, end))
    if not removals:
        return result
    cleaned_blocks = list(blocks)
    for index, spans in removals.items():
        text = blocks[index]['text']
        for start, end in reversed(spans):
            text = text[:start]+text[end:]
        # Strip only envelopes left empty by accepted invocations. Mixed unknown
        # or malformed calls retain both their text and their envelope.
        text = re.sub(rf'<\s*(?P<m>{_TOKEN})\s*(?P<t>tool_calls|calls)\s*>\s*'
                      r'</\s*(?P=m)\s*(?P=t)\s*>', '', text)
        cleaned_blocks[index] = {**blocks[index], 'text': text.strip()}
    by_id = {call.get('id'): call for call in calls}
    for index, block in enumerate(cleaned_blocks):
        if isinstance(block, dict) and block.get('type') == 'tool_use':
            call = by_id.get(block.get('id'))
            if call and call.get('name') == block.get('name'):
                cleaned_blocks[index] = {**block, 'input': call['input']}
    return {**result, 'content': cleaned_blocks if isinstance(content, list) else cleaned_blocks[0]['text'],
            'tool_calls': calls, 'stop_reason': 'tool_use'}
