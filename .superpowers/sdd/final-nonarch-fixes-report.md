# Colibri final non-architecture fixes

## Scope

Applied the final review findings that do not require a cross-process admission
decision. No Task 6 HOLD state, production runtime, deployment, or process-local
admission architecture was changed.

## Root causes and fixes

1. The generic non-stream dispatcher loaded `ProviderConfig` and selected
   request or persisted fallback policy before Colibri had a terminal path.
   Colibri now returns from an early dedicated branch before generic fallback
   or SQLite queue logic. Unavailable and failed calls raise
   `ProviderUnavailableError` directly.
2. Non-stream Colibri responses normalized tool calls without validating their
   structure or offered function name. A shared validator now rejects malformed,
   empty-name, and unoffered calls before either non-stream return or streamed
   frame relay.
3. The SSE iterator treated a clean transport EOF as successful even when the
   upstream sent neither `[DONE]` nor a concrete `finish_reason`. Such truncated
   streams now raise `ColibriProtocolError`; streams with a terminal finish
   reason keep their accumulated content, usage, tools, and stop reason.
4. `/providers` included Colibri with `allowed: false`, and the unauthenticated
   aggregate `/health` exposed its status. `/providers` now omits Colibri unless
   the authenticated principal is allowed; public `/health` always omits it.
5. The Mac Studio listener check in `deploy/colibri/README.md` now uses macOS
   `lsof`. The oracle-vm bridge check remains Linux `ss`.
6. The health-worker overlap test now uses a three-party barrier and tolerant
   three-second deadlines instead of a scheduler-sensitive 200 ms event wait.

## TDD evidence

The initial regression run produced nine expected failures:

```text
python3 -m pytest \
  tests/test_dispatcher_colibri.py::test_colibri_dispatch_ignores_request_fallback_after_primary_failure \
  tests/test_dispatcher_colibri.py::test_colibri_dispatch_ignores_persisted_fallback_when_unavailable \
  tests/test_colibri_provider.py::test_stream_rejects_clean_eof_without_terminal_marker \
  tests/test_colibri_provider.py::test_create_message_rejects_unoffered_tool_call \
  tests/test_colibri_provider.py::test_create_message_rejects_malformed_tool_call \
  tests/test_provider_visibility.py::test_providers_hides_colibri_entirely_when_principal_is_not_allowed \
  tests/test_provider_visibility.py::test_public_health_hides_colibri -q
-> 9 failed
```

After the minimal implementation:

```text
python3 -m pytest tests/test_dispatcher_colibri.py tests/test_colibri_provider.py \
  tests/test_provider_visibility.py tests/test_worker.py -q
-> 51 passed

python3 -m pytest tests/test_openai_api.py tests/test_dispatcher_fallback.py \
  tests/test_gate.py tests/test_gate_integration.py \
  tests/test_providers_error_safety.py -q
-> 46 passed
```

The first full run exposed one test-fixture ownership issue: the positive
visibility test patched the reloaded `Config` class instead of the class owned
by eagerly imported `api.auth`/`api.gate`. The test was corrected to patch the
owning modules, then passed both focused and full-suite runs.

## Independent review

The read-only reviewer confirmed the six requested fixes and found no critical
issues. It suggested preserving `ColibriOverloaded` as a non-stream 429 instead
of converting it to `ProviderUnavailableError`. That suggestion was not applied
because this task explicitly requires unavailable/failed non-stream Colibri
dispatch to raise `ProviderUnavailableError` directly and prohibits admission
behavior changes. The existing native-stream 429 contract remains unchanged.

## Verification

```text
python3 -m pytest -q
-> 398 passed

python3 -m py_compile dispatcher.py providers/colibri.py api/providers_api.py \
  api/health_api.py worker.py
-> exit 0

git diff --check
-> clean
```

`git fetch origin` completed before commit as required. No production or live
provider smoke test was run, and nothing was deployed.
