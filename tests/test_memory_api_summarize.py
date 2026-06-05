"""Memory API — on-demand per-note summarize endpoint."""

import pytest
from unittest.mock import patch
from config import Config


@pytest.fixture
def headers():
    return {'Authorization': f'Bearer {Config.SERVICE_TOKEN}'}


def test_summarize_creates_summary_note(client, headers, app, monkeypatch):
    monkeypatch.setattr(Config, 'MEMORY_FREE_MODELS', ['ollama::mistral'])
    with app.app_context():
        from storage.memory import MemoryWriter
        n = MemoryWriter().write_note(user_id='harald', app='bt',
                                      title='Long doc', body='lorem ipsum...',
                                      tags=[], folder=None, slug=None)
        nid = n.id

    with patch('api.memory_api._call_summary_model', return_value=('Short.', 'ollama::mistral')):
        r = client.post(f'/memory/notes/{nid}/summarize?user_id=harald',
                        headers=headers, json={})
    assert r.status_code == 200, r.get_data(as_text=True)
    summary = r.get_json()['summary']
    assert summary['kind'] == 'summary'
    assert summary['extra']['source_ids'] == [nid]


def test_summarize_503_when_no_free_models(client, headers, app, monkeypatch):
    monkeypatch.setattr(Config, 'MEMORY_FREE_MODELS', [])
    with app.app_context():
        from storage.memory import MemoryWriter
        n = MemoryWriter().write_note(user_id='harald', app='bt', title='X',
                                      body='', tags=[], folder=None, slug=None)
        nid = n.id
    r = client.post(f'/memory/notes/{nid}/summarize?user_id=harald',
                    headers=headers, json={})
    assert r.status_code == 503
