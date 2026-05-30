# Provider Access Control + opencode.ai Integration

**Status:** Draft · awaiting user review
**Date:** 2026-05-30
**Author:** Harald Weiss (with Claude Code)

---

## 1. Problem

The ai-provider-service today has no notion of *who* is calling. Authentication is a single shared `SERVICE_TOKEN`; `user_id` is a free-form string the consumer app asserts. Anyone holding the token can use any configured provider as any `user_id`.

We want:

1. **Admin (Harald) auto-access to opencode.ai + Claude models in every repo** that talks to the service.
2. **Other users get ollama by default.** Any other provider — including claude, opencode, openai, mammouth, custom — requires admin approval.
3. **opencode.ai** is not yet a provider in this service. It needs to be added as a 6th provider (OpenAI-compatible API).
4. A **small admin UI** to approve grants and see an overview of who's using what.

## 2. Goals & non-goals

### Goals
- Identity model that distinguishes admin from non-admin callers.
- Per-(user, provider) grant table with revoke history.
- Gate that 403s non-admin access to gated providers without a grant.
- New `opencode` provider integration (BYO key, OpenAI-compatible).
- Admin REST API + Jinja-rendered UI at `/admin/ui` for grant management and usage overview.
- Backwards compatible for existing consumer apps via a one-shot bootstrap migration.

### Non-goals (explicit YAGNI)
- Budget tracking, per-user spending caps, or shared admin keys. Approval = "you may configure this provider", not "you may spend my money". Users still BYO key.
- In-band approval requests / pending-grant flow. Out-of-band only (grant via admin endpoint or UI).
- Per-agent enforcement (Claude Code vs opencode CLI vs consumer apps). The `X-Agent` header is recorded for the usage overview but does not affect policy.
- Migration of consumer apps to per-principal tokens. They keep `SERVICE_TOKEN`.
- CSRF tokens / login form. Single-admin tool; admin token + signed session cookie is sufficient.
- Pagination on the user list. Revisit if it ever exceeds a few hundred distinct user_ids.

## 3. Architecture

### Component flow

```
incoming request
  ├─ auth.py resolves token → principal (user_id, role)
  │     • ADMIN_TOKEN  → (ADMIN_USER_ID, admin)   ← bypasses gate
  │     • SERVICE_TOKEN → (body.user_id, user)    ← gate enforced
  ├─ gate.py checks (provider_id, principal):
  │     • provider in UNGATED_PROVIDERS         → allow
  │     • role == admin                         → allow
  │     • active grant for (user, provider)     → allow
  │     • else                                  → 403 needs_approval
  └─ existing dispatcher / provider call (unchanged)
```

### Files added / modified

**New:**
- `providers/opencode.py` — opencode.ai client (OpenAI-compatible)
- `api/gate.py` — `require_provider_access(provider_id)` decorator + `is_allowed(principal, provider_id)` helper
- `api/admin_api.py` — grants CRUD + overview JSON
- `api/admin_ui.py` — Jinja-rendered admin pages
- `templates/admin/base.html`, `users.html`, `user_detail.html`
- `deploy/bootstrap_grants.py` (or Flask CLI command) — one-shot grant insertion for existing configs

**Modified:**
- `api/auth.py` — `Principal` dataclass, `_resolve_principal`, `@require_admin`, attach to `flask.g`
- `storage/models.py` — `ProviderGrant` model
- `api/configs_api.py`, `api/chat_api.py`, `api/providers_api.py` — apply gate decorator
- `providers/__init__.py` — register `opencode`
- `config.py` — read `ADMIN_TOKEN`, `ADMIN_USER_ID`, `UNGATED_PROVIDERS`, `OPENCODE_BASE_URL`, `GATE_ENABLED`
- `pricing.py` — opencode model pricing entries

## 4. Identity model

### Tokens and roles

| Token | Role | Effective `user_id` |
|---|---|---|
| `ADMIN_TOKEN` | `admin` | Always `Config.ADMIN_USER_ID` (env-set; defaults to `harald`). Body/query `user_id` is **ignored** — prevents accidental writes under wrong user_id. |
| `SERVICE_TOKEN` | `user` | From request body/query (current behavior; asserted by caller). |

If admin needs to act on another user's behalf (e.g. write a config for testing), they use the admin-API counterpart endpoints — keeps `UsageEvent.actor` answerable.

### `Principal` dataclass

```python
@dataclass(frozen=True)
class Principal:
    user_id: str
    role: str  # 'admin' | 'user'
```

Resolved once per request in `auth.py`, attached to `flask.g.principal`. Existing `@require_token` decorator continues to gate at the API boundary; new `@require_admin` decorator gates admin endpoints.

### `X-Agent` header

Read on every request, stored on `g.agent` (string, may be `None`). When `UsageEvent` is written, if the caller has not set `origin_app`, fall back to `g.agent`. Purely observational — no policy impact in this spec.

## 5. Data model

### New table: `provider_grants`

```python
class ProviderGrant(db.Model):
    __tablename__ = 'provider_grants'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(255), nullable=False, index=True)
    provider_id = db.Column(db.String(32), nullable=False)
    granted_by = db.Column(db.String(255), nullable=False)   # admin user_id
    granted_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    revoked_at = db.Column(db.DateTime, nullable=True)       # soft-delete
    note = db.Column(db.Text, nullable=True)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'provider_id', name='uq_user_provider_grant'),
    )
```

Re-granting after revoke updates the existing row (clears `revoked_at`, refreshes `granted_at`, replaces `note`).

### Gate check

```python
# api/gate.py
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

def require_provider_access(provider_id_arg='provider_id'):
    """Decorator. Resolves provider_id from path/body and checks gate."""
    ...
```

### No migration tool

SQLAlchemy `db.create_all()` on next service start picks up the new table (matches existing model management; no Alembic in the repo). Existing tables (`provider_configs`, `request_queue`, `usage_events`) unchanged.

### User discoverability for the overview

No `users` table. The user roster is derived:

```sql
SELECT user_id FROM provider_configs
UNION
SELECT user_id FROM usage_events
UNION
SELECT user_id FROM provider_grants
```

## 6. Config / env vars

```python
# config.py additions
ADMIN_TOKEN       = os.environ.get('ADMIN_TOKEN')             # required for admin access
ADMIN_USER_ID     = os.environ.get('ADMIN_USER_ID', 'harald')
SERVICE_TOKEN     = os.environ.get('SERVICE_TOKEN')           # unchanged
UNGATED_PROVIDERS = set((os.environ.get('UNGATED_PROVIDERS') or 'ollama').split(','))
OPENCODE_BASE_URL = os.environ.get('OPENCODE_BASE_URL', 'https://opencode.ai/zen/v1')
GATE_ENABLED      = os.environ.get('GATE_ENABLED', 'false').lower() == 'true'
```

`GATE_ENABLED` is a deploy-time feature flag. Default `false` so step-1 deploys are no-ops; flip to `true` after bootstrap.

`UNGATED_PROVIDERS` default `ollama` matches the chosen policy: every remote provider is gated. Env-driven so policy changes don't need a code edit.

## 7. Admin REST API

All routes require `ADMIN_TOKEN`.

```
POST   /admin/grants
       Body: {"user_id": "...", "provider_id": "...", "note": "..."}
       → 201 {grant: {...}}     (idempotent — re-grants restore a revoked row)

GET    /admin/grants?user_id=<id>&provider_id=<pid>&include_revoked=true
       → {grants: [...]}

DELETE /admin/grants/<id>
       → 204                    (soft-delete: sets revoked_at)

GET    /admin/overview
       → {users: [...]}         (single source for UI + future dashboards)
```

`GET /admin/overview` response shape:

```json
{
  "users": [
    {
      "user_id": "lisa",
      "is_admin": false,
      "configured_providers": ["ollama", "claude"],
      "grants": [
        {"id": 7, "provider_id": "claude", "granted_at": "...", "granted_by": "harald", "note": null}
      ],
      "last_30d": {
        "total_calls": 142,
        "by_provider": {"ollama": 130, "claude": 12},
        "by_origin_app": {"loganonymizer": 130, "opencode": 12},
        "last_used_at": "2026-05-28T14:33:02Z"
      }
    }
  ]
}
```

## 8. Admin UI

**Route:** `/admin/ui` (Jinja-rendered, served by `api/admin_ui.py`).

**Auth strategy:**
1. First request: `GET /admin/ui?token=<ADMIN_TOKEN>`. Server validates, sets a signed Flask session cookie (`admin=1`), redirects to `/admin/ui` (clean URL).
2. Subsequent requests use the cookie.
3. Logout = clear cookie.

Token-in-URL is acceptable because (a) it's used once and stripped, (b) admin-only, (c) the alternative is a login form which is overkill for one admin.

**Templates:**

```
templates/admin/
  base.html          ← header, simple CSS, logout link
  users.html         ← overview table (one row per user_id)
  user_detail.html   ← per-user grants + configs + 30d usage; approve/revoke buttons
```

**Overview page — `/admin/ui/users`:**

```
ai-provider-service · admin                                   [logout]
─────────────────────────────────────────────────────────────────────
User              Providers          Grants       30d calls  Last used
─────────────────────────────────────────────────────────────────────
harald (admin)    ollama,claude,…    (admin: bypass)  312    2m ago
lisa              ollama, claude     claude            89    1d ago
loganonymizer-…   ollama              —             1,204    3m ago
new-user-x        (none)              —                 0    never
─────────────────────────────────────────────────────────────────────
```

Each row links to `/admin/ui/users/<user_id>`.

**Detail page — `/admin/ui/users/<user_id>`:**

```
← back   ·   user: lisa                                       [logout]
─────────────────────────────────────────────────────────────────────
Grants
  ✓ claude       granted 2026-05-12 by harald     [revoke]
  ✗ opencode     not granted                       [approve]
  ✗ openai       not granted                       [approve]
  ✗ mammouth     not granted                       [approve]
  ✗ custom       not granted                       [approve]

Configured providers (BYO keys)
  ollama         configured 2026-05-01    (no grant needed)
  claude         configured 2026-05-12

Usage — last 30 days
  ollama         77 calls    origin: loganonymizer
  claude         12 calls    origin: opencode
─────────────────────────────────────────────────────────────────────
```

`[approve]` / `[revoke]` buttons POST to JSON endpoints via `fetch()` with the session cookie; on success, the row re-renders inline (no full page reload). Optional inline `<textarea>` for note.

## 9. opencode.ai provider

`providers/opencode.py`:

```python
from openai import OpenAI
from providers.base import BaseClient
from config import Config

class OpencodeClient(BaseClient):
    def __init__(self, config: dict):
        api_key = config.get('api_key')
        if not api_key:
            raise ValueError("Opencode: api_key erforderlich")
        base_url = config.get('api_endpoint') or Config.OPENCODE_BASE_URL
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def get_models(self) -> list[str]:
        try:
            return sorted(m.id for m in self.client.models.list().data)
        except Exception as e:
            logger.warning(f'Opencode get_models failed: {e}')
            return []

    def create_message(self, model, messages, max_tokens=600):
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

Registered in `providers/__init__.py` alongside the existing five.

Models fetched live via `/models` — no static `KNOWN_MODELS` list (unlike `claude.py`).

Pricing entries added to `pricing.py` based on opencode.ai's published rate card. Cost calc failure is non-fatal (`cost_usd` stays `NULL` for unknown models).

## 10. Behavior changes for existing callers

| Caller | Before | After (with `GATE_ENABLED=true`) |
|---|---|---|
| Consumer app with `SERVICE_TOKEN`, calling `ollama` | works | works (unchanged) |
| Consumer app with `SERVICE_TOKEN`, calling `claude` for an existing user that has a `ProviderConfig` | works | works (bootstrap created a grant) |
| Consumer app with `SERVICE_TOKEN`, calling `claude` for a **new** user with no config | works (until they hit no-config error) | **403 needs_approval** |
| Anything to `/admin/*` | n/a | 403 unless `ADMIN_TOKEN` |
| Admin calling anything | n/a | bypasses gate; `user_id` always `ADMIN_USER_ID` |

## 11. Bootstrap migration

The bootstrap closes the only breaking change: any existing `(user_id, provider_id)` pair in `provider_configs` gets a corresponding active grant, so existing consumer flows continue working.

Implemented as Flask CLI command `flask grants bootstrap` so the set of "gated providers" is read at runtime from `Config.UNGATED_PROVIDERS` rather than hardcoded — keeps bootstrap and gate in sync.

```python
# deploy/bootstrap_grants.py (registered as Flask CLI)
@app.cli.command('grants-bootstrap')
def grants_bootstrap():
    ungated = Config.UNGATED_PROVIDERS
    rows = ProviderConfig.query.filter(
        ~ProviderConfig.provider_id.in_(ungated)
    ).all()
    created = 0
    for cfg in rows:
        exists = ProviderGrant.query.filter_by(
            user_id=cfg.user_id, provider_id=cfg.provider_id
        ).first()
        if exists:
            continue
        db.session.add(ProviderGrant(
            user_id=cfg.user_id, provider_id=cfg.provider_id,
            granted_by='bootstrap',
            note='bootstrap from existing provider_configs',
        ))
        created += 1
    db.session.commit()
    print(f'Created {created} grants from {len(rows)} existing configs.')
```

Idempotent: re-running creates no duplicates.

## 12. Deploy steps (in order)

1. Deploy code with `GATE_ENABLED=false`. New tables created via `db.create_all()`. Gate is a no-op; nothing changes for any caller.
2. Run bootstrap: `flask grants bootstrap`. Creates one active grant per existing `(user_id, gated provider)` pair.
3. Set `ADMIN_TOKEN`, `ADMIN_USER_ID` in `.env`.
4. Set `GATE_ENABLED=true`, restart service.
5. Smoke-test:
   - existing consumer apps unaffected (200s on ollama, 200s on claude for grandfathered users)
   - brand-new user_id calling claude → 403 with `{"error": "needs_approval", ...}`
   - `GET /admin/overview` with `ADMIN_TOKEN` returns user list
   - `GET /admin/ui?token=<ADMIN_TOKEN>` redirects to `/admin/ui/users`, shows roster

## 13. Testing strategy

- **Unit:** `is_allowed()` truth table — admin vs user × ungated vs gated × grant present/revoked/missing.
- **Integration:** `pytest` covering:
  - 200 / 403 on `POST /chat` for each principal × provider combo
  - admin-only enforcement on `POST /admin/grants`
  - idempotent re-grant restoring a revoked row
  - `X-Agent` header propagating to `UsageEvent.origin_app` when caller omits `origin_app`
- **UI smoke:** Render `/admin/ui/users` and `/admin/ui/users/<id>` via test client; assert key strings present.

Existing test suite (142/142 per recent commit history) must still pass.

## 14. Open questions / risks

1. **opencode.ai auth format.** Assumed Bearer + OpenAI-compatible header. If they use a different scheme (OAuth, JWT, custom header), patch `OpencodeClient.__init__`. Verify against current opencode.ai docs at implementation time.
2. **opencode.ai rate limits / 429 handling.** Existing dispatcher fallback handles `ConnectionError` but not 429-aware backoff. If opencode rate-limits aggressively, we may want to mark its health as "throttled" rather than "down". Out of scope; flag as follow-up.
3. **`/providers` listing for non-admin users.** Recommendation: show all, mark `allowed: true/false` per row. Better UX in consumer-app provider pickers. Same query, no filter.
4. **Session cookie secret.** Flask sessions need `SECRET_KEY`. If not already set, add to `.env` and `config.py`. Required for the signed-cookie auth in the admin UI.

## 15. Future work (not in this spec)

- In-band approval requests (user requests access → admin gets notified → approves). Currently out-of-band.
- Per-user spending caps on shared admin keys (if/when we shift to "you spend on my budget" model).
- Per-agent policy (e.g. opencode CLI may use opencode provider but not claude).
- Per-principal tokens for consumer apps (retire shared `SERVICE_TOKEN`).
- Audit log for grant changes (currently captured via `granted_by` + `granted_at`/`revoked_at`; no separate event log).
