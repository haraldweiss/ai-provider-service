"""Preserve actual models, tool calls and reported costs across response formats."""
from __future__ import annotations

import math
from typing import Any


def field(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def reported_cost(usage: Any) -> dict:
    cost = field(usage, 'cost')
    if isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0:
        return {'cost_usd': cost}
    return {}


def completion_metadata(response: Any, message: Any = None) -> dict:
    result = {}
    model = field(response, 'model')
    if isinstance(model, str) and model:
        result['model'] = model
    if message is None:
        choices = field(response, 'choices', [])
        if isinstance(choices, list) and choices:
            message = field(choices[0], 'message')
    calls = field(message, 'tool_calls', [])
    if isinstance(calls, list) and calls:
        normalized = []
        for index, call in enumerate(calls):
            function = field(call, 'function', {})
            name = field(function, 'name')
            if not isinstance(name, str) or not name:
                continue
            call_id = field(call, 'id')
            normalized.append({
                'id': call_id if isinstance(call_id, str) and call_id else f'call_{index}',
                'name': name,
                'input': field(function, 'arguments', {}),
            })
        if normalized:
            result.update(tool_calls=normalized, stop_reason='tool_use')
    return result
