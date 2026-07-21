# oMLX Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Add the MacBook oMLX server as an authenticated, fallback-capable system provider without replacing the Ollama pool.

**Architecture:** A new omlx provider calls oMLX's OpenAI-compatible /v1 API. A dedicated Mac autossh agent forwards 127.0.0.1:8000 to Oracle 127.0.0.1:11442. A root-owned Oracle socat service binds 172.17.0.1:11442 and forwards to the loopback tunnel. The container connects through host.docker.internal:11442/v1.

**Tech Stack:** Python 3.9, Flask, requests, pytest, launchd/autossh, systemd/socat, Docker Compose.

## Global Constraints

- oMLX binds only to 127.0.0.1:8000 on the MacBook; never directly to LAN, port 80, or port 443.
- Oracle port 11442 is reserved for oMLX. GatewayPorts no preserves a loopback-only SSH listener.
- OMLX_API_KEY is opaque. It is never logged, returned, committed, or passed on a command line. The only production copy is the root-owned /etc/ai-provider/ai-provider.env file.
- Use OMLX_BASE_URL=http://host.docker.internal:11442/v1, never a hardcoded Docker gateway address.
- Preserve Ollama ports, agents, and pool routing. An oMLX failure must reach the existing dispatcher fallback/queue logic.
- Health remains parallel through the existing worker; client health only performs a short GET /models.

---

### Task 1: Test and implement the oMLX system provider

**Files:**
- Create: providers/omlx.py
- Modify: providers/__init__.py
- Modify: config.py
- Modify: api/provider_visibility.py
- Test: tests/test_omlx_provider.py
- Test: tests/test_provider_visibility.py

**Interfaces:**
- OmlxClient consumes OMLX_BASE_URL, OMLX_API_KEY, and optional api_endpoint/api_key config overrides.
- OmlxClient exposes get_models(), create_message(model, messages, max_tokens, *, tools=None), and health().
- Registry provider omlx is system-backed, has optional api key/endpoint fields, and does not support personal keys.

- [ ] **Step 1: Write failing provider tests**

```python
@patch('providers.omlx.requests.get')
def test_get_models_uses_bearer_auth(mock_get):
    mock_get.return_value.json.return_value = {'data': [{'id': 'devstral'}]}
    mock_get.return_value.raise_for_status.return_value = None
    client = OmlxClient({'api_endpoint': 'http://omlx:11442/v1', 'api_key': 'test-key'})
    assert client.get_models() == ['devstral']
    mock_get.assert_called_once_with(
        'http://omlx:11442/v1/models',
        headers={'Authorization': 'Bearer test-key'}, timeout=5,
    )

@patch('providers.omlx.requests.post')
def test_create_message_forwards_tools_and_maps_response(mock_post):
    mock_post.return_value.json.return_value = {
        'choices': [{'message': {'content': 'done'}}],
        'usage': {'prompt_tokens': 3, 'completion_tokens': 2},
    }
    mock_post.return_value.raise_for_status.return_value = None
    result = OmlxClient({'api_key': 'test-key'}).create_message(
        'devstral', [{'role': 'user', 'content': 'hi'}], 99,
        tools=[{'type': 'function', 'function': {'name': 'read'}}],
    )
    assert result == {'content': [{'text': 'done'}], 'usage': {'input_tokens': 3, 'output_tokens': 2}}
    assert mock_post.call_args.kwargs['json']['tools'][0]['function']['name'] == 'read'
```

- [ ] **Step 2: Establish red state**

Run: pytest tests/test_omlx_provider.py -q

Expected: import failure because providers.omlx does not exist.

- [ ] **Step 3: Implement minimal client and registration**

```python
# config.py
OMLX_BASE_URL = os.getenv('OMLX_BASE_URL', 'http://host.docker.internal:11442/v1')
OMLX_API_KEY = os.getenv('OMLX_API_KEY', '')

# providers/omlx.py
class OmlxClient(BaseClient):
    def __init__(self, config: dict):
        self._base_url = (config.get('api_endpoint') or Config.OMLX_BASE_URL).rstrip('/')
        self._api_key = config.get('api_key') or Config.OMLX_API_KEY
        if not self._api_key:
            raise ValueError('oMLX: api_key oder OMLX_API_KEY erforderlich')
```

Use GET {base}/models and POST {base}/chat/completions. POST sends model, messages, max_tokens, and optional tools. Map choices[0].message.content and OpenAI usage to the BaseClient response contract. Register omlx as system=True, requires=[], optional=['api_key', 'api_endpoint'], personal_api_key=False. Add the factory branch.

- [ ] **Step 4: Test provider visibility**

Add an assertion that provider_requires_user_key('lisa', 'omlx') is false. Preserve the default UNGATED_PROVIDERS value of ollama; production explicitly adds omlx only after deployment.

- [ ] **Step 5: Verify green**

Run: pytest tests/test_omlx_provider.py tests/test_provider_visibility.py tests/test_config_access_control.py -q

Expected: all selected tests pass.

### Task 2: Document topology and configuration

**Files:**
- Modify: .env.example
- Modify: README.md
- Modify: OPERATIONS.md
- Modify: AGENTS.md

- [ ] **Step 1: Add non-secret configuration**

```dotenv
# oMLX on the MacBook through a reverse SSH tunnel and Oracle bridge.
OMLX_BASE_URL=http://host.docker.internal:11442/v1
# OMLX_API_KEY=  # set only in the Oracle root-owned env file
```

- [ ] **Step 2: Document path, health checks, and rollback**

Document Mac 127.0.0.1:8000 to Oracle 127.0.0.1:11442 to socat 172.17.0.1:11442 to host.docker.internal:11442 to container. Rollback disables the dedicated bridge/agent and removes OMLX variables during a forced container recreate; it never alters Ollama.

- [ ] **Step 3: Add durable rules**

Update AGENTS with port 11442, local-only binding, log locations on internal disk, and opaque key handling.

### Task 3: Verify and commit

- [ ] **Step 1: Run integration tests**

Run: pytest tests/test_omlx_provider.py tests/test_provider_visibility.py tests/test_dispatcher_fallback.py tests/test_openai_api.py -q

- [ ] **Step 2: Run complete checks**

Run: pytest -q && ruff check . && git diff --check

Expected: all pass.

- [ ] **Step 3: Review secret safety and commit**

Confirm no oMLX API key is present in source, tests, docs, logs, or diff. Commit code and tests with Add oMLX system provider; commit documentation separately with Doc oMLX tunnel deployment. Record verification in each commit body.

### Task 4: Stage and deploy tunnel-backed provider

**Files:**
- Create on Mac: /Users/haraldweiss/Library/LaunchAgents/com.wolfini.omlx-tunnel.plist
- Create on Mac: /Users/haraldweiss/bin/check-omlx-tunnel.sh
- Create on Mac: /Users/haraldweiss/Library/LaunchAgents/de.haraldweiss.omlx-tunnel-monitor.plist
- Create on Oracle: /etc/systemd/system/ai-provider-omlx-11442.service
- Modify on Oracle: /etc/ai-provider/ai-provider.env

- [ ] **Step 1: Install dedicated Mac agent and monitor**

Clone the existing Ollama lifecycle pattern but use label com.wolfini.omlx-tunnel, logs in ~/Library/Logs, and autossh -R 11442:127.0.0.1:8000. Bootstrap with launchctl bootstrap gui/$(id -u), and recover only via launchctl kickstart -k.

- [ ] **Step 2: Install Oracle bridge**

```ini
[Service]
Environment=BRIDGE_IP=172.17.0.1
ExecStart=/usr/bin/socat TCP-LISTEN:11442,bind=${BRIDGE_IP},reuseaddr,fork TCP:127.0.0.1:11442
Restart=on-failure
RestartSec=10s
User=root
```

Run sudo systemctl daemon-reload and sudo systemctl enable --now ai-provider-omlx-11442.service. Verify the tunnel loopback and bridge listener.

- [ ] **Step 3: Request credential-transfer approval immediately before production env configuration**

With explicit operator approval, write OMLX_BASE_URL and the existing oMLX API key to /etc/ai-provider/ai-provider.env without printing the key or putting it in shell history.

- [ ] **Step 4: Deploy and live-verify**

Fast-forward Oracle source to the commit SHA, build with ./build.sh <sha>, and recreate with sudo docker compose up -d --force-recreate ai-provider. Verify health, oMLX provider health/model listing, a small omlx/Devstral-Small-2-24B-Instruct-2512-4bit request, fallback after a temporary bridge outage, image SHA, and logs free of key material.
