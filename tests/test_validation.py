"""Unit tests for api.validation helpers."""

import pytest

from api.validation import parse_max_tokens


def test_parse_max_tokens_defaults_on_none():
    assert parse_max_tokens(None, 600) == 600


def test_parse_max_tokens_accepts_int_and_numeric_string():
    assert parse_max_tokens(42, 600) == 42
    assert parse_max_tokens('42', 600) == 42


@pytest.mark.parametrize('bad', ['abc', [1], {'x': 1}, 0, -5])
def test_parse_max_tokens_rejects_invalid(bad):
    with pytest.raises(ValueError):
        parse_max_tokens(bad, 600)
