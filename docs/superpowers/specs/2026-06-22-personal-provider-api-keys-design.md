# Personal Provider API Keys — Design

## Goal

Let each user securely add, replace, test, and remove a personal API key for
Claude, opencode.ai, OpenAI, z.ai, and Ollama Cloud. The same capability must be
available through a built-in self-service UI and the HTTP API.

A personal key means the user, rather than the service owner, bears provider
costs. Therefore, a configured personal key authorizes that user to access that
provider without an admin grant. Access funded by a server-side key continues
to require the existing grant, allowlist, admin, or ungated-provider rules.

## Scope

This change includes:

- per-user authentication tokens;
- admin issuance, rotation, and revocation of those tokens;
- a self-service provider-key settings page;
- secure API access to the same operations;
- BYO-key-aware provider authorization;
- personal-key support metadata for the existing providers;
- a distinct Ollama Cloud provider;
- automated tests and operational documentation.

It does not add account registration, password login, password recovery,
billing, provider subscriptions, or multiple simultaneous user tokens.

## Authentication and identity

Add a `UserAccessToken` database model with one active token per `user_id`. The
token is generated from at least 256 bits of cryptographically secure entropy.
Only a SHA-256 hash, a short non-secret display prefix, a random generation ID,
timestamps, and the associated `user_id` are stored. Because the token is high
entropy, the hash is not a password substitute and does not require a slow
password KDF.

An administrator can issue, rotate, or revoke a token from the existing user
detail page. Issuance or rotation invalidates the previous token. The plaintext
token is returned and displayed exactly once; subsequent reads expose only its
prefix and status.

`Authorization: Bearer <user-token>` resolves directly to that token's
`user_id` and the `user` role. A user token cannot select or override another
`user_id` through a route, query string, or JSON body. Requests whose explicit
`user_id` differs from the authenticated identity return `403`.

Existing `ADMIN_TOKEN` and `SERVICE_TOKEN` behavior remains available for
backward compatibility. The new self-service UI accepts a user token, stores
only the resolved `user_id` and non-secret token generation ID in a signed,
HTTP-only session, and never stores the plaintext token in the session. Every
settings request verifies that the generation is still active, so token
rotation or revocation also invalidates existing UI sessions. State-changing UI
requests use a session-bound CSRF token. Logout clears the user session.

## Credential storage and API behavior

Continue using `ProviderConfig.config_encrypted` and the existing Fernet
encryption path. API keys are never logged, rendered back to a browser, or
included in JSON responses. Safe responses expose only `has_api_key` and
non-secret provider metadata.

The existing config routes remain the canonical API:

- `GET /configs/<user_id>` lists safe configuration state;
- `GET /configs/<user_id>/<provider_id>` returns safe state;
- `POST /configs/<user_id>/<provider_id>` creates or replaces a key;
- `DELETE /configs/<user_id>/<provider_id>` removes the configuration.

These routes accept a matching user token in addition to the existing admin or
service credentials. For the five BYO providers, saving a non-empty `api_key`
does not require an existing provider grant. Empty-key updates preserve the
current key, matching existing behavior. Removing a config removes BYO access
and restores the normal server-funded authorization rules.

Provider test requests use the stored key and return connection status plus
safe model information. Provider exceptions are sanitized so an upstream
response cannot echo credentials. Saving a key does not depend on provider
availability; validation is an explicit test action, preventing temporary
provider outages from blocking configuration.

## Authorization rules

Provider authorization is evaluated in this order:

1. gate kill switch disabled;
2. provider is globally ungated;
3. caller is an administrator;
4. the authenticated user has a non-empty personal API key for a provider
   declared as BYO-key-capable;
5. the user has an active admin grant.

Only the five named providers participate in rule 4. Merely having a
`ProviderConfig` row is insufficient; it must decrypt successfully and contain
a non-empty `api_key`. Corrupt encrypted data fails closed and is logged without
secret material.

Personal-key authorization is identity-bound. The gate checks the principal's
`user_id`, not an independently asserted request value. The dispatcher also
loads configuration for that same identity. A personal key always takes
precedence over any server key, so failed personal credentials never silently
fall back to owner-funded credentials.

Claude, opencode.ai, and z.ai retain their current server-key semantics when no
personal key exists. OpenAI remains personal-key-only. Local Ollama remains
ungated and separate from Ollama Cloud.

## Provider metadata

Extend provider registry metadata with an explicit `personal_api_key` flag.
Set it for:

- `claude`;
- `opencode`;
- `openai`;
- `zai`;
- `ollama_cloud`.

Claude's registry metadata will advertise `api_key` as optional. Existing
provider endpoint and organization fields remain available where already
supported, but the self-service page focuses on the key and shows advanced
fields only for providers that declare them.

## Ollama Cloud

Add `ollama_cloud` as a distinct, non-system provider. It requires a personal
API key and defaults to `https://ollama.com`. Requests use Ollama's native API
under `/api`, with `Authorization: Bearer <key>`. Model listing uses
`GET /api/tags`; chat uses `POST /api/chat` with non-streaming responses.

The client follows the existing `BaseClient` result contract, including content
and token usage where Ollama supplies it. It handles connection errors,
timeouts, invalid JSON, authentication failures, and non-success responses
without exposing request headers or keys. It does not share local Ollama pool,
tunnel, or health-routing state.

## Self-service UI

Add `/settings/login`, `/settings/providers`, and `/settings/logout` pages.
After token login, the provider page shows one card per supported BYO provider:

- configured/not configured state;
- masked status only, never key fragments;
- password-style input for add or replace;
- save, test, and remove actions;
- clear success and sanitized error messages;
- whether access comes from a personal key or from server/admin approval.

The UI uses ordinary server-rendered forms with minimal JavaScript enhancement,
matching the existing Jinja admin interface. It remains usable without
JavaScript except for optional inline test feedback.

## Admin UI

Extend each admin user-detail page with personal-token status and actions to
issue, rotate, or revoke a token. Token plaintext is displayed only in the
immediate successful response. The admin page shows which providers have a
personal key but never allows an administrator to reveal that key.

## Database and deployment

The only schema addition is the `user_access_tokens` table. Existing startup
`db.create_all()` creates it without rewriting `provider_configs`. No plaintext
credentials are migrated or introduced.

README documentation will cover user-token issuance, UI and API usage, Ollama
Cloud, and access semantics. AGENTS.md will gain the durable rule that personal
keys authorize only their owning user and must never fall back to server-funded
credentials. Deployment remains a normal image build and container recreation;
no persistent hotfix is acceptable.

## Testing

Tests are written before implementation and cover:

- token generation, hashing, one-time display, rotation, and revocation;
- user-token identity binding and cross-user rejection;
- CSRF protection and self-service session behavior;
- Fernet-encrypted key storage and safe response serialization;
- add, retain-on-empty-update, replace, test, and remove flows;
- personal-key authorization without a grant for all five providers;
- rejection of config rows without keys and fail-closed decrypt errors;
- restoration of grant/server-key rules after key removal;
- precedence of personal keys over server keys;
- compatibility of admin and legacy service authentication;
- Ollama Cloud model listing, chat mapping, auth headers, timeouts, malformed
  responses, and sanitized errors;
- UI rendering without key disclosure;
- full pytest suite and Docker build/boot/`/health` smoke verification.

No live third-party key is required in automated tests. Provider HTTP behavior
is exercised with controlled fake responses. A live provider test may be done
only as an explicitly recorded optional deployment check.
