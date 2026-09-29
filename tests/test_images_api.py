# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the OpenRouter image API bridge (/v1/images/models, /v1/images/generations).

Regression guards for the NightCafe-style picker support (Wolfini AI Studio):
the endpoint used to forward only {model, prompt, n, size}, so aspect_ratio /
resolution / quality / output_format / seed were silently dropped and a UI
could not offer them.
"""

import pytest

import api.images_api as images_api

HEADERS = {'Authorization': 'Bearer test-token'}

_FAKE_IMAGE = 'data:image/jpeg;base64,ZmFrZQ=='


class _FakeRequestException(Exception):
    """Stand-in for requests.RequestException on the fake requests module."""


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise _FakeRequestException(f'HTTP {self.status_code}')

    def json(self):
        return self._payload


class _FakeRequests:
    """Minimal stand-in for the `requests` module used inside images_api."""

    RequestException = _FakeRequestException

    def __init__(self, post_payload=None, get_payload=None):
        self.post_calls = []
        self.get_calls = []
        self.post_payload = post_payload if post_payload is not None else {
            'data': [{'b64_json': 'ZmFrZQ==', 'media_type': 'image/jpeg'}],
            'usage': {'cost': 0.015, 'prompt_tokens': 12, 'completion_tokens': 7291},
        }
        self.get_payload = get_payload if get_payload is not None else {'data': []}

    def post(self, url, json=None, headers=None, timeout=None):
        self.post_calls.append({'url': url, 'json': json, 'headers': headers, 'timeout': timeout})
        return _FakeResponse(self.post_payload)

    def get(self, url, params=None, headers=None, timeout=None):
        self.get_calls.append({'url': url, 'params': params})
        return _FakeResponse(self.get_payload)


def _auth(app):
    """Set the auth/config values on the Config classes in play.

    `test_config_access_control` reloads `config`, which rebinds
    `sys.modules['config'].Config`; modules that did `from config import Config`
    (api.auth, api.images_api) keep the *original* class. Setting the values on
    both keeps the tests order-independent (see conftest.py).
    """
    from config import Config
    for cfg in (Config, images_api.Config):
        cfg.ADMIN_USER_ID = 'harald'
        cfg.SERVICE_TOKEN = 'test-token'
        cfg.OPENROUTER_API_KEY = 'test-openrouter-key'
        cfg.OPENROUTER_BASE_URL = 'https://openrouter.ai/api/v1'


def _fake_requests(monkeypatch, **kwargs):
    fake = _FakeRequests(**kwargs)
    monkeypatch.setattr(images_api, 'requests', fake)
    return fake


def _generate(client, **body):
    payload = {'model': 'black-forest-labs/flux.2-pro', 'prompt': 'a cat'}
    payload.update(body)
    return client.post('/v1/images/generations', json=payload, headers=HEADERS)


def test_generations_forwards_aspect_ratio_and_quality(app, client, monkeypatch):
    _auth(app)
    fake = _fake_requests(monkeypatch)

    r = _generate(
        client,
        aspect_ratio='16:9',
        resolution='2K',
        quality='high',
        output_format='jpeg',
        seed=42,
    )

    assert r.status_code == 200
    sent = fake.post_calls[0]['json']
    assert sent['aspect_ratio'] == '16:9'
    assert sent['resolution'] == '2K'
    assert sent['quality'] == 'high'
    assert sent['output_format'] == 'jpeg'
    assert sent['seed'] == 42
    assert sent['model'] == 'black-forest-labs/flux.2-pro'
    assert sent['prompt'] == 'a cat'
    # upstream response is passed through as a Data-URI + real cost
    assert r.get_json()['data'][0]['b64_json'] == _FAKE_IMAGE
    assert r.get_json()['usage']['cost'] == 0.015
    assert r.get_json()['params']['aspect_ratio'] == '16:9'


def test_generations_rejects_invalid_aspect_ratio(app, client, monkeypatch):
    _auth(app)
    fake = _fake_requests(monkeypatch)

    r = _generate(client, aspect_ratio='widescreen')

@pytest.mark.parametrize('field,value', [
    ('resolution', '8K'),
    ('quality', 'ultra'),
    ('output_format', 'tiff'),
    ('background', 'rainbow'),
    ('seed', 'abc'),
    ('output_compression', '200'),
    ('size', 'big'),
])
def test_generations_rejects_invalid_values(app, client, monkeypatch, field, value):
    _auth(app)
    fake = _fake_requests(monkeypatch)

    r = _generate(client, **{field: value})

    assert r.status_code == 400, f'{field}={value!r} should be rejected'
    assert field in r.get_json()['error']['message']
    assert fake.post_calls == []


def test_generations_clamps_n_to_ten(app, client, monkeypatch):
    _auth(app)
    fake = _fake_requests(monkeypatch)

    r = _generate(client, n=99)

    assert r.status_code == 200
    assert fake.post_calls[0]['json']['n'] == 10


def test_seedream_size_fallback_only_without_aspect_ratio(app, client, monkeypatch):
    """The min-pixels fallback must not fight an explicitly requested ratio."""
    _auth(app)
    fake = _fake_requests(monkeypatch)

    _generate(client, model='bytedance-seed/seedream-4.5', aspect_ratio='1:1')
    assert 'size' not in fake.post_calls[0]['json']

    _generate(client, model='bytedance-seed/seedream-4.5')
    assert fake.post_calls[1]['json']['size'] == '2048x2048'


def test_input_references_normalized_to_image_url_objects(app, client, monkeypatch):
    _auth(app)
    fake = _fake_requests(monkeypatch)

    r = _generate(client, input_references=['data:image/png;base64,ZmFrZQ=='])

    assert r.status_code == 200
    assert fake.post_calls[0]['json']['input_references'] == [
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,ZmFrZQ=='}},
    ]


def test_input_references_rejects_empty_array(app, client, monkeypatch):
    _auth(app)
    fake = _fake_requests(monkeypatch)

    r = _generate(client, input_references=[])

    assert r.status_code == 400
    assert 'input_references' in r.get_json()['error']['message']
    assert fake.post_calls == []


def test_image_models_include_capabilities(app, client, monkeypatch):
    _auth(app)
    monkeypatch.setattr(images_api, '_fetch_image_models', lambda: [
        {'id': 'black-forest-labs/flux.2-pro', 'name': 'FLUX.2 Pro', '_image_output_cost': 0.1},
    ])
    monkeypatch.setattr(images_api, '_fetch_image_capabilities', lambda: {
        'black-forest-labs/flux.2-pro': {
            'aspect_ratios': ['1:1', '16:9'],
            'output_formats': ['png', 'jpeg'],
            'max_n': 1,
            'supports_seed': True,
        },
    })

    r = client.get('/v1/images/models', headers=HEADERS)

    assert r.status_code == 200
    row = r.get_json()['data'][0]
    assert row['id'] == 'black-forest-labs/flux.2-pro'
    assert row['capabilities']['aspect_ratios'] == ['1:1', '16:9']
    assert row['capabilities']['supports_seed'] is True


def test_image_models_details_zero_keeps_legacy_payload(app, client, monkeypatch):
    _auth(app)
    monkeypatch.setattr(images_api, '_fetch_image_models', lambda: [
        {'id': 'krea/krea-2-medium', 'name': 'Krea 2 Medium', '_image_output_cost': 0.2},
    ])
    monkeypatch.setattr(images_api, '_fetch_image_capabilities', lambda: {
        'krea/krea-2-medium': {'aspect_ratios': ['1:1']},
    })

    r = client.get('/v1/images/models?details=0', headers=HEADERS)

    assert r.status_code == 200
    assert r.get_json()['data'] == [{'id': 'krea/krea-2-medium', 'name': 'Krea 2 Medium'}]


def test_image_models_tolerate_missing_capabilities(app, client, monkeypatch):
    """A model without capabilities must still be listed (endpoint unreachable)."""
    _auth(app)
    monkeypatch.setattr(images_api, '_fetch_image_models', lambda: [
        {'id': 'meta/muse-image', 'name': 'Muse Image', '_image_output_cost': 0.0},
    ])
    monkeypatch.setattr(images_api, '_fetch_image_capabilities', lambda: {})

    r = client.get('/v1/images/models', headers=HEADERS)

    assert r.status_code == 200
    assert r.get_json()['data'] == [{'id': 'meta/muse-image', 'name': 'Muse Image'}]


def test_normalize_capabilities_maps_openrouter_supported_parameters():
    caps = images_api._normalize_capabilities({
        'aspect_ratio': {'type': 'enum', 'values': ['1:1', '16:9', 'auto']},
        'resolution': {'type': 'enum', 'values': ['1K', '2K']},
        'quality': {'type': 'enum', 'values': ['auto', 'high']},
        'n': {'type': 'range', 'min': 1, 'max': 6},
        'seed': {},
        'input_references': {'type': 'range', 'min': 1, 'max': 1},
    }, supports_streaming=False)

    assert caps['aspect_ratios'] == ['1:1', '16:9', 'auto']
    assert caps['resolutions'] == ['1K', '2K']
    assert caps['qualities'] == ['auto', 'high']
    assert caps['max_n'] == 6
    assert caps['supports_seed'] is True
    assert caps['input_references'] == {'min': 1, 'max': 1}
    assert caps['supports_streaming'] is False
    assert 'output_formats' not in caps