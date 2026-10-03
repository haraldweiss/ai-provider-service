# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the OpenRouter video/animation bridge (Wolfini AI Studio).

Covers the pre-generation cost normalisation (OpenRouter's per-model
`pricing_skus` differ wildly between families) and the async submit / poll /
content flow.
"""

import pytest

import api.videos_api as videos_api

HEADERS = {'Authorization': 'Bearer test-token'}
JOB_ID = 'job-abc123'


class _FakeRequestException(Exception):
    pass


class _FakeResponse:
    def __init__(self, payload=None, status=200, content=b'', headers=None):
        self._payload = payload
        self.status_code = status
        self._content = content
        self.headers = headers or {}
        self.text = '' if payload is None else str(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise _FakeRequestException(f'HTTP {self.status_code}')

    def json(self):
        return self._payload

    def iter_content(self, chunk_size=65536):
        yield self._content

    def close(self):
        pass


class _FakeRequests:
    RequestException = _FakeRequestException

    def __init__(self, post_payload=None, get_payload=None, content=b'video-bytes'):
        self.post_calls = []
        self.get_calls = []
        self.post_payload = post_payload if post_payload is not None else {
            'id': JOB_ID, 'polling_url': f'https://openrouter.ai/api/v1/videos/{JOB_ID}',
            'status': 'pending',
        }
        self.get_payload = get_payload if get_payload is not None else {
            'id': JOB_ID, 'status': 'completed',
            'unsigned_urls': [f'https://openrouter.ai/api/v1/videos/{JOB_ID}/content?index=0'],
            'usage': {'cost': 0.42, 'is_byok': False},
        }
        self.content = content

    def post(self, url, json=None, headers=None, timeout=None):
        self.post_calls.append({'url': url, 'json': json, 'headers': headers})
        return _FakeResponse(self.post_payload)

    def get(self, url, params=None, headers=None, timeout=None, stream=False):
        self.get_calls.append({'url': url, 'params': params, 'headers': headers, 'stream': stream})
        if stream:
            return _FakeResponse(status=200, content=self.content,
                                 headers={'Content-Type': 'video/mp4', 'Content-Length': str(len(self.content))})
        return _FakeResponse(self.get_payload)


def _auth(app):
    from config import Config
    for cfg in (Config, videos_api.Config):
        cfg.ADMIN_USER_ID = 'harald'
        cfg.SERVICE_TOKEN = 'test-token'
        cfg.OPENROUTER_API_KEY = 'test-openrouter-key'
        cfg.OPENROUTER_BASE_URL = 'https://openrouter.ai/api/v1'


def _fake_requests(monkeypatch, **kwargs):
    fake = _FakeRequests(**kwargs)
    monkeypatch.setattr(videos_api, 'requests', fake)
    return fake


def _model_row(mid='google/veo-3.1'):
    return {
        'id': mid,
        'name': 'Google: Veo 3.1',
        'capabilities': {
            'aspect_ratios': ['16:9', '9:16'],
            'resolutions': ['720p', '1080p'],
            'durations': [4, 6, 8],
            'frame_images': ['first_frame', 'last_frame'],
            'generate_audio': True,
            'supports_seed': True,
            'price_hint': 'ab 0.2000 $/s',
            'pricing': videos_api._normalize_pricing({
                'duration_seconds_with_audio': '0.40',
                'duration_seconds_with_audio_4k': '0.60',
                'duration_seconds_without_audio': '0.20',
                'duration_seconds_without_audio_4k': '0.40',
            }),
        },
    }


# --- pricing normalisation ---------------------------------------------------

def test_normalize_pricing_duration_seconds():
    pricing = videos_api._normalize_pricing({
        'duration_seconds': '0.08',
        'duration_seconds_480p': '0.05',
        'duration_seconds_768p': '0.08',
    })
    rates = {(t['resolution'], t['audio']): t['usd_per_second'] for t in pricing['tiers']}
    assert rates[(None, None)] == 0.08
    assert rates[('480p', None)] == 0.05
    assert rates[('768p', None)] == 0.08


def test_normalize_pricing_cents_and_audio_variants():
    pricing = videos_api._normalize_pricing({
        'cents_per_video_output_second_720p': '14',
        'cents_per_second_output': '3',
        'duration_seconds_with_audio': '0.12',
        'duration_seconds_without_audio': '0.10',
        'duration_seconds_with_audio_4k': '0.30',
    })
    rates = {(t['resolution'], t['audio']): t['usd_per_second'] for t in pricing['tiers']}
    assert rates[('720p', None)] == pytest.approx(0.14)
    assert rates[(None, None)] == pytest.approx(0.03)
    assert rates[(None, True)] == pytest.approx(0.12)
    assert rates[(None, False)] == pytest.approx(0.10)
    assert rates[('4K', True)] == pytest.approx(0.30)


def test_normalize_pricing_flags_token_and_megapixel_based():
    token = videos_api._normalize_pricing({'video_tokens': '0.0000035'})
    assert token['token_based'] is True
    assert token['tiers'] == []
    mp = videos_api._normalize_pricing({'cents_per_megapixel_second_precise': '7.5'})
    assert mp['megapixel_based'] is True


def test_normalize_pricing_metadata():
    pricing = videos_api._normalize_pricing({
        'minimum_cents_per_generation': '56',
        'cents_per_image_input': '1',
        'reference_duration_seconds_480p': '0.04',
    })
    assert pricing['minimum_usd'] == pytest.approx(0.56)
    assert pricing['per_image_usd'] == pytest.approx(0.01)
    assert any(t['kind'] == 'image' and t['resolution'] == '480p' for t in pricing['tiers'])


def test_price_hint_reports_cheapest_rate():
    pricing = videos_api._normalize_pricing({'duration_seconds_720p': '0.10', 'duration_seconds': '0.20'})
    assert videos_api._price_hint(pricing).startswith('ab 0.1000 $/s')
    assert videos_api._price_hint(videos_api._normalize_pricing({'video_tokens': '0.1'})) == 'token-basiert'


# --- estimate ----------------------------------------------------------------

def test_estimate_prefers_resolution_and_audio():
    caps = _model_row()['capabilities']
    # 1080p + audio falls to the generic with_audio rate (0.40), 8s => 3.20
    assert videos_api.estimate_video_cost(caps, 8, '1080p', True, False) == pytest.approx(3.20)
    # 4K audio tier
    assert videos_api.estimate_video_cost(caps, 4, '4K', True, False) == pytest.approx(2.40)


def test_estimate_applies_minimum_and_image_cost():
    caps = _model_row()['capabilities']
    caps['pricing']['minimum_usd'] = 5.0
    caps['pricing']['per_image_usd'] = 0.01
    cost = videos_api.estimate_video_cost(caps, 4, '1080p', True, True, image_count=2)
    assert cost == pytest.approx(5.0)  # 1.60 base + 0.02 images < minimum


def test_estimate_returns_none_for_token_based():
    caps = {'pricing': videos_api._normalize_pricing({'video_tokens': '0.1'})}
    assert videos_api.estimate_video_cost(caps, 5, '720p', False, False) is None


# --- /v1/videos/models -------------------------------------------------------

def test_list_video_models(app, client, monkeypatch):
    _auth(app)
    monkeypatch.setattr(videos_api, '_fetch_video_models', lambda: [_model_row()])
    r = client.get('/v1/videos/models', headers=HEADERS)
    assert r.status_code == 200
    row = r.get_json()['data'][0]
    assert row['id'] == 'google/veo-3.1'
    assert row['capabilities']['durations'] == [4, 6, 8]
    assert row['capabilities']['pricing']['tiers']
    assert row['capabilities']['price_hint']


# --- /v1/videos/generations --------------------------------------------------

def _submit(client, **body):
    payload = {'model': 'google/veo-3.1', 'prompt': 'a cat surfing'}
    payload.update(body)
    return client.post('/v1/videos/generations', json=payload, headers=HEADERS)


def test_submit_forwards_params_and_normalizes_frames(app, client, monkeypatch):
    _auth(app)
    fake = _fake_requests(monkeypatch)
    monkeypatch.setattr(videos_api, '_fetch_video_models', lambda: [_model_row()])

    r = _submit(
        client,
        duration=8,
        resolution='1080p',
        aspect_ratio='16:9',
        generate_audio=True,
        seed=7,
        frame_images=[{'url': 'data:image/png;base64,AAA', 'frame_type': 'first_frame'}],
    )

    assert r.status_code == 202
    sent = fake.post_calls[0]['json']
    assert sent['duration'] == 8
    assert sent['resolution'] == '1080p'
    assert sent['generate_audio'] is True
    assert sent['seed'] == 7
    assert sent['frame_images'][0]['frame_type'] == 'first_frame'
    assert sent['frame_images'][0]['image_url']['url'] == 'data:image/png;base64,AAA'
    body = r.get_json()
    assert body['id'] == JOB_ID
    assert body['estimate_usd'] == pytest.approx(3.20)


def test_submit_requires_prompt_without_frames(app, client, monkeypatch):
    _auth(app)
    fake = _fake_requests(monkeypatch)
    monkeypatch.setattr(videos_api, '_fetch_video_models', lambda: [_model_row()])
    r = _submit(client, prompt='')
    assert r.status_code == 400
    assert 'prompt' in r.get_json()['error']['message']
    assert fake.post_calls == []


@pytest.mark.parametrize('field,value', [
    ('duration', 'soon'),
    ('duration', 0),
    ('aspect_ratio', 'widescreen'),
    ('generate_audio', 'yes'),
    ('seed', 'abc'),
    ('size', 'huge'),
])
def test_submit_rejects_invalid_values(app, client, monkeypatch, field, value):
    _auth(app)
    fake = _fake_requests(monkeypatch)
    monkeypatch.setattr(videos_api, '_fetch_video_models', lambda: [_model_row()])
    r = _submit(client, **{field: value})
    assert r.status_code == 400
    assert field in r.get_json()['error']['message']
    assert fake.post_calls == []


def test_submit_rejects_bad_frame_type(app, client, monkeypatch):
    _auth(app)
    fake = _fake_requests(monkeypatch)
    monkeypatch.setattr(videos_api, '_fetch_video_models', lambda: [_model_row()])
    r = _submit(client, frame_images=[{'url': 'x', 'frame_type': 'middle_frame'}])
    assert r.status_code == 400
    assert 'frame_type' in r.get_json()['error']['message']
    assert fake.post_calls == []


# --- status + content --------------------------------------------------------

def test_status_returns_content_count_and_cost(app, client, monkeypatch):
    _auth(app)
    _fake_requests(monkeypatch)
    r = client.get(f'/v1/videos/generations/{JOB_ID}', headers=HEADERS)
    assert r.status_code == 200
    body = r.get_json()
    assert body['status'] == 'completed'
    assert body['content_count'] == 1
    assert body['usage']['cost'] == pytest.approx(0.42)


def test_content_streams_bytes(app, client, monkeypatch):
    _auth(app)
    _fake_requests(monkeypatch, content=b'mp4-data')
    r = client.get(f'/v1/videos/generations/{JOB_ID}/content?index=0', headers=HEADERS)
    assert r.status_code == 200
    assert r.headers['Content-Type'] == 'video/mp4'
    assert r.data == b'mp4-data'
