# Task 3 — Colibri native OpenAI SSE relay

## Scope

Implemented only the Task 3 dispatcher/OpenAI integration and its tests:

- `dispatcher.py`
- `api/openai_api.py`
- `tests/test_dispatcher_colibri.py`
- `tests/test_openai_api.py`

No worker, Docker, deployment, queue-worker, or provider-admission configuration
was changed.

## TDD evidence

1. Added dispatcher tests for pre-header `open_stream()` admission, successful
   lifecycle instrumentation, and parser/network failure instrumentation.
2. Added OpenAI API tests for native frame relay, pre-header unavailable `503`,
   and pre-header bounded-admission `429`.
3. RED command:

   ```text
   python3 -m pytest tests/test_dispatcher_colibri.py tests/test_openai_api.py -q
   6 failed, 17 passed
   ```

   The failures were the expected absent `dispatch_stream` import/API branch.
4. The initial GREEN run revealed only test-fixture access-state leakage when
   the complete suite reloads `config`. The Colibri branch tests now isolate
   their access concern by stubbing `is_allowed`, so they test native relay
   behavior rather than configuration-reload order.

## Implementation review

- `dispatch_stream()` accepts only `colibri`; it loads the existing enabled
  admin configuration, checks health, and calls `ColibriClient.open_stream()`
  before a Flask `Response` is constructed.
- It contains no fallback or `RequestQueue` path. Admission failures mark
  Colibri unhealthy and become the existing unavailable error; bounded
  `ColibriOverloaded` is deliberately preserved for the API `429` mapping.
- `_instrument_stream()` replaces only the stream iterator on the existing
  `ProviderStream`. Normal exhaustion records exactly one success `UsageEvent`,
  audit note, and healthy state. An iterator/parser/network exception records
  exactly one error event and unhealthy state. Its `finally` closes the stream.
  The underlying Colibri close function is idempotent, so the API relay can
  also defensively close it without releasing admission twice.
- Lifecycle and post-header relay logs include only `type(exc).__name__`; they
  never format upstream exception text, prompts, tool arguments, or credentials.
- The Colibri streaming API branch relays every validated frame with one SSE
  `data:` record and then `[DONE]`. It does not synthesize role/content/tool
  frames or coalesce frames. All non-Colibri paths retain the previous buffered
  dispatcher and SSE behavior unchanged.
- Before headers, `ProviderUnavailableError` produces the established `503`
  response; `ColibriOverloaded` produces OpenAI-shaped `429` with its exception
  code. After headers, relay errors are only class-name logged and end the
  generator.

## Verification

```text
python3 -m pytest tests/test_dispatcher_colibri.py tests/test_openai_api.py -q
23 passed in 1.36s

python3 -m pytest tests/test_colibri_provider.py -q
14 passed in 0.06s

python3 -m pytest -q
382 passed, 1 existing SQLAlchemy Query.get() deprecation warning in 11.62s

git diff --check
clean
```

## Independent review

An independent Task 3 review found no critical, high, medium, or low findings
and approved the implementation. It specifically confirmed pre-header
admission, no fallback/queueing, exactly-once lifecycle instrumentation, safe
logging, OpenAI `503`/`429` mappings, and preservation of non-Colibri behavior.

## Residual concerns

None identified for Task 3. The known SQLAlchemy `Query.get()` deprecation
warning is pre-existing and outside this scope.

## Disconnect lifecycle follow-up (2026-07-18)

### Scope

Addressed the stream-abort lifecycle leak in:

- `dispatcher.py`
- `api/openai_api.py`
- `tests/test_dispatcher_colibri.py`
- `tests/test_openai_api.py`

### Behavior

- The Colibri `Response` now registers `provider_stream.close` with Flask's
  response-close lifecycle. A client disconnect before the first body
  iteration therefore releases the already-admitted provider stream.
- `_instrument_stream()` now owns an idempotent close wrapper. It closes the
  instrumented iterator first, so a started but abandoned stream receives
  `GeneratorExit`, writes one error usage lifecycle record, and then closes
  the underlying provider exactly once. Explicit asyncio cancellation follows
  the same safe error-accounting path. Client-side aborts deliberately leave
  the existing provider-health state unchanged; they do not indicate that
  Colibri is unavailable.
- Normal relay, upstream parser/network errors, frame shape, no queueing, and
  no-fallback policy remain unchanged.

### TDD evidence

1. Added a response-close-before-first-iteration regression and a
   post-frame-abandonment regression before changing production code.
2. RED command:

   ```text
   python3 -m pytest tests/test_dispatcher_colibri.py::test_dispatch_stream_abandon_after_frame_records_one_error_and_closes tests/test_openai_api.py::test_colibri_response_close_before_first_iteration_closes_provider_stream -q
   -> 1 failed, 1 passed
   ```

   The failure showed no error UsageEvent was recorded for the
   `GeneratorExit` abandonment path.
3. GREEN command:

   ```text
   -> 2 passed
   ```

### Verification

```text
python3 -m pytest tests/test_dispatcher_colibri.py tests/test_openai_api.py tests/test_colibri_provider.py -q
-> 40 passed

python3 -m py_compile dispatcher.py api/openai_api.py
-> exit 0

python3 -m pytest -q
-> 384 passed, 1 existing SQLAlchemy Query.get() deprecation warning

git diff --check
-> clean
```
