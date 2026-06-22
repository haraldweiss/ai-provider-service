# Personal Provider API Keys Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let authenticated users manage personal API keys for Claude, opencode.ai, OpenAI, z.ai, and Ollama Cloud through both an API and a self-service UI, with personal-key access independent of admin grants.

**Architecture:** Keep provider credentials in the existing Fernet-encrypted `ProviderConfig` model. Add hashed, admin-issued user tokens for identity, teach the authorization gate to recognize an owning user's personal key, add a separate native Ollama Cloud client, and expose the shared config behavior through server-rendered settings pages.

**Tech Stack:** Python 3.9+, Flask, Flask-SQLAlchemy, Jinja, `requests`, Fernet, pytest, Docker/gunicorn.

## Global Constraints

- Never log, return, or render plaintext provider keys or plaintext user tokens after initial issuance.
- A user token is bound to exactly one `user_id`; cross-user requests return `403`.
- Personal keys authorize only their owning user and always take precedence over server-funded keys.
- Existing `ADMIN_TOKEN` and `SERVICE_TOKEN` behavior remains backward compatible.
- Local `ollama` and remote `ollama_cloud` remain separate providers.
- Write each behavior test first and observe the expected failure before implementation.
- Use `Config`/environment values for paths and secrets; add no hardcoded database or vault paths.
- Update README and AGENTS.md in lockstep with the implementation.

---

## File Structure

- `storage/models.py`: persist token hashes and safe token metadata alongside existing user/provider models.
- `storage/user_tokens.py`: generate, hash, resolve, rotate, and revoke high-entropy user tokens.
- `api/auth.py`: resolve user-token principals and reject identity mismatches.
- `api/admin_api.py`: admin token lifecycle endpoints.
- `api/admin_ui.py`, `templates/admin/user_detail.html`: admin provisioning controls and one-time token display.
- `api/gate.py`, `api/configs_api.py`, `providers/__init__.py`: BYO metadata, personal-key authorization, and config-management policy.
- `providers/ollama_cloud.py`: isolated remote Ollama API adapter.
- `api/settings_ui.py`, `templates/settings/*`: user login, CSRF-protected key management, and provider tests.
- `app.py`: import the new model and register the settings blueprint.
- `README.md`, `AGENTS.md`: public usage and durable security/operations rules.

### Task 1: User token persistence and authentication

**Files:**
- Modify: `storage/models.py`
- Create: `storage/user_tokens.py`
- Modify: `api/auth.py`
- Modify: `app.py`
- Modify: `tests/conftest.py`
- Create: `tests/test_user_access_tokens.py`
- Create: `tests/test_auth_user_tokens.py`

**Interfaces:**
- Produces: `issue_user_token(user_id: str) -> str`
- Produces: `resolve_user_token(raw_token: str) -> tuple[str, str] | None` returning `(user_id, generation)`
- Produces: `is_user_token_generation_active(user_id: str, generation: str) -> bool`
- Produces: `revoke_user_token(user_id: str) -> bool`
- Produces: `Principal(user_id: str, role: str, credential: str = 'service')`

- [ ] **Step 1: Write failing model/service tests**

```python
def test_issue_stores_hash_and_resolves_once_identity(app):
    from storage.models import UserAccessToken
    from storage.user_tokens import issue_user_token, resolve_user_token
    raw = issue_user_token('lisa')
    row = db.session.get(UserAccessToken, 'lisa')
    assert raw.startswith('aips_')
    assert raw not in row.token_hash
    assert row.token_prefix == raw[:12]
    assert resolve_user_token(raw)[0] == 'lisa'

def test_rotation_and_revocation_invalidate_old_tokens(app):
    first = issue_user_token('lisa')
    second = issue_user_token('lisa')
    assert resolve_user_token(first) is None
    assert resolve_user_token(second)[0] == 'lisa'
    assert revoke_user_token('lisa') is True
    assert resolve_user_token(second) is None
```

- [ ] **Step 2: Run the token tests and verify RED**

Run: `pytest -q tests/test_user_access_tokens.py`

Expected: collection/import failure because `UserAccessToken` and `storage.user_tokens` do not exist.

- [ ] **Step 3: Implement token storage and lifecycle**

Add `UserAccessToken` with `user_id` primary key, unique 64-character
`token_hash`, 12-character `token_prefix`, random 32-character `generation`,
`created_at`, and nullable `revoked_at`. Implement generation and lookup in
`storage/user_tokens.py`:

```python
def _digest(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()

def issue_user_token(user_id: str) -> str:
    raw = f'aips_{secrets.token_urlsafe(32)}'
    row = db.session.get(UserAccessToken, user_id) or UserAccessToken(user_id=user_id)
    row.token_hash = _digest(raw)
    row.token_prefix = raw[:12]
    row.generation = secrets.token_hex(16)
    row.created_at = datetime.now(timezone.utc)
    row.revoked_at = None
    db.session.add(row)
    db.session.commit()
    return raw
```

Resolve to `(user_id, generation)` by digest with `hmac.compare_digest`, ignore
revoked rows, and never log the supplied token. Implement
`is_user_token_generation_active()` as a primary-key lookup that checks both
`revoked_at is None` and a constant-time generation match. Import
`UserAccessToken` before `_safe_create_all()` in
`app.py` and eagerly import the new auth dependencies in `tests/conftest.py`.

- [ ] **Step 4: Run the token tests and verify GREEN**

Run: `pytest -q tests/test_user_access_tokens.py`

Expected: all tests pass.

- [ ] **Step 5: Write failing authentication isolation tests**

```python
def test_user_token_resolves_bound_principal(client, app):
    raw = issue_user_token('lisa')
    response = client.get('/configs/lisa', headers={'Authorization': f'Bearer {raw}'})
    assert response.status_code == 200

def test_user_token_cannot_assert_another_user(client, app):
    raw = issue_user_token('lisa')
    response = client.get('/configs/eve', headers={'Authorization': f'Bearer {raw}'})
    assert response.status_code == 403
    assert response.get_json()['error'] == 'identity_mismatch'
```

- [ ] **Step 6: Run auth tests and verify RED**

Run: `pytest -q tests/test_auth_user_tokens.py`

Expected: `401` because user tokens are not recognized.

- [ ] **Step 7: Extend principal resolution and enforce identity binding**

Add `credential` to `Principal`. Resolve admin and service tokens first, then
call `resolve_user_token(token)` and construct the principal from the returned
`user_id` (the generation is used only by settings sessions). In `require_token`, compare any asserted
path/query/body `user_id` with a `credential == 'user_token'` principal and
return:

```python
return jsonify({
    'error': 'identity_mismatch',
    'message': 'user token cannot access another user',
}), 403
```

- [ ] **Step 8: Run focused and existing auth tests**

Run: `pytest -q tests/test_auth_user_tokens.py tests/test_auth_principal.py tests/test_user_access_tokens.py tests/test_webdav.py`

Expected: all tests pass; legacy Bearer and WebDAV Basic behavior remains green.

- [ ] **Step 9: Commit**

```bash
git add storage/models.py storage/user_tokens.py api/auth.py app.py tests/conftest.py tests/test_user_access_tokens.py tests/test_auth_user_tokens.py
git commit -m "Add: per-user access tokens" -m "Verified: pytest -q tests/test_auth_user_tokens.py tests/test_auth_principal.py tests/test_user_access_tokens.py tests/test_webdav.py"
```

### Task 2: Admin token provisioning

**Files:**
- Modify: `api/admin_api.py`
- Modify: `api/admin_ui.py`
- Modify: `templates/admin/user_detail.html`
- Modify: `tests/test_admin_api.py`
- Modify: `tests/test_admin_ui.py`

**Interfaces:**
- Consumes: `issue_user_token`, `revoke_user_token`, `UserAccessToken.to_safe_dict()`
- Produces: `POST /admin/users/<user_id>/token`
- Produces: `DELETE /admin/users/<user_id>/token`

- [ ] **Step 1: Write failing admin endpoint tests**

```python
def test_admin_issues_token_once(client):
    response = client.post('/admin/users/lisa/token', headers=admin_headers())
    assert response.status_code == 201
    body = response.get_json()
    assert body['token'].startswith('aips_')
    assert body['token_status']['prefix'] == body['token'][:12]

def test_admin_revokes_token(client):
    raw = client.post('/admin/users/lisa/token', headers=admin_headers()).get_json()['token']
    response = client.delete('/admin/users/lisa/token', headers=admin_headers())
    assert response.status_code == 204
    assert resolve_user_token(raw) is None
```

- [ ] **Step 2: Run endpoint tests and verify RED**

Run: `pytest -q tests/test_admin_api.py -k token`

Expected: `404` for both new routes.

- [ ] **Step 3: Add admin lifecycle routes and safe status**

Return plaintext only from the successful POST. Add token status to
`user_detail()` as `{configured, prefix, created_at, revoked_at}`; never pass
`token_hash` to Jinja or JSON.

- [ ] **Step 4: Run endpoint tests and verify GREEN**

Run: `pytest -q tests/test_admin_api.py -k token`

Expected: all token endpoint tests pass.

- [ ] **Step 5: Write failing admin UI tests**

```python
def test_user_detail_shows_token_controls_without_hash(client, app):
    login_admin(client)
    response = client.get('/admin/ui/users/lisa')
    assert b'issue personal token' in response.data
    assert b'token_hash' not in response.data
```

- [ ] **Step 6: Add issue/rotate/revoke controls**

Use same-origin `fetch` against the admin routes. After POST, display the
one-time token in a `<code id="issued-token">` element and warn that it cannot
be recovered. Require explicit confirmation for rotation and revocation.

- [ ] **Step 7: Run admin tests and commit**

Run: `pytest -q tests/test_admin_api.py tests/test_admin_ui.py`

Expected: all tests pass.

```bash
git add api/admin_api.py api/admin_ui.py templates/admin/user_detail.html tests/test_admin_api.py tests/test_admin_ui.py
git commit -m "Add: admin-managed user tokens" -m "Verified: pytest -q tests/test_admin_api.py tests/test_admin_ui.py"
```

### Task 3: BYO-key metadata, config management, and authorization

**Files:**
- Modify: `providers/__init__.py`
- Modify: `api/gate.py`
- Modify: `api/configs_api.py`
- Modify: `api/providers_api.py`
- Modify: `tests/test_gate.py`
- Modify: `tests/test_gate_integration.py`
- Modify: `tests/test_end_to_end_access_control.py`
- Create: `tests/test_personal_key_access.py`

**Interfaces:**
- Produces: `provider_supports_personal_key(provider_id: str) -> bool`
- Produces: `has_personal_api_key(user_id: str, provider_id: str) -> bool`
- Changes: `is_allowed()` returns true for the owning user's valid personal key.

- [ ] **Step 1: Write failing registry and gate tests**

```python
@pytest.mark.parametrize('provider_id', ['claude', 'opencode', 'openai', 'zai', 'ollama_cloud'])
def test_personal_key_authorizes_without_grant(app, provider_id):
    pc = ProviderConfig(user_id='lisa', provider_id=provider_id)
    pc.set_config({'api_key': 'personal-test-key'})
    db.session.add(pc)
    db.session.commit()
    assert is_allowed(Principal('lisa', 'user', 'user_token'), provider_id) is True

def test_empty_or_corrupt_config_does_not_authorize(app):
    # Assert both encrypted {} and invalid ciphertext return False.
```

- [ ] **Step 2: Run gate tests and verify RED**

Run: `pytest -q tests/test_personal_key_access.py tests/test_gate.py`

Expected: personal-key cases fail because `is_allowed()` only checks grants.

- [ ] **Step 3: Add explicit registry metadata and fail-closed helpers**

Add `personal_api_key: True` only to the five named providers and
`optional: ['api_key']` to Claude. Implement `has_personal_api_key()` with a
single `ProviderConfig` lookup, decryption inside `try/except`, and a boolean
check of stripped `api_key`. Log only user/provider identifiers on corruption.

- [ ] **Step 4: Run gate tests and verify GREEN**

Run: `pytest -q tests/test_personal_key_access.py tests/test_gate.py`

Expected: all tests pass.

- [ ] **Step 5: Write failing config API policy tests**

```python
def test_user_can_save_own_personal_key_without_grant(client, app):
    raw = issue_user_token('lisa')
    response = client.post('/configs/lisa/claude',
        json={'config': {'api_key': 'personal-key'}},
        headers={'Authorization': f'Bearer {raw}'})
    assert response.status_code == 200
    assert response.get_json()['has_api_key'] is True

def test_personal_key_removal_restores_grant_requirement(client, app):
    # Save, delete, then assert /providers/claude/test returns needs_approval.
```

- [ ] **Step 6: Implement credential-management policy**

Replace blanket `@require_provider_access` on config CRUD with a focused helper:

```python
def _can_manage_config(principal, user_id, provider_id, config_dict=None):
    if principal.role == 'admin' or is_allowed(principal, provider_id):
        return True
    return (
        principal.user_id == user_id
        and provider_supports_personal_key(provider_id)
        and (has_personal_api_key(user_id, provider_id) or bool((config_dict or {}).get('api_key')))
    )
```

Apply it inside GET/POST/DELETE after authentication. Ensure providers test and
models routes use the principal's bound identity and sanitize exceptions before
returning JSON.

- [ ] **Step 7: Run access-control suites and commit**

Run: `pytest -q tests/test_personal_key_access.py tests/test_gate.py tests/test_gate_integration.py tests/test_end_to_end_access_control.py`

Expected: all tests pass, including updated E2E expectations that BYO keys do
not need grants while server-funded calls still do.

```bash
git add providers/__init__.py api/gate.py api/configs_api.py api/providers_api.py tests/test_gate.py tests/test_gate_integration.py tests/test_end_to_end_access_control.py tests/test_personal_key_access.py
git commit -m "Add: BYO-key provider authorization" -m "Verified: focused gate, config API, and E2E access-control tests"
```

### Task 4: Ollama Cloud provider

**Files:**
- Create: `providers/ollama_cloud.py`
- Modify: `providers/__init__.py`
- Create: `tests/test_ollama_cloud_provider.py`

**Interfaces:**
- Produces: `OllamaCloudClient(config: dict)` implementing `BaseClient`
- Uses: base URL `config['api_endpoint']` or `https://ollama.com`

- [ ] **Step 1: Write failing native API adapter tests**

```python
def test_get_models_sends_bearer_header(monkeypatch):
    get = Mock(return_value=fake_response({'models': [{'name': 'glm-4.6'}]}))
    monkeypatch.setattr('providers.ollama_cloud.requests.get', get)
    client = OllamaCloudClient({'api_key': 'secret'})
    assert client.get_models() == ['glm-4.6']
    assert get.call_args.kwargs['headers'] == {'Authorization': 'Bearer secret'}

def test_create_message_maps_native_response(monkeypatch):
    # Assert POST /api/chat, stream=False, max-token options, content, and usage mapping.

def test_errors_never_include_key(monkeypatch):
    # Make requests raise an error containing the key and assert sanitized text.
```

- [ ] **Step 2: Run provider tests and verify RED**

Run: `pytest -q tests/test_ollama_cloud_provider.py`

Expected: import failure because the provider does not exist.

- [ ] **Step 3: Implement the isolated cloud client**

Require `api_key`; strip trailing `/api` from a custom endpoint so URLs are
formed once; set Bearer headers on every call. Use 5 seconds for tags/health and
180 seconds for chat. Map `prompt_eval_count` and `eval_count` into the common
usage contract. Raise sanitized `RuntimeError` messages containing status and
provider operation but no response headers, request headers, or key.

- [ ] **Step 4: Register factory branch and run tests**

Run: `pytest -q tests/test_ollama_cloud_provider.py tests/test_opencode_provider.py tests/test_zai_provider.py`

Expected: all tests pass and `get_client('ollama_cloud', config)` returns the
new client.

- [ ] **Step 5: Commit**

```bash
git add providers/ollama_cloud.py providers/__init__.py tests/test_ollama_cloud_provider.py
git commit -m "Add: Ollama Cloud provider" -m "Verified: pytest -q tests/test_ollama_cloud_provider.py tests/test_opencode_provider.py tests/test_zai_provider.py"
```

### Task 5: Self-service settings UI

**Files:**
- Create: `api/settings_ui.py`
- Create: `templates/settings/base.html`
- Create: `templates/settings/login.html`
- Create: `templates/settings/providers.html`
- Modify: `app.py`
- Create: `tests/test_settings_ui.py`

**Interfaces:**
- Produces: `GET|POST /settings/login`
- Produces: `GET /settings/providers`, `POST /settings/providers/<provider_id>/save|test|remove`
- Produces: `POST /settings/logout`

- [ ] **Step 1: Write failing login, session, and CSRF tests**

```python
def test_login_stores_identity_not_plaintext_token(client, app):
    raw = issue_user_token('lisa')
    response = client.post('/settings/login', data={'token': raw})
    assert response.status_code == 302
    with client.session_transaction() as session:
    assert session['settings_user_id'] == 'lisa'
    assert session['settings_token_generation']
        assert raw not in repr(dict(session))
        assert session['settings_csrf']

def test_state_change_rejects_missing_csrf(client, logged_in_user):
    response = client.post('/settings/providers/claude/save', data={'api_key': 'x'})
    assert response.status_code == 403
```

- [ ] **Step 2: Run UI tests and verify RED**

Run: `pytest -q tests/test_settings_ui.py`

Expected: `404` because the settings blueprint is not registered.

- [ ] **Step 3: Implement login/session/CSRF boundary**

Resolve the submitted token once, rotate a session CSRF value with
`secrets.token_urlsafe(32)`, and store only `settings_user_id`,
`settings_token_generation`, and `settings_csrf`. Before every settings request,
verify the database row is active and its generation still matches; otherwise
clear the session and redirect to login. Use `hmac.compare_digest` for form CSRF
checks. Set session cookie defaults to HTTP-only, `SameSite=Lax`, and secure
when served through HTTPS.

- [ ] **Step 4: Run login and CSRF tests and verify GREEN**

Run: `pytest -q tests/test_settings_ui.py -k 'login or csrf or logout'`

Expected: all selected tests pass.

- [ ] **Step 5: Write failing provider form tests**

```python
def test_save_renders_only_safe_key_state(client, logged_in_user):
    response = post_with_csrf(client, '/settings/providers/claude/save', {'api_key': 'top-secret'})
    assert response.status_code == 302
    page = client.get('/settings/providers')
    assert b'configured' in page.data
    assert b'top-secret' not in page.data

def test_test_action_uses_stored_key_and_sanitizes_error(client, logged_in_user, monkeypatch):
    # Patch get_client, submit test form, and assert no key appears in response.
```

- [ ] **Step 6: Implement provider cards and form actions**

Render only registry entries with `personal_api_key=True`. Reuse shared config
save/remove functions extracted from `api/configs_api.py` so UI and JSON routes
cannot drift. Test by calling `get_client(provider_id, stored_config).get_models()`
and flash only a model count or sanitized error category.

- [ ] **Step 7: Run UI and config tests**

Run: `pytest -q tests/test_settings_ui.py tests/test_gate_integration.py tests/test_personal_key_access.py`

Expected: all tests pass; HTML contains no plaintext keys.

- [ ] **Step 8: Commit**

```bash
git add api/settings_ui.py api/configs_api.py templates/settings app.py tests/test_settings_ui.py tests/test_gate_integration.py
git commit -m "Add: personal provider settings UI" -m "Verified: settings UI, config API, and personal access tests"
```

### Task 6: Documentation and release verification

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `.env.example` only if implementation adds a configurable default

**Interfaces:**
- Documents: token provisioning, UI login, API examples, provider IDs, access precedence, rotation/revocation, and deployment.

- [ ] **Step 1: Update README and durable agent rules**

Document `/settings/login`, the three admin token operations, Bearer user-token
examples for `/configs`, all five BYO providers, and `ollama_cloud`. Add the
AGENTS.md hard rule: personal keys authorize only their owning identity, take
precedence over server keys, must never be disclosed, and must never silently
fall back to owner-funded credentials.

- [ ] **Step 2: Run documentation consistency checks**

Run: `rg -n "ollama_cloud|personal API key|user token|/settings" README.md AGENTS.md providers/__init__.py app.py`

Expected: docs, registry, and routes all use the same names.

- [ ] **Step 3: Run the full test suite**

Run: `pytest -q`

Expected: all tests pass with no warnings or errors attributable to this change.

- [ ] **Step 4: Run image build and smoke verification**

Run: `./build.sh`

Expected: SHA-tagged and `latest` images build successfully.

Run the repository's CI-equivalent container boot and `/health` smoke commands
from `.github/workflows/ci.yml` exactly. Expected: container becomes healthy and
`GET /health` returns HTTP 200. Do not deploy or use live provider keys unless
the user separately authorizes production deployment.

- [ ] **Step 5: Inspect secret-safety and diff quality**

Run: `git diff --check`

Run: `rg -n "api_key.*(print|logger)|token_hash.*jsonify|issued-token" api providers storage templates`

Expected: no key logging, no token hash serialization, and `issued-token`
appears only in the one-time admin response UI.

- [ ] **Step 6: Commit documentation**

```bash
git add README.md AGENTS.md .env.example
git commit -m "Doc: personal provider key operations" -m "Verified: pytest -q; Docker build, boot, and /health smoke; NOT deployed"
```

- [ ] **Step 7: Review branch completion**

Invoke `superpowers:verification-before-completion`, then
`superpowers:requesting-code-review`. Confirm `git status --short` is clean and
report exact test counts, image tag, smoke result, and deployment status.
