# Colibri Provider POC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an admin-only, tunnelled Colibri GLM-5.2 provider with validated OpenAI tool calls and native SSE streaming, then run a reversible Mac-Studio POC.

**Architecture:** A `ColibriClient` reaches the loopback-only Mac Studio service through a separate reverse-SSH port and oracle-vm Docker bridge. The gateway opens the upstream response before HTTP headers are sent, limits access to one running plus one waiting request, validates streamed tool names, and relays valid SSE frames without buffering them.

**Tech Stack:** Python 3.9+, Flask, Requests, `threading`, `concurrent.futures`, gunicorn, Docker Compose, macOS launchd/autossh, socat, pytest.

## Global Constraints

- `colibri` is a system provider but only `Config.ADMIN_USER_ID` may use it while `COLIBRI_ENABLED=true`; it is never in `UNGATED_PROVIDERS`.
- The Colibri API key lives only in the root-owned gateway environment file and a mode-600 Mac local environment file. Never log, return, print, or commit it.
- Colibri binds `127.0.0.1:18000`; existing Ollama ports 11434, 11435, and 11440 remain unchanged.
- The bridge is `172.17.0.1:18000 -> 127.0.0.1:18000`; the container calls `http://host.docker.internal:18000`.
- POC admission is one active inference, one waiting request, and a 60-second queue deadline. Colibri is never written to `request_queue`.
- Health probes are concurrent and use five-second Colibri health timeouts.
- The 32-GB Mac Studio is exclusive to Colibri during measurements; stop or remove its Ollama endpoint only for the measurement window.
- Use immutable gateway deployment: tests, `CURRENT_SHA="$(git rev-parse HEAD)"`, `build.sh "$CURRENT_SHA"`, and Compose recreate. Never hot-edit the container.

---

## File structure

| File | Responsibility |
| --- | --- |
| `config.py` | Colibri configuration constants. |
| `providers/base.py` | Shared native-stream handle. |
| `providers/__init__.py` | Provider registry and factory. |
| `providers/colibri.py` | Colibri REST/SSE adapter, admission, tool-name validation. |
| `dispatcher.py` | Admin gate and pre-header streaming lifecycle. |
| `api/openai_api.py` | Native Colibri SSE branch. |
| `worker.py` | Concurrent health checks. |
| `tests/test_colibri_provider.py` | Adapter, parser, admission, and health tests. |
| `tests/test_dispatcher_colibri.py` | Identity, no-queue, and lifecycle tests. |
| `tests/test_openai_api.py` | Flask SSE relay tests. |
| `tests/test_worker.py` | Health-probe concurrency regression. |
| `deploy/colibri/*` | launchd, autossh, bridge, and operational instructions. |
| `.env.example`, `README.md`, `OPERATIONS.md`, `AGENTS.md` | Configuration and operational documentation. |

### Task 1: Add the Colibri provider contract and system access gate

**Files:**
- Modify: `config.py`, `providers/base.py`, `providers/__init__.py`, `dispatcher.py`
- Create: `tests/test_dispatcher_colibri.py`
- Modify: `tests/test_config_access_control.py`

**Interfaces:**
- Produces `ProviderStream(events: Iterator[dict], close: Callable[[], None], result: dict)`.
- Produces `dispatch_stream(user_id, provider_id, model, messages, max_tokens, *, tools, tool_choice, origin_app) -> ProviderStream`.

- [ ] **Step 1: Write the failing identity and registry tests**

```python
def test_colibri_only_loads_for_enabled_admin(monkeypatch):
    import dispatcher
    monkeypatch.setattr(dispatcher.Config, 'COLIBRI_ENABLED', False)
    assert dispatcher._load_config('harald', 'colibri') is None
    monkeypatch.setattr(dispatcher.Config, 'COLIBRI_ENABLED', True)
    monkeypatch.setattr(dispatcher.Config, 'ADMIN_USER_ID', 'harald')
    assert dispatcher._load_config('lisa', 'colibri') is None
    assert dispatcher._load_config('harald', 'colibri') == {}

def test_colibri_registry_is_system_only():
    from providers import PROVIDER_REGISTRY, provider_supports_personal_key
    assert PROVIDER_REGISTRY['colibri']['system'] is True
    assert provider_supports_personal_key('colibri') is False
```

- [ ] **Step 2: Confirm the new tests fail**

Run: `pytest tests/test_dispatcher_colibri.py tests/test_config_access_control.py -q`

Expected: FAIL because Colibri has no configuration, registry, or loading rule.

- [ ] **Step 3: Add fixed POC configuration and stream type**

Add immediately after the Ollama settings:

```python
    COLIBRI_ENABLED = os.getenv('COLIBRI_ENABLED', 'false').lower() == 'true'
    COLIBRI_BASE_URL = os.getenv('COLIBRI_BASE_URL', 'http://host.docker.internal:18000')
    COLIBRI_API_KEY = os.getenv('COLIBRI_API_KEY', '')
    COLIBRI_MODEL_ID = os.getenv('COLIBRI_MODEL_ID', 'glm-5.2-colibri')
    COLIBRI_CONNECT_TIMEOUT_SEC = int(os.getenv('COLIBRI_CONNECT_TIMEOUT_SEC', '10'))
    COLIBRI_STREAM_READ_TIMEOUT_SEC = int(os.getenv('COLIBRI_STREAM_READ_TIMEOUT_SEC', '180'))
    COLIBRI_MAX_WAITING = int(os.getenv('COLIBRI_MAX_WAITING', '1'))
    COLIBRI_QUEUE_TIMEOUT_SEC = int(os.getenv('COLIBRI_QUEUE_TIMEOUT_SEC', '60'))
```

Add this concrete type in `providers/base.py`:

```python
from dataclasses import dataclass, field
from typing import Callable, Iterator

@dataclass
class ProviderStream:
    events: Iterator[dict]
    close: Callable[[], None]
    result: dict = field(default_factory=lambda: {
        'content': [], 'tool_calls': [], 'usage': {}, 'stop_reason': 'stop',
    })
```

Register `colibri` with `system: True`, empty required/optional fields, and `personal_api_key: False`; add the `get_client` branch. In `_load_config`, before the generic system-provider return, add:

```python
    if provider_id == 'colibri':
        return {} if Config.COLIBRI_ENABLED and user_id == Config.ADMIN_USER_ID else None
```

- [ ] **Step 4: Reject non-admin Colibri dispatch before fallback or queueing**

At the start of `dispatch`, add:

```python
    if provider_id == 'colibri' and _load_config(user_id, provider_id) is None:
        raise ProviderUnavailableError('Provider colibri ist nicht für diesen Benutzer verfügbar')
```

Do not add a Colibri branch that creates `RequestQueue` rows.

- [ ] **Step 5: Verify and commit the provider contract**

Run: `pytest tests/test_dispatcher_colibri.py tests/test_config_access_control.py -q`

Expected: PASS.

```bash
git add config.py providers/base.py providers/__init__.py dispatcher.py tests/test_dispatcher_colibri.py tests/test_config_access_control.py
git commit -m "Add: define Colibri provider contract" -m "Verified: pytest tests/test_dispatcher_colibri.py tests/test_config_access_control.py -q"
```

### Task 2: Implement the authenticated Colibri adapter and bounded admission

**Files:**
- Create: `providers/colibri.py`
- Create: `tests/test_colibri_provider.py`

**Interfaces:**
- Produces `ColibriClient.get_models()`, `health()`, `create_message(...)`, and `open_stream(...) -> ProviderStream`.
- Produces `ColibriOverloaded` for 429 and `ColibriProtocolError` for invalid upstream SSE.

- [ ] **Step 1: Write failing adapter tests**

```python
def test_stream_forwards_tools_and_emits_multiple_frames(monkeypatch):
    from providers.colibri import ColibriClient
    post = Mock(return_value=fake_sse_response([
        {'choices': [{'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': None}]},
        {'choices': [{'delta': {'content': 'Hallo'}, 'finish_reason': None}]},
        {'choices': [{'delta': {}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 1, 'completion_tokens': 1}},
    ]))
    monkeypatch.setattr('providers.colibri.requests.post', post)
    stream = ColibriClient({}).open_stream('glm-5.2-colibri', [{'role': 'user', 'content': 'Hi'}], 16, tools=[{'type': 'function', 'function': {'name': 'read_file'}}], tool_choice='auto')
    assert len(list(stream.events)) == 3
    assert post.call_args.kwargs['json']['stream'] is True
    assert post.call_args.kwargs['json']['tools'][0]['function']['name'] == 'read_file'
    stream.close()

def test_stream_rejects_an_unoffered_tool_before_yield(monkeypatch):
    from providers.colibri import ColibriClient, ColibriProtocolError
    monkeypatch.setattr('providers.colibri.requests.post', Mock(return_value=fake_sse_response([
        {'choices': [{'delta': {'tool_calls': [{'function': {'name': 'erase_all', 'arguments': '{}'}}]}, 'finish_reason': None}]},
    ])))
    stream = ColibriClient({}).open_stream('glm-5.2-colibri', [], 16, tools=[{'type': 'function', 'function': {'name': 'read_file'}}], tool_choice='auto')
    with pytest.raises(ColibriProtocolError):
        next(stream.events)
    stream.close()
```

- [ ] **Step 2: Confirm adapter tests fail**

Run: `pytest tests/test_colibri_provider.py -q`

Expected: FAIL because `providers.colibri` does not exist.

- [ ] **Step 3: Implement the minimal safe adapter**

Implement these signatures:

```python
class ColibriClient(BaseClient):
    def __init__(self, config: dict): ...
    def get_models(self) -> list[str]: ...
    def health(self) -> bool: ...
    def create_message(self, model: str, messages: list[dict], max_tokens: int = 600, *, tools: list[dict] | None = None, tool_choice=None) -> dict: ...
    def open_stream(self, model: str, messages: list[dict], max_tokens: int = 600, *, tools: list[dict] | None = None, tool_choice=None) -> ProviderStream: ...
```

`__init__` raises `ValueError('Colibri is disabled or has no API key')` unless enabled and keyed. Use the server API key only in the standard Bearer authorization header. `get_models()` calls `/v1/models` with a five-second timeout and returns only the configured model ID. `health()` calls `/health` and returns true only for `{"status":"ok"}`.

For native streaming, submit this payload with `stream=True` and call `raise_for_status()` before returning the handle:

```python
payload = {'model': self._model_id, 'messages': messages, 'max_completion_tokens': max_tokens, 'stream': True}
if tools:
    payload['tools'] = tools
if tool_choice is not None:
    payload['tool_choice'] = tool_choice
```

Use `requests.post(..., stream=True, timeout=(Config.COLIBRI_CONNECT_TIMEOUT_SEC, Config.COLIBRI_STREAM_READ_TIMEOUT_SEC))`. Parse only `data:` lines, ignore blank/comment frames, stop on `[DONE]`, JSON-decode each frame, and require a non-empty `choices` list. Before yielding a frame, verify every non-empty `delta.tool_calls[*].function.name` occurs in the caller's offered tool names. Update `ProviderStream.result` with text, tool calls, usage, and the final finish reason. `close()` must be idempotent, close the response, and release admission.

Implement admission using one active `threading.Semaphore`, a lock-protected waiter counter, `COLIBRI_MAX_WAITING=1`, and `COLIBRI_QUEUE_TIMEOUT_SEC=60`. A second waiting request raises `ColibriOverloaded('colibri_queue_full')`; a timed-out waiter raises `ColibriOverloaded('colibri_queue_timeout')`.

- [ ] **Step 4: Verify and commit the adapter**

Run: `pytest tests/test_colibri_provider.py -q && python -m py_compile providers/colibri.py`

Expected: PASS and exit code 0.

```bash
git add providers/colibri.py tests/test_colibri_provider.py
git commit -m "Add: implement Colibri provider client" -m "Verified: pytest tests/test_colibri_provider.py -q; python -m py_compile providers/colibri.py"
```

### Task 3: Add pre-header dispatch and native OpenAI SSE relay

**Files:**
- Modify: `dispatcher.py`, `api/openai_api.py`
- Modify: `tests/test_dispatcher_colibri.py`, `tests/test_openai_api.py`

**Interfaces:**
- Consumes `ColibriClient.open_stream()` and `ProviderStream` from Tasks 1–2.
- Produces `dispatch_stream(...) -> ProviderStream`; return occurs only after upstream HTTP status has been checked.

- [ ] **Step 1: Write failing dispatcher and API tests**

```python
def test_dispatch_stream_opens_colibri_before_response_headers(app, monkeypatch):
    from dispatcher import dispatch_stream
    opened = Mock(return_value=fake_provider_stream())
    monkeypatch.setattr('dispatcher._load_config', lambda *_: {})
    monkeypatch.setattr('dispatcher.get_client', lambda *_: SimpleNamespace(open_stream=opened))
    monkeypatch.setattr('dispatcher.health_tracker.is_healthy', lambda _: True)
    assert dispatch_stream('harald', 'colibri', 'glm-5.2-colibri', [], 16, tools=[], tool_choice='auto') is opened.return_value
    opened.assert_called_once()

def test_colibri_stream_relays_each_upstream_frame(app, client, monkeypatch):
    import api.openai_api as api
    monkeypatch.setattr(api, 'dispatch_stream', lambda **_: fake_provider_stream(events=[
        {'choices': [{'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': None}]},
        {'choices': [{'delta': {'content': 'Hallo'}, 'finish_reason': None}]},
        {'choices': [{'delta': {}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 1, 'completion_tokens': 1}},
    ]))
    r = client.post('/v1/chat/completions', json={'model': 'colibri/glm-5.2-colibri', 'messages': [{'role': 'user', 'content': 'Hi'}], 'stream': True}, headers={'Authorization': 'Bearer admin-test-token'})
    assert r.status_code == 200
    assert r.data.decode().count('data: {') == 3
    assert r.data.decode().endswith('data: [DONE]\n\n')
```

- [ ] **Step 2: Confirm the tests fail**

Run: `pytest tests/test_dispatcher_colibri.py tests/test_openai_api.py -q`

Expected: FAIL because `dispatch_stream` and the Colibri SSE branch do not exist.

- [ ] **Step 3: Implement stream dispatch without fallback or SQLite queueing**

Add this function to `dispatcher.py`:

```python
def dispatch_stream(user_id: str, provider_id: str, model: str, messages: list, max_tokens: int, *, tools: list[dict] | None, tool_choice, origin_app: str | None = None) -> ProviderStream:
    if provider_id != 'colibri':
        raise ProviderUnavailableError('Native streaming is only available for colibri')
    cfg = _load_config(user_id, provider_id)
    if cfg is None or not health_tracker.is_healthy(provider_id):
        raise ProviderUnavailableError('Provider colibri nicht erreichbar, kein Fallback/Queue konfiguriert')
    try:
        stream = get_client(provider_id, cfg).open_stream(model, messages, max_tokens, tools=tools, tool_choice=tool_choice)
    except Exception as exc:
        health_tracker.set_status(provider_id, False, reason=type(exc).__name__, persistent=False)
        raise ProviderUnavailableError('Provider colibri nicht erreichbar, kein Fallback/Queue konfiguriert') from exc
    return _instrument_stream(stream, user_id, provider_id, model, messages, origin_app)
```

`_instrument_stream` must wrap `events`; on normal completion it writes one success UsageEvent and audit note from `stream.result`, then marks Colibri healthy. On parser/network failure it writes one error UsageEvent and marks it unhealthy. Its `finally` always calls `stream.close()`. It must not log prompt text, tool arguments, or credentials, and must not call `RequestQueue`.

- [ ] **Step 4: Relay frames without buffering or rewriting**

In `chat_completions`, retain the current buffered code for every provider except `provider_id == 'colibri' and stream is True`. For that branch call `dispatch_stream` before constructing `Response`. Map `ProviderUnavailableError` to the existing 503 JSON and `ColibriOverloaded` to OpenAI-shaped 429 JSON with `error.code` from the exception.

Use this generator exactly:

```python
def generate_colibri():
    try:
        for frame in provider_stream.events:
            yield f'data: {json.dumps(frame, ensure_ascii=False)}\n\n'
        yield 'data: [DONE]\n\n'
    finally:
        provider_stream.close()
```

Return it via `Response(stream_with_context(generate_colibri()), mimetype='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})`. Do not synthesize/coalesce content chunks. If an error follows committed headers, log only the exception class and end the generator; never attempt a second HTTP response.

- [ ] **Step 5: Verify and commit native streaming**

Run: `pytest tests/test_dispatcher_colibri.py tests/test_openai_api.py -q`

Expected: PASS, including multiple data frames, `[DONE]`, offered-tool enforcement, pre-header 503, and queue 429.

```bash
git add dispatcher.py api/openai_api.py tests/test_dispatcher_colibri.py tests/test_openai_api.py
git commit -m "Add: stream Colibri responses through OpenAI API" -m "Verified: pytest tests/test_dispatcher_colibri.py tests/test_openai_api.py -q"
```

### Task 4: Make health probes concurrent

**Files:**
- Modify: `worker.py`, `tests/test_worker.py`

**Interfaces:**
- Produces `_tick(app)` that launches all `_check_provider(provider_id)` calls concurrently and isolates individual exceptions.

- [ ] **Step 1: Add a failing overlap test**

```python
def test_tick_starts_independent_health_checks_before_waiting(app, monkeypatch):
    import worker
    barrier = threading.Barrier(2)
    release = threading.Event()
    def slow_check(_):
        barrier.wait(timeout=1)
        release.wait(timeout=1)
        return True
    monkeypatch.setattr(worker, 'PROVIDER_REGISTRY', {'one': {}, 'two': {}})
    monkeypatch.setattr(worker, '_check_provider', slow_check)
    thread = threading.Thread(target=worker._tick, args=(app,), daemon=True)
    thread.start()
    barrier.wait(timeout=1)
    release.set()
    thread.join(timeout=1)
    assert not thread.is_alive()
```

- [ ] **Step 2: Confirm the test fails under serial polling**

Run: `pytest tests/test_worker.py -q`

Expected: FAIL or time out in the new overlap test because `_tick` currently loops serially.

- [ ] **Step 3: Use a bounded thread pool**

Import `ThreadPoolExecutor` and `as_completed`, then replace `_tick` with:

```python
def _tick(app: Flask) -> None:
    provider_ids = tuple(PROVIDER_REGISTRY)
    if not provider_ids:
        return
    with app.app_context():
        with ThreadPoolExecutor(max_workers=min(8, len(provider_ids)), thread_name_prefix='provider-health') as pool:
            futures = {pool.submit(_check_provider, pid): pid for pid in provider_ids}
            for future in as_completed(futures):
                pid = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    logger.warning('health-check %s crashed: %s', pid, type(exc).__name__)
```

- [ ] **Step 4: Verify and commit concurrent health checks**

Run: `pytest tests/test_worker.py tests/test_colibri_provider.py -q`

Expected: PASS.

```bash
git add worker.py tests/test_worker.py
git commit -m "Fix: run provider health checks in parallel" -m "Verified: pytest tests/test_worker.py tests/test_colibri_provider.py -q"
```

### Task 5: Add reproducible Mac Studio, tunnel, and bridge assets

**Files:**
- Create: `deploy/colibri/com.wolfini.colibri.plist`
- Create: `deploy/colibri/com.wolfini.colibri-tunnel.plist`
- Create: `deploy/colibri/ai-provider-colibri-bridge.service`
- Create: `deploy/colibri/README.md`
- Modify: `.env.example`, `README.md`, `OPERATIONS.md`, `AGENTS.md`

**Interfaces:**
- Consumes the `COLIBRI_*` environment names from Task 1.
- Produces a local-only Mac service and a separate remote listener on port 18000; it does not edit Ollama launch agents.

- [ ] **Step 1: Create secret-free launchd and bridge templates**

The Colibri launchd template uses `RunAtLoad`, `KeepAlive`, and internal-disk logs under `~/Library/Logs/colibri/`. It executes this command after loading `COLI_MODEL` and `COLI_API_KEY` from a mode-600 local environment file outside the repository:

```xml
<array>
  <string>/bin/zsh</string>
  <string>-lc</string>
  <string>exec /opt/colibri/c/coli serve --host 127.0.0.1 --port 18000 --model-id glm-5.2-colibri --max-queue 1 --queue-timeout 60</string>
</array>
```

The dedicated autossh template retains `-M 0`, `ServerAliveInterval=30`, `ServerAliveCountMax=3`, `ExitOnForwardFailure=yes`, and contains exactly:

```xml
<string>-R</string>
<string>18000:127.0.0.1:18000</string>
<string>opc@oracle-vm</string>
```

The bridge unit binds only Docker's host gateway:

```ini
[Service]
Type=simple
ExecStart=/usr/bin/socat TCP-LISTEN:18000,bind=172.17.0.1,fork,reuseaddr TCP:127.0.0.1:18000
Restart=always
RestartSec=5
```

- [ ] **Step 2: Document the preflight before any model download**

Add this exact read-only sequence to `deploy/colibri/README.md`:

```bash
COLIBRI_MODEL_DIR=/Volumes/ColibriModels/glm52_i4
sysctl -n hw.memsize
df -h "$COLIBRI_MODEL_DIR"
diskutil info "$COLIBRI_MODEL_DIR"
pgrep -alf 'ollama|coli|glm' || true
launchctl print gui/$(id -u)/com.wolfini.ollama-tunnel 2>/dev/null || true
```

The executor must use the shown `COLIBRI_MODEL_DIR` only after that path has been created on the Mac Studio's stable directly attached model volume. The documentation must require 400 GB free, then require `./setup.sh`, `COLI_MODEL="$COLIBRI_MODEL_DIR" ./coli doctor`, and Colibri's `iobench` before gateway enablement. It must state that Ollama is stopped/rerouted only for a measurement window and no existing model/tunnel is deleted.

- [ ] **Step 3: Document secure install, checks, and rollback**

Include these checks:

```bash
curl -fsS -H "Authorization: Bearer $COLI_API_KEY" http://127.0.0.1:18000/health
ss -tln | grep -E '127\.0\.0\.1:18000|\[::1\]:18000'
curl -fsS -H "Authorization: Bearer $COLI_API_KEY" http://127.0.0.1:18000/v1/models
ss -tln | grep '172\.17\.0\.1:18000'
```

Rollback stops/unloads only the two new launchd jobs, stops/disables only `ai-provider-colibri-bridge.service`, removes `COLIBRI_ENABLED` from gateway environment, recreates the prior SHA-tagged gateway image, and restores prior Ollama routing. Model files remain until the owner separately asks for deletion.

- [ ] **Step 4: Add redacted environment and operations documentation**

Add the following unkeyed block to `.env.example`, then document each value in README/OPERATIONS and record the port/rollback rule in AGENTS:

```dotenv
COLIBRI_ENABLED=false
COLIBRI_BASE_URL=http://host.docker.internal:18000
COLIBRI_API_KEY=
COLIBRI_MODEL_ID=glm-5.2-colibri
COLIBRI_CONNECT_TIMEOUT_SEC=10
COLIBRI_STREAM_READ_TIMEOUT_SEC=180
COLIBRI_MAX_WAITING=1
COLIBRI_QUEUE_TIMEOUT_SEC=60
```

- [ ] **Step 5: Validate and commit operations assets**

Run: `plutil -lint deploy/colibri/com.wolfini.colibri.plist deploy/colibri/com.wolfini.colibri-tunnel.plist`

Expected: both files are syntactically valid property lists.

Run: `systemd-analyze verify deploy/colibri/ai-provider-colibri-bridge.service`

Expected: exit code 0 on oracle-vm.

Run: `git diff --check`

Expected: exit code 0.

```bash
git add deploy/colibri .env.example README.md OPERATIONS.md AGENTS.md
git commit -m "Doc: add Colibri POC operations" -m "Verified: plutil -lint deploy/colibri/com.wolfini.colibri.plist deploy/colibri/com.wolfini.colibri-tunnel.plist; git diff --check"
```

### Task 6: Run controlled preflight and acceptance

**Files:**
- Modify: `docs/superpowers/plans/2026-07-18-colibri-provider-poc.md` only to append non-sensitive execution evidence.

**Interfaces:**
- Consumes all code and assets from Tasks 1–5.
- Produces a documented pass, hold, or rollback decision.

- [ ] **Step 1: Verify the repository before runtime changes**

Run:

```bash
pytest tests/test_colibri_provider.py tests/test_dispatcher_colibri.py tests/test_openai_api.py tests/test_worker.py -q
pytest -q
git diff --check
```

Expected: every test passes and no whitespace errors exist. Do not deploy on any failure.

- [ ] **Step 2: Complete the Mac Studio preflight**

Run the Task 5 commands and record only RAM GiB, free-space GiB, mount/filesystem status, I/O benchmark, doctor result, and whether Ollama was rerouted. Do not record any API key, complete environment file, prompt, or local user path.

Expected: 32 GB RAM, at least 400 GB local free storage, no competing Ollama process, and passing setup/doctor checks. Any failed condition ends the POC before tunnel or gateway deployment.

- [ ] **Step 3: Start Colibri and prove it cannot listen publicly**

Install the two launchd jobs, use `launchctl kickstart -k`, and run the Task 5 local/tunnel/bridge checks.

Expected: authenticated `/health` and `/v1/models` work through the tunnel; there is no `0.0.0.0:18000` or `[::]:18000` listener.

- [ ] **Step 4: Deploy the gateway immutably**

On oracle-vm run `CURRENT_SHA="$(git rev-parse HEAD)"` followed by `./build.sh "$CURRENT_SHA"`, add only the redacted `COLIBRI_*` values to the root-owned environment file, then run:

```bash
cd /opt/ai-provider-service
sudo docker compose up -d --force-recreate ai-provider
```

Expected: a healthy container whose running image tag is the built SHA. A `docker restart` is not acceptable because it does not reload environment values.

- [ ] **Step 5: Execute the authenticated acceptance matrix**

1. `GET /v1/models` contains only `colibri/glm-5.2-colibri` for the enabled admin while healthy.
2. A non-stream text request returns a valid OpenAI choice with concrete finish reason.
3. A `stream=true` request with offered `read_file` yields multiple JSON SSE frames, only that offered tool name if a tool is called, a final finish reason, and `[DONE]`.
4. Stop Colibri once: model discovery hides it and a new request is bounded 503 while another provider still works.
5. Restart using `launchctl kickstart -k`: health/model visibility recover and no Colibri `request_queue` row exists.

- [ ] **Step 6: Record outcome and commit it**

Pass requires all matrix rows with no swap/OOM and no buffered single-chunk stream. Hold means integration passes but latency is unsuitable for interactive traffic. Rollback is required for failed preflight, memory pressure, public listener, or broken tool/stream validation.

```bash
git add docs/superpowers/plans/2026-07-18-colibri-provider-poc.md
git commit -m "Doc: record Colibri POC outcome" -m "Verified: focused tests, full pytest, Docker smoke, and authenticated POC matrix"
```

## Plan self-review

- Coverage: Tasks 1–4 implement identity, tools, SSE, admission, failure mapping, and parallel health; Task 5 covers Mac/tunnel/bridge/docs; Task 6 covers preflight, deploy, acceptance, and rollback.
- Scope: no personal Colibri keys, automatic fallback, price tracking, media, or broad routing is introduced.
- Contract consistency: Task 1 defines `ProviderStream`/`dispatch_stream`; Task 2 returns that handle; Task 3 consumes it; later tasks leave its API unchanged.
