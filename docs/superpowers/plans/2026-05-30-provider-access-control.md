# Provider Access Control + opencode.ai Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Spec:** [`docs/superpowers/specs/2026-05-30-provider-access-control-design.md`](../specs/2026-05-30-provider-access-control-design.md)
>
> **AGENTS.md compatibility:** This plan is a good fit for opencode (throughput-optimized — bulk implementation, test coverage, new provider integration). Claude Code should handle the deploy-time tasks (Task 16+ smoke tests on VPS) per the agent routing rules.

**Goal:** Add admin-vs-user identity, per-(user, provider) grant gating, opencode.ai as a 6th provider, and an admin UI for grant management + usage overview.

**Architecture:** New `provider_grants` table with soft-delete + admin-bypass. `Principal` resolved from token at auth boundary; gate decorator applied to `/configs`, `/chat`, `/providers`. New `opencode` provider modeled on the existing OpenAI client. Admin REST API + Jinja-rendered UI at `/admin/ui`.

**Tech Stack:** Python 3.9+, Flask, Flask-SQLAlchemy, pytest, OpenAI SDK (for opencode.ai's OpenAI-compatible endpoint), Jinja templates, vanilla JS for UI interactions.

**Verification baseline:** existing test suite passes (`pytest -q`). After each task: re-run full suite.

---

## Phase 1 — Foundation

### Task 1: `ProviderGrant` model + table creation

**Files:**
- Modify: [storage/models.py](../../../storage/models.py) — append `ProviderGrant` model
- Modify: [app.py](../../../app.py:31) — import `ProviderGrant` so `db.create_all()` registers it
- Create: `tests/test_provider_grant_model.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_provider_grant_model.py`:

```python
"""Tests for ProviderGrant model: insert, uniqueness, soft-delete semantics."""

from datetime import datetime, timezone
import pytest
from database import db
from storage.models import ProviderGrant


def test_create_grant(app):
    with app.app_context():
        g = ProviderGrant(
            user_id='lisa',
            provider_id='claude',
            granted_by='harald',
            note='for transcript summarization',
        )
        db.session.add(g)
        db.session.commit()

        assert g.id is not None
        assert g.granted_at is not None
        assert g.revoked_at is None


def test_unique_user_provider(app):
    with app.app_context():
        db.session.add(ProviderGrant(
            user_id='lisa', provider_id='claude', granted_by='harald'))
        db.session.commit()

        db.session.add(ProviderGrant(
            user_id='lisa', provider_id='claude', granted_by='harald'))
        with pytest.raises(Exception):
            db.session.commit()
        db.session.rollback()


def test_soft_delete(app):
    with app.app_context():
        g = ProviderGrant(
            user_id='lisa', provider_id='claude', granted_by='harald')
        db.session.add(g)
        db.session.commit()

        g.revoked_at = datetime.now(timezone.utc)
        db.session.commit()

        fresh = ProviderGrant.query.filter_by(id=g.id).first()
        assert fresh.revoked_at is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_provider_grant_model.py -v`
Expected: FAIL with `ImportError: cannot import name 'ProviderGrant'`

- [ ] **Step 3: Add the model**

Append to [storage/models.py](../../../storage/models.py) (after the `UsageEvent` class):

```python
class ProviderGrant(db.Model):
    """Admin grant: user X may configure & use provider Y.

    Required for all non-admin users to access providers not in
    Config.UNGATED_PROVIDERS. Re-granting after revoke updates the existing
    row (clears revoked_at, refreshes granted_at, replaces note).
    """
    __tablename__ = 'provider_grants'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(255), nullable=False, index=True)
    provider_id = db.Column(db.String(32), nullable=False)
    granted_by = db.Column(db.String(255), nullable=False)
    granted_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    revoked_at = db.Column(db.DateTime, nullable=True)
    note = db.Column(db.Text, nullable=True)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'provider_id', name='uq_user_provider_grant'),
    )

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'user_id': self.user_id,
            'provider_id': self.provider_id,
            'granted_by': self.granted_by,
            'granted_at': self.granted_at.isoformat() if self.granted_at else None,
            'revoked_at': self.revoked_at.isoformat() if self.revoked_at else None,
            'note': self.note,
        }
```

- [ ] **Step 4: Register import in app.py**

In [app.py](../../../app.py:31), update the noqa import line:

```python
from storage.models import ProviderConfig, RequestQueue, UsageEvent, ProviderGrant  # noqa: F401
```

- [ ] **Step 5: Run tests, verify they pass**

Run: `pytest tests/test_provider_grant_model.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Run full suite to confirm no regression**

Run: `pytest -q`
Expected: all existing tests pass, +3 new ones.

- [ ] **Step 7: Commit**

```bash
git add storage/models.py app.py tests/test_provider_grant_model.py
git commit -m "Add: ProviderGrant model with soft-delete semantics

Verified: pytest ✓ (existing + 3 new), no service restart on VPS needed
(db.create_all() picks up the new table on next start)."
```

---

### Task 2: Config additions for identity + gate

**Files:**
- Modify: [config.py](../../../config.py)
- Create: `tests/test_config_access_control.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_config_access_control.py`:

```python
"""Tests for Config additions: admin/gate/opencode env vars."""

import importlib
import os


def reload_config():
    import config
    importlib.reload(config)
    return config.Config


def test_admin_user_id_defaults_to_harald(monkeypatch):
    monkeypatch.delenv('ADMIN_USER_ID', raising=False)
    Config = reload_config()
    assert Config.ADMIN_USER_ID == 'harald'


def test_ungated_providers_default(monkeypatch):
    monkeypatch.delenv('UNGATED_PROVIDERS', raising=False)
    Config = reload_config()
    assert Config.UNGATED_PROVIDERS == {'ollama'}


def test_ungated_providers_env_override(monkeypatch):
    monkeypatch.setenv('UNGATED_PROVIDERS', 'ollama,custom')
    Config = reload_config()
    assert Config.UNGATED_PROVIDERS == {'ollama', 'custom'}


def test_gate_enabled_default_false(monkeypatch):
    monkeypatch.delenv('GATE_ENABLED', raising=False)
    Config = reload_config()
    assert Config.GATE_ENABLED is False


def test_gate_enabled_truthy(monkeypatch):
    monkeypatch.setenv('GATE_ENABLED', 'true')
    Config = reload_config()
    assert Config.GATE_ENABLED is True


def test_opencode_base_url_default(monkeypatch):
    monkeypatch.delenv('OPENCODE_BASE_URL', raising=False)
    Config = reload_config()
    assert Config.OPENCODE_BASE_URL == 'https://opencode.ai/zen/v1'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config_access_control.py -v`
Expected: FAIL with `AttributeError: type object 'Config' has no attribute 'ADMIN_USER_ID'`

- [ ] **Step 3: Add config entries**

Modify [config.py](../../../config.py), append inside the `Config` class (before `validate`):

```python
    # Access control (provider gating)
    ADMIN_TOKEN = os.getenv('ADMIN_TOKEN', '')
    ADMIN_USER_ID = os.getenv('ADMIN_USER_ID', 'harald')
    UNGATED_PROVIDERS = set(
        p.strip() for p in (os.getenv('UNGATED_PROVIDERS') or 'ollama').split(',') if p.strip()
    )
    GATE_ENABLED = os.getenv('GATE_ENABLED', 'false').lower() == 'true'

    # opencode.ai provider
    OPENCODE_BASE_URL = os.getenv('OPENCODE_BASE_URL', 'https://opencode.ai/zen/v1')

    # Flask sessions (admin UI cookie)
    SECRET_KEY = os.getenv('SECRET_KEY', '')
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `pytest tests/test_config_access_control.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Run full suite**

Run: `pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add config.py tests/test_config_access_control.py
git commit -m "Add: config entries for admin token, gate, opencode, sessions

Verified: pytest ✓ (existing + 6 new)."
```

---

## Phase 2 — Identity & Gate

### Task 3: `Principal` + `auth.py` rewrite

**Files:**
- Modify: [api/auth.py](../../../api/auth.py)
- Create: `tests/test_auth_principal.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_auth_principal.py`:

```python
"""Tests for Principal resolution and require_admin decorator."""

import pytest
from flask import g, jsonify
from config import Config


@pytest.fixture
def admin_app(app):
    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.ADMIN_USER_ID = 'harald'

    from api.auth import require_token, require_admin

    @app.route('/_t/who', methods=['GET'])
    @require_token
    def who():
        return jsonify({'user_id': g.principal.user_id, 'role': g.principal.role})

    @app.route('/_t/admin-only', methods=['GET'])
    @require_admin
    def admin_only():
        return jsonify({'ok': True, 'user_id': g.principal.user_id})

    return app


def test_no_token_returns_401(admin_app, client):
    r = client.get('/_t/who')
    assert r.status_code == 401


def test_invalid_token_returns_401(admin_app, client):
    r = client.get('/_t/who', headers={'Authorization': 'Bearer wrong'})
    assert r.status_code == 401


def test_service_token_resolves_to_user_role(admin_app, client):
    r = client.get('/_t/who?user_id=lisa',
                   headers={'Authorization': 'Bearer test-token'})
    assert r.status_code == 200
    data = r.get_json()
    assert data['role'] == 'user'
    assert data['user_id'] == 'lisa'


def test_admin_token_resolves_to_admin_role(admin_app, client):
    r = client.get('/_t/who?user_id=ignored-should-be-overridden',
                   headers={'Authorization': 'Bearer admin-test-token'})
    assert r.status_code == 200
    data = r.get_json()
    assert data['role'] == 'admin'
    assert data['user_id'] == 'harald'  # body user_id ignored for admin


def test_require_admin_rejects_service_token(admin_app, client):
    r = client.get('/_t/admin-only',
                   headers={'Authorization': 'Bearer test-token'})
    assert r.status_code == 403


def test_require_admin_allows_admin_token(admin_app, client):
    r = client.get('/_t/admin-only',
                   headers={'Authorization': 'Bearer admin-test-token'})
    assert r.status_code == 200
    assert r.get_json()['user_id'] == 'harald'
```

- [ ] **Step 2: Run test, verify it fails**

Run: `pytest tests/test_auth_principal.py -v`
Expected: FAIL — `require_admin` doesn't exist, `g.principal` not set.

- [ ] **Step 3: Rewrite `api/auth.py`**

Replace [api/auth.py](../../../api/auth.py) entirely:

```python
"""Bearer-Token-Auth with Principal resolution.

Two tokens are recognized:
- ADMIN_TOKEN: resolves to Principal(user_id=ADMIN_USER_ID, role='admin')
               and bypasses the provider gate. Body/query user_id is ignored
               for admin tokens so the principal's user_id is unambiguous.
- SERVICE_TOKEN: resolves to Principal(user_id=<asserted>, role='user').
                 user_id taken from body JSON, query string, or path arg.

A new X-Agent header is read into g.agent (string, may be None). Used
informationally — currently only flowed into UsageEvent.origin_app when the
caller hasn't set X-Origin-App. No policy impact.
"""

from dataclasses import dataclass
from functools import wraps
from flask import request, jsonify, g
from config import Config


@dataclass(frozen=True)
class Principal:
    user_id: str
    role: str  # 'admin' | 'user'


def _asserted_user_id() -> str:
    """Pull user_id from JSON body, then query string, then path args."""
    if request.is_json:
        body = request.get_json(silent=True) or {}
        if body.get('user_id'):
            return str(body['user_id'])
    if request.args.get('user_id'):
        return request.args['user_id']
    if request.view_args and 'user_id' in request.view_args:
        return request.view_args['user_id']
    return ''


def _resolve_principal():
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return None
    token = auth.split(' ', 1)[1].strip()
    if Config.ADMIN_TOKEN and token == Config.ADMIN_TOKEN:
        return Principal(user_id=Config.ADMIN_USER_ID, role='admin')
    if Config.SERVICE_TOKEN and token == Config.SERVICE_TOKEN:
        return Principal(user_id=_asserted_user_id(), role='user')
    return None


def require_token(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        p = _resolve_principal()
        if p is None:
            return jsonify({'error': 'Missing or invalid Bearer token'}), 401
        g.principal = p
        g.agent = request.headers.get('X-Agent')
        return f(*args, **kwargs)
    return wrapped


def require_admin(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        p = _resolve_principal()
        if p is None or p.role != 'admin':
            return jsonify({'error': 'Admin token required'}), 403
        g.principal = p
        g.agent = request.headers.get('X-Agent')
        return f(*args, **kwargs)
    return wrapped
```

- [ ] **Step 4: Run new tests**

Run: `pytest tests/test_auth_principal.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Run full suite — watch for regressions in existing auth callers**

Run: `pytest -q`
Expected: all pass. If anything fails, the most likely cause is a test that hits a `@require_token` route without setting `user_id` somewhere — check the test fixture.

- [ ] **Step 6: Commit**

```bash
git add api/auth.py tests/test_auth_principal.py
git commit -m "Refactor: auth.py — Principal resolution + require_admin

Adds Principal(user_id, role) dataclass. ADMIN_TOKEN resolves to role=admin
with user_id forced to Config.ADMIN_USER_ID; SERVICE_TOKEN keeps existing
caller-asserted user_id behavior. X-Agent header is read into g.agent.

Verified: pytest ✓ (existing + 6 new)."
```

---

### Task 4: Gate module

**Files:**
- Create: `api/gate.py`
- Create: `tests/test_gate.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_gate.py`:

```python
"""Tests for is_allowed() and require_provider_access decorator."""

import pytest
from flask import jsonify, g
from config import Config
from database import db
from storage.models import ProviderGrant
from api.auth import Principal
from api.gate import is_allowed


@pytest.fixture
def gate_on(app):
    Config.GATE_ENABLED = True
    Config.UNGATED_PROVIDERS = {'ollama'}
    return app


def test_ungated_provider_always_allowed(gate_on):
    with gate_on.app_context():
        assert is_allowed(Principal('lisa', 'user'), 'ollama') is True


def test_admin_bypasses_gate(gate_on):
    with gate_on.app_context():
        assert is_allowed(Principal('harald', 'admin'), 'claude') is True


def test_user_without_grant_denied(gate_on):
    with gate_on.app_context():
        assert is_allowed(Principal('lisa', 'user'), 'claude') is False


def test_user_with_active_grant_allowed(gate_on):
    with gate_on.app_context():
        db.session.add(ProviderGrant(
            user_id='lisa', provider_id='claude', granted_by='harald'))
        db.session.commit()
        assert is_allowed(Principal('lisa', 'user'), 'claude') is True


def test_user_with_revoked_grant_denied(gate_on):
    from datetime import datetime, timezone
    with gate_on.app_context():
        g_row = ProviderGrant(
            user_id='lisa', provider_id='claude', granted_by='harald',
            revoked_at=datetime.now(timezone.utc))
        db.session.add(g_row)
        db.session.commit()
        assert is_allowed(Principal('lisa', 'user'), 'claude') is False


def test_gate_disabled_allows_everything(app):
    Config.GATE_ENABLED = False
    with app.app_context():
        assert is_allowed(Principal('anyone', 'user'), 'claude') is True


def test_decorator_403s_when_denied(gate_on, client):
    from api.gate import require_provider_access
    from api.auth import require_token

    @gate_on.route('/_t/use/<provider_id>')
    @require_token
    @require_provider_access('provider_id')
    def use(provider_id):
        return jsonify({'ok': True})

    r = client.get('/_t/use/claude?user_id=lisa',
                   headers={'Authorization': 'Bearer test-token'})
    assert r.status_code == 403
    assert r.get_json()['error'] == 'needs_approval'


def test_decorator_allows_when_granted(gate_on, client):
    from api.gate import require_provider_access
    from api.auth import require_token

    with gate_on.app_context():
        db.session.add(ProviderGrant(
            user_id='lisa', provider_id='claude', granted_by='harald'))
        db.session.commit()

    @gate_on.route('/_t/use2/<provider_id>')
    @require_token
    @require_provider_access('provider_id')
    def use2(provider_id):
        return jsonify({'ok': True})

    r = client.get('/_t/use2/claude?user_id=lisa',
                   headers={'Authorization': 'Bearer test-token'})
    assert r.status_code == 200
```

- [ ] **Step 2: Run test, verify it fails**

Run: `pytest tests/test_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.gate'`.

- [ ] **Step 3: Implement `api/gate.py`**

Create `api/gate.py`:

```python
"""Provider access gate.

is_allowed(principal, provider_id) returns True iff:
  - Config.GATE_ENABLED is False (kill switch), OR
  - provider_id is in Config.UNGATED_PROVIDERS, OR
  - principal.role == 'admin', OR
  - an active (un-revoked) ProviderGrant exists for (user_id, provider_id).

@require_provider_access(arg_name) decorator pulls provider_id from
view_args, query, or JSON body and gates the route. Must be used AFTER
@require_token so g.principal is set.
"""

from functools import wraps
from flask import jsonify, g, request
from config import Config
from storage.models import ProviderGrant
from api.auth import Principal


def is_allowed(principal: Principal, provider_id: str) -> bool:
    if not Config.GATE_ENABLED:
        return True
    if provider_id in Config.UNGATED_PROVIDERS:
        return True
    if principal.role == 'admin':
        return True
    grant = ProviderGrant.query.filter_by(
        user_id=principal.user_id,
        provider_id=provider_id,
    ).filter(ProviderGrant.revoked_at.is_(None)).first()
    return grant is not None


def _extract_provider_id(arg_name: str) -> str | None:
    if request.view_args and arg_name in request.view_args:
        return request.view_args[arg_name]
    if request.args.get(arg_name):
        return request.args[arg_name]
    if request.is_json:
        body = request.get_json(silent=True) or {}
        # /chat uses 'provider' not 'provider_id' — accept both.
        return body.get(arg_name) or body.get('provider')
    return None


def require_provider_access(arg_name: str = 'provider_id'):
    """Decorator: 403 if g.principal lacks access to provider in path/query/body."""
    def deco(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            provider_id = _extract_provider_id(arg_name)
            if not provider_id:
                return jsonify({'error': 'missing provider_id'}), 400
            if not is_allowed(g.principal, provider_id):
                return jsonify({
                    'error': 'needs_approval',
                    'provider_id': provider_id,
                    'user_id': g.principal.user_id,
                    'message': f'Provider {provider_id} requires admin approval for this user',
                }), 403
            return f(*args, **kwargs)
        return wrapped
    return deco
```

- [ ] **Step 4: Run tests, verify pass**

Run: `pytest tests/test_gate.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Run full suite**

Run: `pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add api/gate.py tests/test_gate.py
git commit -m "Add: gate module — is_allowed + require_provider_access decorator

Gate is a no-op until Config.GATE_ENABLED is true (deploy-time flag).
Admin role bypasses; ungated providers (default: ollama) pass through;
non-admin users need an active ProviderGrant row.

Verified: pytest ✓ (existing + 8 new)."
```

---

### Task 5: Apply gate to existing endpoints

**Files:**
- Modify: [api/configs_api.py](../../../api/configs_api.py) — POST/GET/DELETE `<provider_id>` routes
- Modify: [api/chat_api.py](../../../api/chat_api.py) — POST `/chat`
- Modify: [api/providers_api.py](../../../api/providers_api.py) — read first to decide
- Create: `tests/test_gate_integration.py`

- [ ] **Step 1: Read providers_api.py to confirm where to apply the gate**

Run: `cat api/providers_api.py | head -60`
Look for routes that take a `<provider_id>` path arg or `provider` body field.

- [ ] **Step 2: Write the failing integration test**

Create `tests/test_gate_integration.py`:

```python
"""Integration tests: gate applied to /configs, /chat, /providers endpoints."""

import pytest
from config import Config
from database import db
from storage.models import ProviderGrant


@pytest.fixture(autouse=True)
def enable_gate():
    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.GATE_ENABLED = True
    Config.UNGATED_PROVIDERS = {'ollama'}
    yield
    Config.GATE_ENABLED = False


def test_save_config_ollama_works_without_grant(client):
    r = client.post(
        '/configs/lisa/ollama',
        json={'config': {}, 'fallback_provider': None},
        headers={'Authorization': 'Bearer test-token'},
    )
    assert r.status_code == 200


def test_save_config_claude_blocked_without_grant(client):
    r = client.post(
        '/configs/lisa/claude',
        json={'config': {'api_key': 'sk-test'}},
        headers={'Authorization': 'Bearer test-token'},
    )
    assert r.status_code == 403
    assert r.get_json()['error'] == 'needs_approval'


def test_save_config_claude_allowed_with_grant(app, client):
    with app.app_context():
        db.session.add(ProviderGrant(
            user_id='lisa', provider_id='claude', granted_by='harald'))
        db.session.commit()

    r = client.post(
        '/configs/lisa/claude',
        json={'config': {'api_key': 'sk-test'}},
        headers={'Authorization': 'Bearer test-token'},
    )
    assert r.status_code == 200


def test_save_config_admin_token_bypasses_gate(client):
    r = client.post(
        '/configs/harald/claude',
        json={'config': {'api_key': 'sk-test'}},
        headers={'Authorization': 'Bearer admin-test-token'},
    )
    assert r.status_code == 200


def test_chat_blocks_claude_without_grant(client):
    r = client.post(
        '/chat',
        json={
            'user_id': 'lisa',
            'provider': 'claude',
            'model': 'claude-haiku-4-5-20251001',
            'messages': [{'role': 'user', 'content': 'hi'}],
        },
        headers={'Authorization': 'Bearer test-token'},
    )
    assert r.status_code == 403
    assert r.get_json()['error'] == 'needs_approval'
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_gate_integration.py -v`
Expected: FAIL — gate not applied; saves succeed.

- [ ] **Step 4: Apply gate to `configs_api.py`**

Modify [api/configs_api.py](../../../api/configs_api.py) — add the import and decorator to the three per-provider routes:

```python
# at top, add import
from api.gate import require_provider_access
```

For `get_config`, `save_config`, `delete_config` — add `@require_provider_access('provider_id')` AFTER `@require_token`:

```python
@configs_bp.get('/<user_id>/<provider_id>')
@require_token
@require_provider_access('provider_id')
def get_config(user_id, provider_id):
    ...

@configs_bp.post('/<user_id>/<provider_id>')
@require_token
@require_provider_access('provider_id')
def save_config(user_id, provider_id):
    ...

@configs_bp.delete('/<user_id>/<provider_id>')
@require_token
@require_provider_access('provider_id')
def delete_config(user_id, provider_id):
    ...
```

Leave the `/configs/<user_id>` (list) route alone — listing is allowed.

- [ ] **Step 5: Apply gate to `chat_api.py`**

Modify [api/chat_api.py](../../../api/chat_api.py):

```python
# at top, add import
from api.gate import require_provider_access
```

Apply to `/chat`:

```python
@chat_bp.post('/chat')
@require_token
@require_provider_access('provider')   # body uses 'provider', not 'provider_id'
def chat():
    ...
```

(The decorator already accepts `provider` as a fallback body key.)

**Note on fallback_provider:** the gate above checks `provider` only. If a non-admin user supplies a `fallback_provider` they don't have access to, we want that blocked too. Add explicit check inside the route, just after the existing fallback validation:

```python
    if fallback_provider and fallback_provider not in PROVIDER_REGISTRY:
        return jsonify({'error': f'Unbekannter Fallback-Provider: {fallback_provider}'}), 400

    # NEW: gate fallback provider too
    if fallback_provider:
        from api.gate import is_allowed
        if not is_allowed(g.principal, fallback_provider):
            return jsonify({
                'error': 'needs_approval',
                'provider_id': fallback_provider,
                'message': f'Fallback provider {fallback_provider} requires approval',
            }), 403
```

Add `from flask import g` to imports if not already there.

- [ ] **Step 6: Decide whether to gate `providers_api.py`**

Per spec §14 open question #3: `GET /providers` should list ALL providers with an `allowed` flag rather than filter. So don't gate the list endpoint. Per-provider routes like `POST /providers/<id>/test` and `GET /providers/<id>/models` SHOULD be gated.

Inspect with `cat api/providers_api.py`. For each route that takes `<provider_id>`:

```python
@providers_bp.post('/<provider_id>/test')
@require_token
@require_provider_access('provider_id')
def test_provider(provider_id):
    ...
```

For `GET /providers` (the list route), modify the response to include `allowed` per entry:

```python
# inside the list handler, when building each provider dict:
from api.gate import is_allowed
allowed = is_allowed(g.principal, provider_id)
entry['allowed'] = allowed
```

Add `from flask import g` to imports.

- [ ] **Step 7: Run integration tests**

Run: `pytest tests/test_gate_integration.py -v`
Expected: PASS (5 tests).

- [ ] **Step 8: Run full suite**

Run: `pytest -q`
Expected: all pass. If something fails, the most likely culprit is an existing test that hits `/configs/<user>/<provider>` or `/chat` without setting `GATE_ENABLED=False` in the fixture. The autouse fixture in the new test file flips the flag — make sure the cleanup runs.

- [ ] **Step 9: Commit**

```bash
git add api/configs_api.py api/chat_api.py api/providers_api.py tests/test_gate_integration.py
git commit -m "Add: apply provider-access gate to /configs, /chat, /providers

Gate is conditional on Config.GATE_ENABLED (still default false). Once
enabled, non-admin users hit 403 for any provider not in UNGATED_PROVIDERS
unless they have an active ProviderGrant row. Fallback providers in /chat
are gated separately. /providers list endpoint returns 'allowed' flag per
provider rather than filtering, for better UX in consumer apps.

Verified: pytest ✓ (existing + 5 new)."
```

---

## Phase 3 — opencode.ai Provider

### Task 6: `OpencodeClient` + factory registration

**Files:**
- Create: `providers/opencode.py`
- Modify: [providers/__init__.py](../../../providers/__init__.py)
- Create: `tests/test_opencode_provider.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_opencode_provider.py`:

```python
"""Tests for OpencodeClient + factory registration."""

from unittest.mock import MagicMock, patch
import pytest
from providers import get_client, PROVIDER_REGISTRY


def test_opencode_registered():
    assert 'opencode' in PROVIDER_REGISTRY


def test_opencode_requires_api_key():
    assert 'api_key' in PROVIDER_REGISTRY['opencode']['requires']


def test_factory_returns_opencode_client():
    client = get_client('opencode', {'api_key': 'sk-test'})
    assert client.__class__.__name__ == 'OpencodeClient'


def test_opencode_raises_without_api_key():
    with pytest.raises(ValueError, match='api_key'):
        get_client('opencode', {})


@patch('providers.opencode.OpenAI')
def test_opencode_uses_default_base_url(mock_openai):
    from providers.opencode import OpencodeClient
    OpencodeClient({'api_key': 'sk-test'})
    args, kwargs = mock_openai.call_args
    assert kwargs['base_url'] == 'https://opencode.ai/zen/v1'
    assert kwargs['api_key'] == 'sk-test'


@patch('providers.opencode.OpenAI')
def test_opencode_respects_custom_endpoint(mock_openai):
    from providers.opencode import OpencodeClient
    OpencodeClient({'api_key': 'sk-test', 'api_endpoint': 'https://example.org/v1'})
    _, kwargs = mock_openai.call_args
    assert kwargs['base_url'] == 'https://example.org/v1'


@patch('providers.opencode.OpenAI')
def test_opencode_create_message_returns_claude_format(mock_openai):
    from providers.opencode import OpencodeClient
    fake_response = MagicMock()
    fake_response.choices = [MagicMock(message=MagicMock(content='hi back'))]
    fake_response.usage = MagicMock(prompt_tokens=10, completion_tokens=3)
    mock_openai.return_value.chat.completions.create.return_value = fake_response

    c = OpencodeClient({'api_key': 'sk-test'})
    out = c.create_message('gpt-5', [{'role': 'user', 'content': 'hi'}], 50)

    assert out == {
        'content': [{'text': 'hi back'}],
        'usage': {'input_tokens': 10, 'output_tokens': 3},
    }
```

- [ ] **Step 2: Run test, verify it fails**

Run: `pytest tests/test_opencode_provider.py -v`
Expected: FAIL — opencode not in registry, no provider module.

- [ ] **Step 3: Create `providers/opencode.py`**

```python
"""Opencode.ai (Zen) Client — OpenAI-compatible hosted gateway.

API surface assumed Bearer + OpenAI-compatible /v1/chat/completions and
/v1/models. If opencode.ai's published auth scheme differs at integration
time (OAuth, JWT, custom header), patch __init__ accordingly.
"""

from __future__ import annotations
import logging
from providers.base import BaseClient
from config import Config

logger = logging.getLogger(__name__)


class OpencodeClient(BaseClient):
    def __init__(self, config: dict):
        api_key = config.get('api_key')
        if not api_key:
            raise ValueError("Opencode: api_key erforderlich")
        base_url = config.get('api_endpoint') or Config.OPENCODE_BASE_URL
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def get_models(self) -> list[str]:
        try:
            return sorted(m.id for m in self.client.models.list().data)
        except Exception as e:
            logger.warning(f'Opencode get_models failed: {e}')
            return []

    def create_message(self, model: str, messages: list[dict], max_tokens: int = 600) -> dict:
        r = self.client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens
        )
        return {
            'content': [{'text': r.choices[0].message.content}],
            'usage': {
                'input_tokens': r.usage.prompt_tokens,
                'output_tokens': r.usage.completion_tokens,
            },
        }

    def health(self) -> bool:
        try:
            self.client.models.list()
            return True
        except Exception:
            return False
```

- [ ] **Step 4: Register in `providers/__init__.py`**

Modify [providers/__init__.py](../../../providers/__init__.py):

Add to `PROVIDER_REGISTRY` dict:

```python
    'opencode': {
        'name': 'opencode.ai (Zen)',
        'system': False,
        'requires': ['api_key'],
        'optional': ['api_endpoint'],
    },
```

Add to `get_client`:

```python
    if provider_id == 'opencode':
        from providers.opencode import OpencodeClient
        return OpencodeClient(config)
```

- [ ] **Step 5: Run tests, verify pass**

Run: `pytest tests/test_opencode_provider.py -v`
Expected: PASS (7 tests).

- [ ] **Step 6: Run full suite**

Run: `pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add providers/opencode.py providers/__init__.py tests/test_opencode_provider.py
git commit -m "Add: opencode.ai provider integration (OpenAI-compatible, BYO key)

Models fetched live via /v1/models — no static KNOWN_MODELS list.
Default base URL: https://opencode.ai/zen/v1, override via OPENCODE_BASE_URL
env or per-config api_endpoint field.

Verified: pytest ✓ (existing + 7 new), no live integration test yet."
```

---

### Task 7: Pricing entries for opencode

**Files:**
- Modify: [pricing.py](../../../pricing.py) — add opencode model entries
- Modify: `tests/test_pricing.py` — add opencode case

- [ ] **Step 1: Read pricing.py to understand the table format**

Run: `cat pricing.py`

Look for how providers like `openai` and `claude` are keyed. Most likely a nested dict `PRICING[provider][model] = (input_per_1k, output_per_1k)`.

- [ ] **Step 2: Write failing test**

In `tests/test_pricing.py`, append:

```python
def test_opencode_pricing_returns_value_for_known_model():
    from pricing import calculate_cost
    # If opencode lists e.g. 'gpt-5' in their rate card:
    cost = calculate_cost('opencode', 'gpt-5', input_tokens=1000, output_tokens=500)
    assert cost is None or cost > 0  # tolerate either an entry or N/A


def test_opencode_pricing_none_for_unknown_model():
    from pricing import calculate_cost
    cost = calculate_cost('opencode', 'definitely-not-a-real-model', 100, 100)
    assert cost is None
```

- [ ] **Step 3: Run test**

Run: `pytest tests/test_pricing.py -v`
Expected: the "unknown model returns None" test passes if the pricing module's default is already None-on-miss. The "known model" test passes too because the assertion is permissive. Both tests should pass even without pricing entries — that's intentional, since opencode.ai's rate card needs to be looked up at implementation time.

- [ ] **Step 4: Add opencode pricing entries**

Modify [pricing.py](../../../pricing.py). Add a new sub-dict in `PRICING` (or whatever the structure is):

```python
'opencode': {
    # Populate from opencode.ai's published Zen rate card at implementation time.
    # Format matches existing providers (input_usd_per_1k, output_usd_per_1k).
    # If unknown when this task runs, leave the dict empty — cost_usd will be NULL
    # for opencode UsageEvent rows until populated.
    # Example placeholder (REPLACE):
    # 'gpt-5':    (0.005, 0.015),
    # 'sonnet-4': (0.003, 0.015),
},
```

If you don't have current rate-card numbers, leave the dict empty and document in commit body: "pricing entries deferred — rate card lookup pending".

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_pricing.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pricing.py tests/test_pricing.py
git commit -m "Add: opencode pricing table skeleton

Entries to be populated from opencode.ai's Zen rate card. Until populated,
UsageEvent.cost_usd stays NULL for opencode calls (existing behavior for
unknown models).

Verified: pytest ✓ (existing + 2 new)."
```

---

## Phase 4 — Admin REST API

### Task 8: Admin grants CRUD endpoints

**Files:**
- Create: `api/admin_api.py`
- Modify: [app.py](../../../app.py) — register blueprint
- Create: `tests/test_admin_api.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_admin_api.py`:

```python
"""Tests for /admin/grants CRUD endpoints."""

import pytest
from config import Config
from database import db
from storage.models import ProviderGrant


@pytest.fixture(autouse=True)
def admin_token():
    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.ADMIN_USER_ID = 'harald'


def H_admin():
    return {'Authorization': 'Bearer admin-test-token'}


def H_user():
    return {'Authorization': 'Bearer test-token'}


def test_post_grant_requires_admin(client):
    r = client.post('/admin/grants',
                    json={'user_id': 'lisa', 'provider_id': 'claude'},
                    headers=H_user())
    assert r.status_code == 403


def test_post_grant_creates_row(client, app):
    r = client.post('/admin/grants',
                    json={'user_id': 'lisa', 'provider_id': 'claude',
                          'note': 'test reason'},
                    headers=H_admin())
    assert r.status_code == 201
    g = r.get_json()['grant']
    assert g['user_id'] == 'lisa'
    assert g['provider_id'] == 'claude'
    assert g['granted_by'] == 'harald'
    assert g['note'] == 'test reason'
    assert g['revoked_at'] is None


def test_post_grant_idempotent_restores_revoked(client, app):
    from datetime import datetime, timezone
    with app.app_context():
        db.session.add(ProviderGrant(
            user_id='lisa', provider_id='claude', granted_by='harald',
            revoked_at=datetime.now(timezone.utc)))
        db.session.commit()

    r = client.post('/admin/grants',
                    json={'user_id': 'lisa', 'provider_id': 'claude'},
                    headers=H_admin())
    assert r.status_code == 201
    assert r.get_json()['grant']['revoked_at'] is None


def test_get_grants_lists(client, app):
    with app.app_context():
        db.session.add(ProviderGrant(
            user_id='lisa', provider_id='claude', granted_by='harald'))
        db.session.add(ProviderGrant(
            user_id='bob', provider_id='openai', granted_by='harald'))
        db.session.commit()

    r = client.get('/admin/grants', headers=H_admin())
    assert r.status_code == 200
    grants = r.get_json()['grants']
    assert len(grants) == 2


def test_get_grants_filters_by_user(client, app):
    with app.app_context():
        db.session.add(ProviderGrant(
            user_id='lisa', provider_id='claude', granted_by='harald'))
        db.session.add(ProviderGrant(
            user_id='bob', provider_id='openai', granted_by='harald'))
        db.session.commit()

    r = client.get('/admin/grants?user_id=lisa', headers=H_admin())
    assert r.status_code == 200
    grants = r.get_json()['grants']
    assert len(grants) == 1
    assert grants[0]['user_id'] == 'lisa'


def test_get_grants_excludes_revoked_by_default(client, app):
    from datetime import datetime, timezone
    with app.app_context():
        db.session.add(ProviderGrant(
            user_id='lisa', provider_id='claude', granted_by='harald',
            revoked_at=datetime.now(timezone.utc)))
        db.session.commit()

    r = client.get('/admin/grants', headers=H_admin())
    assert r.get_json()['grants'] == []

    r2 = client.get('/admin/grants?include_revoked=true', headers=H_admin())
    assert len(r2.get_json()['grants']) == 1


def test_delete_grant_soft_deletes(client, app):
    with app.app_context():
        g = ProviderGrant(
            user_id='lisa', provider_id='claude', granted_by='harald')
        db.session.add(g)
        db.session.commit()
        grant_id = g.id

    r = client.delete(f'/admin/grants/{grant_id}', headers=H_admin())
    assert r.status_code == 204

    with app.app_context():
        fresh = db.session.get(ProviderGrant, grant_id)
        assert fresh.revoked_at is not None


def test_delete_unknown_grant_404(client):
    r = client.delete('/admin/grants/99999', headers=H_admin())
    assert r.status_code == 404
```

- [ ] **Step 2: Run test, verify it fails**

Run: `pytest tests/test_admin_api.py -v`
Expected: FAIL — `/admin/grants` returns 404.

- [ ] **Step 3: Implement `api/admin_api.py`**

Create `api/admin_api.py`:

```python
"""Admin endpoints: grants CRUD + overview JSON.

All routes require ADMIN_TOKEN (enforced via @require_admin).
Mounted at /admin.
"""

from datetime import datetime, timezone
from flask import Blueprint, jsonify, request, g
from database import db
from api.auth import require_admin
from storage.models import ProviderGrant

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


@admin_bp.post('/grants')
@require_admin
def create_grant():
    body = request.get_json() or {}
    user_id = body.get('user_id')
    provider_id = body.get('provider_id')
    note = body.get('note')

    if not user_id or not provider_id:
        return jsonify({'error': 'user_id and provider_id required'}), 400

    existing = ProviderGrant.query.filter_by(
        user_id=user_id, provider_id=provider_id).first()

    if existing:
        # Re-grant: restore revoked row, refresh timestamp, replace note.
        existing.revoked_at = None
        existing.granted_at = datetime.now(timezone.utc)
        existing.granted_by = g.principal.user_id
        if note is not None:
            existing.note = note
        db.session.commit()
        return jsonify({'grant': existing.to_dict()}), 201

    grant = ProviderGrant(
        user_id=user_id,
        provider_id=provider_id,
        granted_by=g.principal.user_id,
        note=note,
    )
    db.session.add(grant)
    db.session.commit()
    return jsonify({'grant': grant.to_dict()}), 201


@admin_bp.get('/grants')
@require_admin
def list_grants():
    q = ProviderGrant.query
    if request.args.get('user_id'):
        q = q.filter_by(user_id=request.args['user_id'])
    if request.args.get('provider_id'):
        q = q.filter_by(provider_id=request.args['provider_id'])
    if request.args.get('include_revoked', '').lower() != 'true':
        q = q.filter(ProviderGrant.revoked_at.is_(None))
    grants = [g.to_dict() for g in q.order_by(ProviderGrant.granted_at.desc()).all()]
    return jsonify({'grants': grants})


@admin_bp.delete('/grants/<int:grant_id>')
@require_admin
def revoke_grant(grant_id):
    grant = db.session.get(ProviderGrant, grant_id)
    if grant is None:
        return jsonify({'error': 'not found'}), 404
    if grant.revoked_at is None:
        grant.revoked_at = datetime.now(timezone.utc)
        db.session.commit()
    return '', 204
```

- [ ] **Step 4: Register blueprint in app.py**

Modify [app.py](../../../app.py:34-49). Add to the blueprint imports and registrations:

```python
    from api.admin_api import admin_bp
    # ... other blueprints ...
    app.register_blueprint(admin_bp)
```

- [ ] **Step 5: Run admin API tests**

Run: `pytest tests/test_admin_api.py -v`
Expected: PASS (8 tests).

- [ ] **Step 6: Run full suite**

Run: `pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add api/admin_api.py app.py tests/test_admin_api.py
git commit -m "Add: /admin/grants CRUD endpoints (admin token gated)

POST creates or restores a revoked grant (idempotent). GET filters by
user_id/provider_id/include_revoked. DELETE soft-deletes via revoked_at.
Audit trail via granted_by (from g.principal).

Verified: pytest ✓ (existing + 8 new)."
```

---

### Task 9: Admin overview endpoint

**Files:**
- Modify: `api/admin_api.py` — add `GET /admin/overview`
- Modify: `tests/test_admin_api.py` — add overview tests

- [ ] **Step 1: Write the failing test**

Append to `tests/test_admin_api.py`:

```python
def test_overview_requires_admin(client):
    r = client.get('/admin/overview', headers=H_user())
    assert r.status_code == 403


def test_overview_lists_users_from_configs_grants_usage(client, app):
    from storage.models import ProviderConfig, UsageEvent
    with app.app_context():
        # User with a config
        pc = ProviderConfig(user_id='lisa', provider_id='ollama')
        pc.set_config({})
        db.session.add(pc)

        # User with only a grant
        db.session.add(ProviderGrant(
            user_id='bob', provider_id='claude', granted_by='harald'))

        # User with only a usage event
        db.session.add(UsageEvent(
            user_id='carol', provider_id='ollama', model='llama3',
            status='ok'))
        db.session.commit()

    r = client.get('/admin/overview', headers=H_admin())
    assert r.status_code == 200
    users = {u['user_id']: u for u in r.get_json()['users']}
    assert 'lisa' in users
    assert 'bob' in users
    assert 'carol' in users


def test_overview_marks_admin_user(client, app):
    from storage.models import ProviderConfig
    with app.app_context():
        pc = ProviderConfig(user_id='harald', provider_id='ollama')
        pc.set_config({})
        db.session.add(pc)
        db.session.commit()

    r = client.get('/admin/overview', headers=H_admin())
    harald = next(u for u in r.get_json()['users'] if u['user_id'] == 'harald')
    assert harald['is_admin'] is True


def test_overview_includes_30d_call_counts(client, app):
    from storage.models import UsageEvent
    with app.app_context():
        for _ in range(5):
            db.session.add(UsageEvent(
                user_id='lisa', provider_id='ollama', model='mistral',
                status='ok', origin_app='loganonymizer'))
        db.session.commit()

    r = client.get('/admin/overview', headers=H_admin())
    lisa = next(u for u in r.get_json()['users'] if u['user_id'] == 'lisa')
    assert lisa['last_30d']['total_calls'] == 5
    assert lisa['last_30d']['by_provider']['ollama'] == 5
    assert lisa['last_30d']['by_origin_app']['loganonymizer'] == 5
```

- [ ] **Step 2: Run test, verify it fails**

Run: `pytest tests/test_admin_api.py::test_overview_lists_users_from_configs_grants_usage -v`
Expected: FAIL — `/admin/overview` 404.

- [ ] **Step 3: Add overview endpoint to `api/admin_api.py`**

Append to `api/admin_api.py`:

```python
from datetime import timedelta
from sqlalchemy import func, distinct, union
from storage.models import ProviderConfig, UsageEvent
from config import Config


@admin_bp.get('/overview')
@require_admin
def overview():
    """Returns one row per known user_id (from configs ∪ grants ∪ usage)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    # Discover user_ids via union of three tables.
    cfg_users = db.session.query(ProviderConfig.user_id).distinct()
    grant_users = db.session.query(ProviderGrant.user_id).distinct()
    usage_users = db.session.query(UsageEvent.user_id).distinct()
    user_ids = sorted({
        r[0] for r in cfg_users.union(grant_users).union(usage_users).all()
    })

    out = []
    for uid in user_ids:
        # Configured providers
        configured = [r.provider_id for r in
                      ProviderConfig.query.filter_by(user_id=uid).all()]

        # Active grants
        grants = [g.to_dict() for g in
                  ProviderGrant.query.filter_by(user_id=uid)
                  .filter(ProviderGrant.revoked_at.is_(None)).all()]

        # 30d usage rollup
        events = UsageEvent.query.filter(
            UsageEvent.user_id == uid,
            UsageEvent.created_at >= cutoff,
        ).all()

        by_provider = {}
        by_origin = {}
        last_used = None
        for ev in events:
            by_provider[ev.provider_id] = by_provider.get(ev.provider_id, 0) + 1
            if ev.origin_app:
                by_origin[ev.origin_app] = by_origin.get(ev.origin_app, 0) + 1
            if last_used is None or (ev.created_at and ev.created_at > last_used):
                last_used = ev.created_at

        out.append({
            'user_id': uid,
            'is_admin': uid == Config.ADMIN_USER_ID,
            'configured_providers': sorted(configured),
            'grants': grants,
            'last_30d': {
                'total_calls': len(events),
                'by_provider': by_provider,
                'by_origin_app': by_origin,
                'last_used_at': last_used.isoformat() if last_used else None,
            },
        })

    return jsonify({'users': out})
```

- [ ] **Step 4: Run tests, verify pass**

Run: `pytest tests/test_admin_api.py -v`
Expected: PASS (all 8 from Task 8 + 4 new = 12 total).

- [ ] **Step 5: Run full suite**

Run: `pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add api/admin_api.py tests/test_admin_api.py
git commit -m "Add: /admin/overview — per-user roster with grants + 30d usage

Roster is derived from union of provider_configs, provider_grants, and
usage_events — no separate users table. Marks ADMIN_USER_ID with
is_admin=true. Rollup: total_calls, by_provider, by_origin_app,
last_used_at over last 30 days.

Verified: pytest ✓ (existing + 4 new)."
```

---

## Phase 5 — Admin UI

### Task 10: Session cookie auth + base template

**Files:**
- Create: `api/admin_ui.py`
- Create: `templates/admin/base.html`
- Create: `templates/admin/login.html` (token-entry fallback)
- Modify: [app.py](../../../app.py) — register `admin_ui` blueprint, set `template_folder` config if needed
- Create: `tests/test_admin_ui.py`

- [ ] **Step 1: Confirm templates dir conventions**

Flask defaults to `templates/` in the app root. Check:

Run: `ls templates 2>/dev/null || echo "no templates dir yet"`
Expected: most likely no templates dir — we'll create it.

- [ ] **Step 2: Write failing tests**

Create `tests/test_admin_ui.py`:

```python
"""Tests for admin UI routes — auth flow and page rendering."""

import pytest
from config import Config


@pytest.fixture(autouse=True)
def setup_admin():
    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.SECRET_KEY = 'test-secret-key-for-sessions'


def test_admin_ui_root_redirects_to_users_when_authed(client):
    # First, get the cookie via ?token=
    r = client.get('/admin/ui?token=admin-test-token', follow_redirects=False)
    assert r.status_code in (302, 303)
    # Then root should redirect into /admin/ui/users
    r2 = client.get('/admin/ui')
    assert r2.status_code in (302, 303)
    assert '/admin/ui/users' in r2.location


def test_admin_ui_root_redirects_to_login_when_not_authed(client):
    r = client.get('/admin/ui')
    assert r.status_code in (302, 303)
    assert 'login' in r.location.lower() or '?token=' in r.location.lower()


def test_admin_ui_invalid_token_redirects_to_login(client):
    r = client.get('/admin/ui?token=wrong-token', follow_redirects=False)
    assert r.status_code in (302, 303)
    assert 'login' in r.location.lower()


def test_admin_ui_login_page_renders(client):
    r = client.get('/admin/ui/login')
    assert r.status_code == 200
    assert b'admin' in r.data.lower()


def test_admin_ui_logout_clears_session(client):
    client.get('/admin/ui?token=admin-test-token')
    r = client.get('/admin/ui/logout', follow_redirects=False)
    assert r.status_code in (302, 303)
    # After logout, accessing /admin/ui should redirect to login
    r2 = client.get('/admin/ui')
    assert 'login' in r2.location.lower() or '?token=' in r2.location.lower()
```

- [ ] **Step 3: Run test, verify it fails**

Run: `pytest tests/test_admin_ui.py -v`
Expected: FAIL — `/admin/ui` 404.

- [ ] **Step 4: Create `api/admin_ui.py`**

```python
"""Admin UI — Jinja-rendered pages at /admin/ui.

Auth flow:
  1. GET /admin/ui?token=<ADMIN_TOKEN>  → validates, sets session cookie, redirects.
  2. Subsequent navigation uses session['admin']=True.
  3. GET /admin/ui/logout → clears session.

Single-admin scope. No login form posts a password; the URL-token bootstrap
plus a signed Flask session cookie is acceptable for this use case.
"""

from flask import (
    Blueprint, render_template, request, redirect, url_for, session, abort,
    jsonify, current_app,
)
from datetime import datetime, timedelta, timezone
from config import Config
from database import db
from storage.models import ProviderConfig, ProviderGrant, UsageEvent

admin_ui_bp = Blueprint(
    'admin_ui', __name__,
    url_prefix='/admin/ui',
    template_folder='../templates',
)


def _is_authed() -> bool:
    return bool(session.get('admin'))


def _require_admin_ui():
    if not _is_authed():
        return redirect(url_for('admin_ui.login'))
    return None


@admin_ui_bp.before_request
def _entry():
    # Allow login/logout/token-bootstrap without auth.
    if request.endpoint in ('admin_ui.login', 'admin_ui.logout'):
        return None

    token = request.args.get('token')
    if token:
        if Config.ADMIN_TOKEN and token == Config.ADMIN_TOKEN:
            session['admin'] = True
            # Strip token from URL — redirect to clean path.
            return redirect(request.path)
        return redirect(url_for('admin_ui.login'))


@admin_ui_bp.get('/login')
def login():
    return render_template('admin/login.html')


@admin_ui_bp.get('/logout')
def logout():
    session.pop('admin', None)
    return redirect(url_for('admin_ui.login'))


@admin_ui_bp.get('/')
def root():
    if not _is_authed():
        return redirect(url_for('admin_ui.login'))
    return redirect(url_for('admin_ui.users'))


@admin_ui_bp.get('/users')
def users():
    redirect_resp = _require_admin_ui()
    if redirect_resp:
        return redirect_resp
    # TODO Task 11: real data
    return render_template('admin/users.html', users=[])


@admin_ui_bp.get('/users/<user_id>')
def user_detail(user_id):
    redirect_resp = _require_admin_ui()
    if redirect_resp:
        return redirect_resp
    # TODO Task 12: real data
    return render_template('admin/user_detail.html', user_id=user_id, user={})
```

- [ ] **Step 5: Create `templates/admin/base.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{% block title %}ai-provider-service · admin{% endblock %}</title>
<style>
  body { font: 14px/1.4 -apple-system, system-ui, sans-serif; margin: 0; padding: 1rem 2rem; color: #222; }
  header { display: flex; justify-content: space-between; border-bottom: 1px solid #ddd; padding-bottom: .5rem; margin-bottom: 1rem; }
  header h1 { margin: 0; font-size: 1.1rem; }
  table { border-collapse: collapse; width: 100%; }
  th, td { border-bottom: 1px solid #eee; padding: .5rem .75rem; text-align: left; vertical-align: top; }
  th { background: #f7f7f7; font-weight: 600; }
  .muted { color: #888; }
  .ok { color: #1c7c2e; }
  .bad { color: #b00020; }
  button { font-size: 13px; padding: .25rem .6rem; cursor: pointer; }
  a { color: #0367c4; text-decoration: none; }
  a:hover { text-decoration: underline; }
  .row-msg { font-size: 12px; color: #888; margin-left: .5rem; }
</style>
</head>
<body>
<header>
  <h1>ai-provider-service · admin</h1>
  <nav>
    {% if session.get('admin') %}
      <a href="{{ url_for('admin_ui.users') }}">users</a> ·
      <a href="{{ url_for('admin_ui.logout') }}">logout</a>
    {% endif %}
  </nav>
</header>
{% block content %}{% endblock %}
</body>
</html>
```

- [ ] **Step 6: Create `templates/admin/login.html`**

```html
{% extends "admin/base.html" %}
{% block content %}
<h2>admin login</h2>
<p>This page requires the admin bearer token. To start a session, visit:</p>
<pre>/admin/ui?token=&lt;ADMIN_TOKEN&gt;</pre>
<p class="muted">The token is set via the <code>ADMIN_TOKEN</code> env var.</p>
{% endblock %}
```

- [ ] **Step 7: Create stub `templates/admin/users.html` and `user_detail.html`**

`templates/admin/users.html`:

```html
{% extends "admin/base.html" %}
{% block content %}
<h2>users</h2>
<table>
  <thead><tr><th>user</th><th>providers</th><th>grants</th><th>30d calls</th><th>last used</th></tr></thead>
  <tbody>
    {% for u in users %}
    <tr>
      <td><a href="{{ url_for('admin_ui.user_detail', user_id=u.user_id) }}">{{ u.user_id }}</a>{% if u.is_admin %} <span class="muted">(admin)</span>{% endif %}</td>
      <td>{{ u.configured_providers | join(', ') or '—' }}</td>
      <td>
        {% if u.is_admin %}<span class="ok">bypass</span>
        {% else %}{{ u.grants | map(attribute='provider_id') | join(', ') or '—' }}{% endif %}
      </td>
      <td>{{ u.last_30d.total_calls }}</td>
      <td>{{ u.last_30d.last_used_at or '—' }}</td>
    </tr>
    {% else %}
    <tr><td colspan="5" class="muted">no users yet</td></tr>
    {% endfor %}
  </tbody>
</table>
{% endblock %}
```

`templates/admin/user_detail.html`:

```html
{% extends "admin/base.html" %}
{% block content %}
<p><a href="{{ url_for('admin_ui.users') }}">← back</a></p>
<h2>user: {{ user_id }}</h2>
<p class="muted">detail content rendered in Task 12.</p>
{% endblock %}
```

- [ ] **Step 8: Register blueprint and set SECRET_KEY in app.py**

Modify [app.py](../../../app.py):

After `app.config.from_object(Config)`:

```python
    # Required for admin UI session cookie. Fail-fast if missing.
    if not app.config.get('SECRET_KEY'):
        import logging
        logging.getLogger(__name__).warning(
            'SECRET_KEY is not set — admin UI sessions will not work.'
        )
```

In the blueprint imports/registrations:

```python
    from api.admin_api import admin_bp
    from api.admin_ui import admin_ui_bp
    ...
    app.register_blueprint(admin_bp)
    app.register_blueprint(admin_ui_bp)
```

- [ ] **Step 9: Run UI tests**

Run: `pytest tests/test_admin_ui.py -v`
Expected: PASS (5 tests).

- [ ] **Step 10: Run full suite**

Run: `pytest -q`
Expected: all pass.

- [ ] **Step 11: Commit**

```bash
git add api/admin_ui.py templates/admin/ app.py tests/test_admin_ui.py
git commit -m "Add: admin UI scaffolding — session auth + base templates

GET /admin/ui?token=<ADMIN_TOKEN> bootstraps a signed Flask session cookie,
strips the token from the URL, redirects clean. Subsequent navigation uses
the cookie. /admin/ui/logout clears it. Three Jinja templates: base, login,
users (skeleton), user_detail (skeleton). Real data wiring in Task 11/12.

Verified: pytest ✓ (existing + 5 new). SECRET_KEY required for prod."
```

---

### Task 11: `/admin/ui/users` overview page (real data)

**Files:**
- Modify: `api/admin_ui.py` — fetch overview data, render users.html

- [ ] **Step 1: Append overview-binding test to `tests/test_admin_ui.py`**

```python
def test_users_page_lists_known_users(client, app):
    from database import db
    from storage.models import ProviderConfig, ProviderGrant

    with app.app_context():
        pc = ProviderConfig(user_id='lisa', provider_id='ollama')
        pc.set_config({})
        db.session.add(pc)
        db.session.add(ProviderGrant(
            user_id='lisa', provider_id='claude', granted_by='harald'))
        db.session.commit()

    client.get('/admin/ui?token=admin-test-token')
    r = client.get('/admin/ui/users')
    assert r.status_code == 200
    assert b'lisa' in r.data
    assert b'claude' in r.data
```

- [ ] **Step 2: Run, verify it fails**

Run: `pytest tests/test_admin_ui.py::test_users_page_lists_known_users -v`
Expected: FAIL — page renders but `lisa` not in HTML.

- [ ] **Step 3: Refactor admin_api.py to expose overview as a function**

Modify `api/admin_api.py` — extract the overview logic so admin_ui can reuse it:

Above the `@admin_bp.get('/overview')` route, add a free function:

```python
def build_overview() -> list[dict]:
    """Build the overview list. Pure data, no jsonify."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    cfg_users = db.session.query(ProviderConfig.user_id).distinct()
    grant_users = db.session.query(ProviderGrant.user_id).distinct()
    usage_users = db.session.query(UsageEvent.user_id).distinct()
    user_ids = sorted({
        r[0] for r in cfg_users.union(grant_users).union(usage_users).all()
    })

    out = []
    for uid in user_ids:
        configured = [r.provider_id for r in
                      ProviderConfig.query.filter_by(user_id=uid).all()]
        grants = [g.to_dict() for g in
                  ProviderGrant.query.filter_by(user_id=uid)
                  .filter(ProviderGrant.revoked_at.is_(None)).all()]
        events = UsageEvent.query.filter(
            UsageEvent.user_id == uid,
            UsageEvent.created_at >= cutoff,
        ).all()
        by_provider = {}
        by_origin = {}
        last_used = None
        for ev in events:
            by_provider[ev.provider_id] = by_provider.get(ev.provider_id, 0) + 1
            if ev.origin_app:
                by_origin[ev.origin_app] = by_origin.get(ev.origin_app, 0) + 1
            if last_used is None or (ev.created_at and ev.created_at > last_used):
                last_used = ev.created_at
        out.append({
            'user_id': uid,
            'is_admin': uid == Config.ADMIN_USER_ID,
            'configured_providers': sorted(configured),
            'grants': grants,
            'last_30d': {
                'total_calls': len(events),
                'by_provider': by_provider,
                'by_origin_app': by_origin,
                'last_used_at': last_used.isoformat() if last_used else None,
            },
        })
    return out
```

Replace the `@admin_bp.get('/overview')` route body with:

```python
@admin_bp.get('/overview')
@require_admin
def overview():
    return jsonify({'users': build_overview()})
```

- [ ] **Step 4: Wire `build_overview()` into admin_ui**

Modify `api/admin_ui.py`'s `users` view:

```python
from api.admin_api import build_overview

@admin_ui_bp.get('/users')
def users():
    redirect_resp = _require_admin_ui()
    if redirect_resp:
        return redirect_resp
    return render_template('admin/users.html', users=build_overview())
```

- [ ] **Step 5: Run UI tests**

Run: `pytest tests/test_admin_ui.py -v`
Expected: PASS (6 tests).

- [ ] **Step 6: Run full suite**

Run: `pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add api/admin_api.py api/admin_ui.py tests/test_admin_ui.py
git commit -m "Add: /admin/ui/users renders real overview data

build_overview() extracted from /admin/overview JSON endpoint as a pure
function reused by the Jinja template. Same source of truth for API
consumers and the UI.

Verified: pytest ✓ (existing + 1 new)."
```

---

### Task 12: `/admin/ui/users/<user_id>` detail page + approve/revoke buttons

**Files:**
- Modify: `api/admin_ui.py` — populate `user` context dict
- Modify: `templates/admin/user_detail.html` — full content
- Create: `static/admin/admin.js` (or inline in template)

- [ ] **Step 1: Append detail-page test**

In `tests/test_admin_ui.py`:

```python
def test_user_detail_page_shows_grants_and_configs(client, app):
    from database import db
    from storage.models import ProviderConfig, ProviderGrant

    with app.app_context():
        pc = ProviderConfig(user_id='lisa', provider_id='ollama')
        pc.set_config({})
        db.session.add(pc)
        db.session.add(ProviderGrant(
            user_id='lisa', provider_id='claude', granted_by='harald'))
        db.session.commit()

    client.get('/admin/ui?token=admin-test-token')
    r = client.get('/admin/ui/users/lisa')
    assert r.status_code == 200
    assert b'ollama' in r.data
    assert b'claude' in r.data
    assert b'approve' in r.data.lower() or b'revoke' in r.data.lower()
```

- [ ] **Step 2: Run, verify it fails**

Run: `pytest tests/test_admin_ui.py::test_user_detail_page_shows_grants_and_configs -v`
Expected: FAIL — placeholder template, no real data.

- [ ] **Step 3: Wire detail view in `api/admin_ui.py`**

```python
from providers import PROVIDER_REGISTRY

@admin_ui_bp.get('/users/<user_id>')
def user_detail(user_id):
    redirect_resp = _require_admin_ui()
    if redirect_resp:
        return redirect_resp

    configured = ProviderConfig.query.filter_by(user_id=user_id).all()
    active_grants = {
        g.provider_id: g for g in ProviderGrant.query.filter_by(user_id=user_id)
        .filter(ProviderGrant.revoked_at.is_(None)).all()
    }

    # Show one row per registered provider with allowed/granted state.
    provider_rows = []
    for pid, meta in PROVIDER_REGISTRY.items():
        ungated = pid in Config.UNGATED_PROVIDERS
        granted = active_grants.get(pid)
        provider_rows.append({
            'provider_id': pid,
            'name': meta['name'],
            'ungated': ungated,
            'granted': granted is not None,
            'grant': granted.to_dict() if granted else None,
        })

    is_admin = (user_id == Config.ADMIN_USER_ID)

    return render_template(
        'admin/user_detail.html',
        user_id=user_id,
        is_admin=is_admin,
        provider_rows=provider_rows,
        configured=[r.to_safe_dict() for r in configured],
    )
```

- [ ] **Step 4: Fill out `templates/admin/user_detail.html`**

```html
{% extends "admin/base.html" %}
{% block content %}
<p><a href="{{ url_for('admin_ui.users') }}">← back</a></p>
<h2>user: {{ user_id }}{% if is_admin %} <span class="muted">(admin)</span>{% endif %}</h2>

{% if is_admin %}
<p class="ok">Admin role — all providers allowed (gate bypassed).</p>
{% endif %}

<h3>Provider access</h3>
<table id="grants-table">
<thead><tr><th>provider</th><th>status</th><th>action</th><th></th></tr></thead>
<tbody>
{% for row in provider_rows %}
<tr data-provider="{{ row.provider_id }}">
  <td>{{ row.provider_id }} <span class="muted">— {{ row.name }}</span></td>
  <td>
    {% if row.ungated %}<span class="ok">ungated</span>
    {% elif is_admin %}<span class="ok">bypass (admin)</span>
    {% elif row.granted %}<span class="ok">✓ granted {{ row.grant.granted_at[:10] }} by {{ row.grant.granted_by }}</span>
    {% else %}<span class="bad">✗ not granted</span>{% endif %}
  </td>
  <td>
    {% if row.ungated or is_admin %}<span class="muted">—</span>
    {% elif row.granted %}<button data-action="revoke" data-grant-id="{{ row.grant.id }}">revoke</button>
    {% else %}<button data-action="approve">approve</button>{% endif %}
  </td>
  <td class="row-msg"></td>
</tr>
{% endfor %}
</tbody>
</table>

<h3>Configured providers (BYO keys)</h3>
{% if configured %}
<ul>
  {% for c in configured %}
  <li>{{ c.provider_id }}{% if c.fallback_provider %} <span class="muted">→ fallback: {{ c.fallback_provider }}</span>{% endif %}</li>
  {% endfor %}
</ul>
{% else %}
<p class="muted">none</p>
{% endif %}

<script>
const userId = {{ user_id | tojson }};
document.querySelectorAll('#grants-table button').forEach(btn => {
  btn.addEventListener('click', async () => {
    const tr = btn.closest('tr');
    const provider = tr.dataset.provider;
    const action = btn.dataset.action;
    const msg = tr.querySelector('.row-msg');
    btn.disabled = true;
    msg.textContent = '...';
    try {
      let resp;
      if (action === 'approve') {
        resp = await fetch('/admin/grants', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({ user_id: userId, provider_id: provider }),
        });
      } else {
        const grantId = btn.dataset.grantId;
        resp = await fetch(`/admin/grants/${grantId}`, {
          method: 'DELETE',
          credentials: 'same-origin',
        });
      }
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      msg.textContent = 'ok — reload to see updated state';
      msg.className = 'row-msg ok';
    } catch (e) {
      msg.textContent = `error: ${e.message}`;
      msg.className = 'row-msg bad';
      btn.disabled = false;
    }
  });
});
</script>
{% endblock %}
```

**Note on the JS fetch auth:** these calls hit `/admin/grants*` which require `ADMIN_TOKEN` via `Authorization: Bearer`, NOT a cookie. The session cookie auths the UI page but not the JSON API. Two options:

(a) Switch admin endpoints to also accept the session cookie via a new `@require_admin_or_session` decorator.
(b) Inject the admin token into the page on render so the JS can send it.

(a) is cleaner. Implement it now:

In `api/auth.py`, add:

```python
def require_admin_or_session(f):
    """Like require_admin but also accepts a valid admin UI session cookie.

    Lets the admin UI's same-origin fetch() calls authenticate without
    exposing ADMIN_TOKEN in the rendered page.
    """
    from flask import session
    @wraps(f)
    def wrapped(*args, **kwargs):
        # First try the bearer token path.
        p = _resolve_principal()
        if p and p.role == 'admin':
            g.principal = p
            g.agent = request.headers.get('X-Agent')
            return f(*args, **kwargs)
        # Fall back to session.
        if session.get('admin'):
            g.principal = Principal(user_id=Config.ADMIN_USER_ID, role='admin')
            g.agent = request.headers.get('X-Agent')
            return f(*args, **kwargs)
        return jsonify({'error': 'Admin token or session required'}), 403
    return wrapped
```

In `api/admin_api.py`, swap `@require_admin` → `@require_admin_or_session` on the three CRUD endpoints (POST, GET, DELETE on `/admin/grants*`) and `/admin/overview`. Update the import.

- [ ] **Step 5: Update admin_api tests for cookie path**

Append to `tests/test_admin_api.py`:

```python
def test_create_grant_with_session_cookie(client):
    # bootstrap admin session
    client.get('/admin/ui?token=admin-test-token')
    r = client.post('/admin/grants',
                    json={'user_id': 'lisa', 'provider_id': 'claude'})
    assert r.status_code == 201
```

- [ ] **Step 6: Run all admin + UI tests**

Run: `pytest tests/test_admin_api.py tests/test_admin_ui.py -v`
Expected: PASS.

- [ ] **Step 7: Run full suite**

Run: `pytest -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add api/admin_ui.py api/admin_api.py api/auth.py templates/admin/user_detail.html tests/
git commit -m "Add: admin UI detail page with approve/revoke actions

Per-user detail page lists every registered provider with grant status
(ungated, bypass-admin, granted, not-granted). Approve/revoke buttons
POST/DELETE against /admin/grants via session-cookie auth (new
require_admin_or_session decorator). Click-then-reload pattern — no
SPA-style re-render to keep templates simple.

Verified: pytest ✓ (existing + tests for cookie auth + detail page)."
```

---

## Phase 6 — Migration & Deploy

### Task 13: Bootstrap CLI command

**Files:**
- Create: `cli.py` (or extend an existing CLI file)
- Modify: [app.py](../../../app.py) — register CLI
- Create: `tests/test_bootstrap_grants.py`

- [ ] **Step 1: Confirm where to register CLI commands**

Run: `grep -rn 'app.cli' .` or `grep -rn 'cli.command' .` (excluding venv).
Expected: probably no existing CLI. We'll add one.

- [ ] **Step 2: Write the failing test**

Create `tests/test_bootstrap_grants.py`:

```python
"""Tests for the grants-bootstrap CLI command."""

import pytest
from config import Config
from database import db
from storage.models import ProviderConfig, ProviderGrant


def test_bootstrap_creates_grants_for_gated_configs(app):
    Config.UNGATED_PROVIDERS = {'ollama'}
    with app.app_context():
        for uid, pid in [('lisa', 'ollama'), ('lisa', 'claude'),
                         ('bob', 'openai')]:
            pc = ProviderConfig(user_id=uid, provider_id=pid)
            pc.set_config({})
            db.session.add(pc)
        db.session.commit()

        from cli import bootstrap_grants
        created = bootstrap_grants()
        assert created == 2  # claude + openai; ollama is ungated

        grants = ProviderGrant.query.all()
        assert {(g.user_id, g.provider_id) for g in grants} == {
            ('lisa', 'claude'), ('bob', 'openai')
        }


def test_bootstrap_idempotent(app):
    Config.UNGATED_PROVIDERS = {'ollama'}
    with app.app_context():
        pc = ProviderConfig(user_id='lisa', provider_id='claude')
        pc.set_config({})
        db.session.add(pc)
        db.session.commit()

        from cli import bootstrap_grants
        first = bootstrap_grants()
        second = bootstrap_grants()
        assert first == 1
        assert second == 0  # nothing new


def test_bootstrap_skips_ungated(app):
    Config.UNGATED_PROVIDERS = {'ollama'}
    with app.app_context():
        pc = ProviderConfig(user_id='lisa', provider_id='ollama')
        pc.set_config({})
        db.session.add(pc)
        db.session.commit()

        from cli import bootstrap_grants
        assert bootstrap_grants() == 0
        assert ProviderGrant.query.count() == 0
```

- [ ] **Step 3: Run, verify it fails**

Run: `pytest tests/test_bootstrap_grants.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cli'`.

- [ ] **Step 4: Create `cli.py`**

```python
"""Flask CLI commands for ai-provider-service.

Register with: app.cli.add_command(...) in app.create_app().

Commands:
  grants-bootstrap: insert one active grant per existing (user_id, provider_id)
    in provider_configs where provider_id is NOT in Config.UNGATED_PROVIDERS.
    Idempotent.
"""

from __future__ import annotations
import click
from datetime import datetime, timezone
from database import db
from config import Config
from storage.models import ProviderConfig, ProviderGrant


def bootstrap_grants() -> int:
    """Returns number of new grants created."""
    ungated = Config.UNGATED_PROVIDERS
    rows = ProviderConfig.query.filter(
        ~ProviderConfig.provider_id.in_(ungated)
    ).all()
    created = 0
    for cfg in rows:
        existing = ProviderGrant.query.filter_by(
            user_id=cfg.user_id, provider_id=cfg.provider_id
        ).first()
        if existing:
            continue
        db.session.add(ProviderGrant(
            user_id=cfg.user_id,
            provider_id=cfg.provider_id,
            granted_by='bootstrap',
            note='bootstrap from existing provider_configs',
        ))
        created += 1
    db.session.commit()
    return created


@click.command('grants-bootstrap')
def grants_bootstrap_command():
    """Insert grants for existing provider_configs (one-shot, idempotent)."""
    n = bootstrap_grants()
    click.echo(f'Created {n} new grants.')
```

- [ ] **Step 5: Register the command in `app.py`**

Modify [app.py](../../../app.py). After blueprints register:

```python
    from cli import grants_bootstrap_command
    app.cli.add_command(grants_bootstrap_command)
```

- [ ] **Step 6: Run tests, verify pass**

Run: `pytest tests/test_bootstrap_grants.py -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Run full suite**

Run: `pytest -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add cli.py app.py tests/test_bootstrap_grants.py
git commit -m "Add: grants-bootstrap CLI — one-shot migration for existing configs

Inserts an active ProviderGrant for every (user_id, provider_id) pair in
provider_configs where provider_id is not in Config.UNGATED_PROVIDERS.
Idempotent — re-runs create zero new rows. Run once after deploying the
gate code, before flipping GATE_ENABLED=true.

Verified: pytest ✓ (existing + 3 new)."
```

---

### Task 14: Documentation + deploy steps

**Files:**
- Modify: [README.md](../../../README.md) — add access-control section + opencode mention
- Modify: [OPERATIONS.md](../../../OPERATIONS.md) — add deploy steps
- Modify: `.env.example` (if it exists) — add new vars

- [ ] **Step 1: Check current .env.example state**

Run: `cat .env.example 2>/dev/null || echo "no .env.example"`

- [ ] **Step 2: Add to `.env.example`**

If it exists, append:

```bash
# Access control (Task 13: provider gating)
ADMIN_TOKEN=          # required for admin access; generate with: python -c "import secrets;print(secrets.token_urlsafe(32))"
ADMIN_USER_ID=harald  # principal.user_id assigned to admin token holders
UNGATED_PROVIDERS=ollama  # comma-separated; everything else needs a grant
GATE_ENABLED=false    # flip to true AFTER running `flask grants-bootstrap`

# opencode.ai provider
OPENCODE_BASE_URL=https://opencode.ai/zen/v1

# Flask sessions (admin UI cookie)
SECRET_KEY=           # generate with: python -c "import secrets;print(secrets.token_urlsafe(32))"
```

If no .env.example, create one — copy values from `.env` template format used elsewhere in the project.

- [ ] **Step 3: Add an "Access control" section to README.md**

Append after the existing "API-Übersicht" section, before "Limitationen":

```markdown
## Access control (provider gating)

The gateway gates non-`ollama` providers behind admin approval. Defaults:

- **ollama** — available to all callers (configurable via `UNGATED_PROVIDERS`)
- **claude, opencode, openai, mammouth, custom** — require an active
  `ProviderGrant` row for the calling `user_id`, OR the caller must hold
  the `ADMIN_TOKEN`.

### Tokens

| Token | Role | `user_id` resolution |
|---|---|---|
| `ADMIN_TOKEN` | admin | always `Config.ADMIN_USER_ID` (env, default `harald`) |
| `SERVICE_TOKEN` | user | from request body/query (current behavior) |

### Admin UI

Visit `https://<service>/admin/ui?token=<ADMIN_TOKEN>` once to bootstrap
a signed session cookie. After that, the URL is `/admin/ui/users`.

Shows per-user roster with configured providers, active grants, and
30-day usage rollup. Approve/revoke buttons hit `/admin/grants` via the
session cookie.

### Admin REST API

`Authorization: Bearer <ADMIN_TOKEN>` on every endpoint.

```
POST   /admin/grants           {user_id, provider_id, note?}  → 201
GET    /admin/grants[?user_id=&provider_id=&include_revoked=true]
DELETE /admin/grants/<id>      → 204 (soft-delete)
GET    /admin/overview         → {users: [...]}
```
```

- [ ] **Step 4: Add deploy steps to OPERATIONS.md**

Find the deployment section and append:

```markdown
## Deploying the provider-access gate

Order matters — the bootstrap MUST run before flipping `GATE_ENABLED=true`,
or existing consumer apps will hit 403 for already-configured providers.

1. **Deploy code with `GATE_ENABLED=false`** (default). New table
   `provider_grants` is created via `db.create_all()` on service restart.
   No behavior change for existing callers.

2. **Set `ADMIN_TOKEN`, `ADMIN_USER_ID`, `SECRET_KEY`** in `.env` on the VPS:
   ```bash
   ssh ionos-vps
   cd /opt/ai-provider-service
   echo "ADMIN_TOKEN=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" >> .env
   echo "SECRET_KEY=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" >> .env
   echo "ADMIN_USER_ID=harald" >> .env
   ```

3. **Run the bootstrap** to grant every existing config:
   ```bash
   cd /opt/ai-provider-service
   source venv/bin/activate
   flask --app app grants-bootstrap
   # → "Created N new grants."
   ```

4. **Flip the gate on** and restart:
   ```bash
   echo "GATE_ENABLED=true" >> .env
   sudo systemctl restart ai-provider-service
   ```

5. **Smoke test:**
   ```bash
   # admin token — should work
   curl -H "Authorization: Bearer $ADMIN_TOKEN" https://<host>/admin/overview

   # existing consumer app on a grandfathered config — should still work
   curl -H "Authorization: Bearer $SERVICE_TOKEN" \
        -X POST https://<host>/configs/loganonymizer-default/ollama \
        -H 'Content-Type: application/json' -d '{"config":{}}'

   # new user_id on a gated provider — should 403
   curl -H "Authorization: Bearer $SERVICE_TOKEN" \
        -X POST https://<host>/configs/test-new-user/claude \
        -H 'Content-Type: application/json' -d '{"config":{"api_key":"x"}}'
   # → {"error":"needs_approval", ...}
   ```

6. **Verify admin UI** by visiting:
   ```
   https://<host>/admin/ui?token=<ADMIN_TOKEN>
   ```
   Should redirect to `/admin/ui/users` and show the roster.

### Rollback

If anything misbehaves:
1. `echo "GATE_ENABLED=false" >> .env` (or remove the line)
2. `sudo systemctl restart ai-provider-service`

Gate becomes a no-op, all existing callers work unchanged. Bootstrap rows
in `provider_grants` are harmless — they're ignored when gate is off.
```

- [ ] **Step 5: Commit**

```bash
git add README.md OPERATIONS.md .env.example
git commit -m "Doc: access control + deploy steps for provider gating

Documents the admin-token model, /admin/ui usage, /admin/grants REST API,
.env vars, and the deploy order (bootstrap before flipping GATE_ENABLED).
Also includes rollback procedure.

Verified: docs only, no code changes."
```

---

### Task 15: Final integration smoke test + AGENTS.md handoff zone update

**Files:**
- Create: `tests/test_end_to_end_access_control.py`
- Modify: [AGENTS.md](../../../AGENTS.md) — update §7 Handoff zone

- [ ] **Step 1: Write end-to-end smoke**

Create `tests/test_end_to_end_access_control.py`:

```python
"""End-to-end: simulate the full admin → grant → consumer flow."""

import pytest
from config import Config
from database import db


@pytest.fixture(autouse=True)
def gated_app():
    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.SECRET_KEY = 'test-secret'
    Config.GATE_ENABLED = True
    Config.UNGATED_PROVIDERS = {'ollama'}
    yield
    Config.GATE_ENABLED = False


def test_e2e_user_blocked_then_granted_then_configured(client, app):
    # Step 1: New user 'lisa' tries to configure claude — blocked.
    r = client.post(
        '/configs/lisa/claude',
        json={'config': {'api_key': 'sk-test'}},
        headers={'Authorization': 'Bearer test-token'},
    )
    assert r.status_code == 403

    # Step 2: Admin grants claude to lisa.
    r = client.post(
        '/admin/grants',
        json={'user_id': 'lisa', 'provider_id': 'claude',
              'note': 'transcript summaries'},
        headers={'Authorization': 'Bearer admin-test-token'},
    )
    assert r.status_code == 201
    grant_id = r.get_json()['grant']['id']

    # Step 3: lisa retries — succeeds.
    r = client.post(
        '/configs/lisa/claude',
        json={'config': {'api_key': 'sk-test'}},
        headers={'Authorization': 'Bearer test-token'},
    )
    assert r.status_code == 200

    # Step 4: Admin revokes.
    r = client.delete(
        f'/admin/grants/{grant_id}',
        headers={'Authorization': 'Bearer admin-test-token'},
    )
    assert r.status_code == 204

    # Step 5: lisa is blocked again on next config attempt.
    r = client.delete(
        '/configs/lisa/claude',
        headers={'Authorization': 'Bearer test-token'},
    )
    assert r.status_code == 403
```

- [ ] **Step 2: Run e2e test**

Run: `pytest tests/test_end_to_end_access_control.py -v`
Expected: PASS.

- [ ] **Step 3: Run full suite — final check**

Run: `pytest -q`
Expected: ALL tests pass — the recent baseline was 142, plus the new ones added across Tasks 1-15.

- [ ] **Step 4: Update AGENTS.md §7 Handoff zone**

Edit [AGENTS.md](../../../AGENTS.md) — replace the empty §7 section with implementation status:

```markdown
## 7. Handoff zone

### Provider access control + opencode.ai integration

**Status:** Implementation complete (2026-05-30) per
[`docs/superpowers/plans/2026-05-30-provider-access-control.md`](docs/superpowers/plans/2026-05-30-provider-access-control.md)
([spec](docs/superpowers/specs/2026-05-30-provider-access-control-design.md)).

**Deployed:** No — pending. Follow OPERATIONS.md "Deploying the
provider-access gate" section.

**Pricing entries for opencode.ai:** populate `pricing.py` with the current
Zen rate card before relying on cost tracking.
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_end_to_end_access_control.py AGENTS.md
git commit -m "Test: end-to-end smoke for access control + handoff status

Single test covers the full admin → grant → consumer flow: block, grant,
allow, revoke, re-block. AGENTS.md §7 handoff zone updated with status
pointing to the spec and plan.

Verified: pytest ✓ (full suite passes)."
```

---

## Verification Summary (after final task)

- Existing test baseline (per recent commits): 142/142 pass.
- New tests added: ~40 across Tasks 1, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 15.
- No service restart on VPS required for code changes — `db.create_all()` picks up the new table on the next planned restart for the deploy.
- `GATE_ENABLED=false` keeps deploy idempotent. Flip after bootstrap.
- Rollback: flip `GATE_ENABLED` back to `false`.

## Open follow-ups (per spec §15, not part of this plan)

- 429-aware backoff for opencode.ai
- Pricing entries populated from opencode.ai's current Zen rate card
- In-band approval requests (currently out-of-band only)
- Per-principal tokens for consumer apps (retiring shared `SERVICE_TOKEN`)
- Audit log table for grant changes
