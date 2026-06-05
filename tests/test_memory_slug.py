"""Slug helpers for markdown memory filenames."""

import pytest
from storage.slug import slugify, next_free_slug


def test_basic():
    assert slugify('Hello World') == 'hello-world'


def test_umlauts():
    assert slugify('Über Föhn — heute') == 'uber-fohn-heute'


def test_specials_collapse():
    assert slugify('!!!Hi!!! ???there??? @#$') == 'hi-there'


def test_empty_falls_back_to_note():
    assert slugify('') == 'note'
    assert slugify('   ') == 'note'
    assert slugify('!!!') == 'note'


def test_max_length():
    long = 'a' * 200
    s = slugify(long)
    assert len(s) <= 80
    assert s == 'a' * 80


def test_explicit_slug_validated():
    from storage.slug import validate_explicit_slug
    assert validate_explicit_slug('hello-world') is True
    assert validate_explicit_slug('Hello') is False  # uppercase
    assert validate_explicit_slug('a' * 81) is False  # too long
    assert validate_explicit_slug('') is False
    assert validate_explicit_slug('with space') is False


def test_next_free_slug_no_collision():
    taken = set()
    assert next_free_slug('hello', taken) == 'hello'


def test_next_free_slug_with_collision():
    taken = {'hello', 'hello-2', 'hello-3'}
    assert next_free_slug('hello', taken) == 'hello-4'


def test_next_free_slug_max_attempts_raises():
    taken = {'hello'} | {f'hello-{i}' for i in range(2, 102)}
    with pytest.raises(ValueError, match='slug collision'):
        next_free_slug('hello', taken)
