"""Memory API — read-only audit listing."""

import pytest
from config import Config


@pytest.fixture
def headers():
    return {'Authorization': f'Bearer {Config.SERVICE_TOKEN}'}


def test_audit_lists_only_audit_kind(client, headers, app):
    with app.app_context():
        from storage.memory import MemoryWriter
        w = MemoryWriter()
        w.write_audit(user_id='harald', app='bt', provider='claude',
                      chat_request_id='r1', prompt='p', response='r',
                      tokens={}, cost_eur=0, latency_ms=0, timestamp=None)
        w.write_note(user_id='harald', app='bt', title='nope', body='',
                     tags=[], folder=None, slug=None)
    r = client.get('/memory/audit?user_id=harald', headers=headers)
    assert r.status_code == 200
    audit = r.get_json()['notes']
    assert all(n['kind'] == 'audit' for n in audit)
    assert len(audit) == 1
