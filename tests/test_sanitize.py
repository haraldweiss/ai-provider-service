"""Content sanitization tests."""

from storage.sanitize import sanitize_for_summary


def test_preserves_normal_text():
    assert sanitize_for_summary('hello world') == 'hello world'


def test_strips_control_chars():
    raw = 'a\x00b\x01c\x1fd'
    assert sanitize_for_summary(raw) == 'abcd'


def test_preserves_newlines_and_tabs():
    assert sanitize_for_summary('line1\n\tindented') == 'line1\n\tindented'


def test_escapes_mustache():
    result = sanitize_for_summary('{{system_prompt}}')
    assert '{{' not in result
    assert 'system_prompt' in result


def test_escapes_triple_backtick():
    result = sanitize_for_summary('```\necho hi\n```')
    assert '```' not in result
    assert 'echo hi' in result


def test_truncates_long_input():
    long = 'x' * 6000
    result = sanitize_for_summary(long)
    assert len(result) <= 5100
    assert 'truncated' in result


def test_empty_returns_empty():
    assert sanitize_for_summary('') == ''
    assert sanitize_for_summary(None) == ''
