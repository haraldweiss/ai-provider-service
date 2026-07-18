# Colibri global admission correction

## Outcome

Colibri itself is now the sole global admission authority across both Gunicorn
workers. The gateway has no process-local inference semaphore, waiter counter,
or admission timeout. The existing launchd command remains authoritative:

```text
coli serve ... --max-queue 1 --queue-timeout 60
```

Every gateway worker opens its upstream request directly. The adapter maps the
Colibri server's documented OpenAI error codes as follows:

- HTTP 429 `error.code=queue_timeout` -> `colibri_queue_timeout`
- every other HTTP 429, including `queue_full` -> `colibri_queue_full`
- transport/read timeouts without that protocol code remain their original
  upstream exceptions

The existing OpenAI-shaped gateway 429 mapping remains active. Colibri still
has no provider fallback and is never persisted in `request_queue`.

## TDD evidence

The RED run targeted the two architecture regressions:

```text
pytest \
  tests/test_colibri_provider.py::test_open_stream_maps_upstream_queue_timeout_code \
  tests/test_colibri_provider.py::test_gateway_opens_requests_directly_for_service_admission -q
-> 2 failed
```

The failures showed that the adapter mapped `queue_timeout` to
`colibri_queue_full` and that the second request raised the former local
`colibri_queue_timeout` before a second `requests.post` call.

After the minimal provider change:

```text
pytest tests/test_colibri_provider.py -q
-> 20 passed

pytest tests/test_config_access_control.py -q
-> 8 passed

pytest tests/test_colibri_provider.py tests/test_dispatcher_colibri.py \
  tests/test_openai_api.py tests/test_worker.py -q
-> 61 passed

pytest -q
-> 399 passed

python3 -m py_compile providers/colibri.py config.py api/openai_api.py dispatcher.py
-> exit 0

git diff --check
-> clean
```

A combined non-canonical focused command that ran the config-module reload
tests immediately before the OpenAI API module produced 12 authentication
401s because `api.auth` retained the pre-reload `Config` class. Running each
ownership boundary independently passed, and the canonical full suite passed
399/399. No unrelated auth fixture was changed.

## Scope and operations

Gateway-only `COLIBRI_MAX_WAITING` and `COLIBRI_QUEUE_TIMEOUT_SEC` settings were
removed from code, tests, `.env.example`, and operator documentation. The
launchd service template still uses `--max-queue 1 --queue-timeout 60`.

The existing Task 6 HOLD remains unchanged. No runtime preflight, model
download, service/tunnel/bridge change, container build, production deploy, or
live provider request was performed.
