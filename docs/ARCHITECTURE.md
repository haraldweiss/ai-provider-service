# Architecture: Centralized AI Provider Service

Technical deep-dive into the system design, components, and data flow.

## System Overview

The ai-provider-service is a centralized hub that manages LLM access across multiple providers with intelligent model lifecycle management.

```
┌─────────────────────────────────────────────────────────────┐
│              ai-provider-service (Central Hub)              │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ REST API Layer (Flask)                              │  │
│  │ ├─ /chat (send messages, auto-load models)         │  │
│  │ ├─ /models/load (explicit load)                    │  │
│  │ ├─ /models/unload (explicit unload)                │  │
│  │ ├─ /models/status (hardware + loaded models)       │  │
│  │ └─ /models/unload-all (clear all)                  │  │
│  └─────────────────────────────────────────────────────┘  │
│                         ↓                                  │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ Request Dispatcher                                  │  │
│  │ ├─ Route to primary provider                        │  │
│  │ ├─ Fallback to secondary provider                  │  │
│  │ └─ Queue if all fail                               │  │
│  └─────────────────────────────────────────────────────┘  │
│                         ↓                                  │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ Model Lifecycle Manager                             │  │
│  │ ├─ Load model into Ollama                           │  │
│  │ ├─ Track loaded models (DB)                         │  │
│  │ ├─ Unload LRU on VRAM pressure                      │  │
│  │ └─ Hardware detection & constraints                 │  │
│  └─────────────────────────────────────────────────────┘  │
│                         ↓                                  │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ Lazy Init Component System                          │  │
│  │ ├─ OllamaClient (on first ollama request)          │  │
│  │ ├─ ClaudeClient (on first claude request)          │  │
│  │ ├─ OpenAIClient (on first openai request)          │  │
│  │ └─ Thread-safe double-check locking                │  │
│  └─────────────────────────────────────────────────────┘  │
│                         ↓                                  │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ Provider Clients & Database                         │  │
│  │ ├─ Ollama (local LLM via API)                      │  │
│  │ ├─ Claude (Anthropic API)                          │  │
│  │ ├─ OpenAI (ChatGPT API)                            │  │
│  │ ├─ Mammouth (custom endpoint)                      │  │
│  │ ├─ Custom (OpenAI-compatible)                      │  │
│  │ └─ SQLite/PostgreSQL                               │  │
│  └─────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
         ↑        ↑         ↑          ↑
    ┌────┴────┐ ┌─┴──────┐ ┌┴────┐ ┌──┴─────┐
    │ Repo A  │ │ Repo B │ │ CLI │ │  Web  │
    │ (Python)│ │(NodeJS)│ │Tool │ │ App   │
    └────────┘ └────────┘ └────┘ └───────┘
   (lightweight client library)
```

---

## Core Components

### 1. REST API Layer (`api/`)

**Flask Blueprints** that expose HTTP endpoints.

**Files:**
- `chat_api.py` — `/chat` for message requests
- `models_api.py` — `/models/*` for model management
- `configs_api.py` — `/configs/*` for provider configuration
- `providers_api.py` — `/providers/*` for provider listing
- `health_api.py` — `/health` for health checks
- `queue_api.py` — `/queue/*` for async queue management
- `auth.py` — Bearer token authentication

**Request Flow:**
```
POST /chat
  ↓
[require_token] — Check Authorization header
  ↓
[chat()] — Extract user_id, provider, model, messages
  ↓
[ModelManager.load_model()] — Load if Ollama (auto-lock LRU)
  ↓
[dispatcher.dispatch()] — Route to provider
  ↓
[response] — Return content + usage + metadata
```

### 2. Request Dispatcher (`dispatcher.py`)

Routes requests through provider cascade: primary → fallback → queue.

**Algorithm:**
```python
def dispatch(user_id, provider_id, model, messages, max_tokens):
    # Try primary provider
    try:
        client = get_client(provider_id)
        return client.create_message(model, messages, max_tokens)
    except Exception:
        pass
    
    # Try fallback provider
    try:
        fallback = get_fallback_provider(user_id, provider_id)
        if fallback:
            client = get_client(fallback)
            return client.create_message(model, messages, max_tokens)
    except Exception:
        pass
    
    # Queue the request
    if should_queue(user_id, provider_id):
        queue_request(user_id, provider_id, model, messages, max_tokens)
        return {"queued": True, "queue_id": "..."}
    
    # All failed
    raise Error("No available provider")
```

### 3. Model Lifecycle Manager (`providers/model_manager.py`)

Manages loading, unloading, and tracking of Ollama models.

**Key Operations:**

#### Load Model
```python
def load_model(model_name: str, force: bool = False) -> bool:
    # 1. Check if already loaded (DB)
    #    → Yes: update last_used, return True
    # 2. Get model metadata (OllamaModelRegistry)
    # 3. Check hardware constraints
    #    available_vram = GPU_VRAM or (SYSTEM_RAM / 2)
    #    needed_vram = model_size_gb * 1024 * 1.1  (10% overhead)
    # 4. While needed_vram > available_vram:
    #    → _try_unload_lru() — evict least-recently-used
    # 5. Execute: ollama pull <model_name>
    # 6. Record in OllamaLoadedModels DB with timestamps
    # 7. Return True if success
```

#### Unload Model (Strategy: Option B)
```python
def unload_model(model_name: str) -> bool:
    # 1. Find in OllamaLoadedModels
    # 2. Remove from DB (mark as unloaded)
    # 3. Model stays in Ollama cache but is "available to evict"
    # 4. Ollama's own memory management will evict if needed
    #
    # Why Option B (DB-only) vs Option A (restart):
    # - Pro: No downtime, no restart needed
    # - Con: Less aggressive control over VRAM
    # - Fallback: Can escalate to subprocess restart if pressure
```

**Database Tables:**
- `OllamaLoadedModels` — Currently loaded models
  - `model_name` (unique)
  - `size_gb`
  - `loaded_at` (timestamp)
  - `last_used` (timestamp, updated on each use)
- `OllamaModelRegistry` — Discovered models (daily sync from ollama.com)
  - `model_name`
  - `size_gb`
  - `use_case` ("chat", "reasoning", "vision", "embedding")
  - `is_multimodal`
  - `min_vram_mb`, `min_ram_mb` (hardware requirements)
  - `is_loaded` (sync state)

### 4. Hardware Detection (`providers/hardware.py`)

Cross-platform detection of available GPU VRAM, system RAM, and CPU.

**Detection Order:**
```
GPU VRAM:
  1. Try nvidia-smi (NVIDIA GPUs)
  2. Try rocm-smi (AMD GPUs)
  3. Try Metal (Apple Silicon)
  4. Return None

System RAM:
  1. Use psutil.virtual_memory()
  2. Fallback: /proc/meminfo (Linux)
  3. Fallback: sysctl (macOS)

CPU Count:
  1. os.cpu_count()
  2. Default: 4
```

**Caching:**
- Results cached for 5 minutes
- `clear_hardware_cache()` forces re-detection
- Used by ModelManager to check constraints

### 5. Lazy Initialization (`providers/lazy_init.py`)

Thread-safe component initialization on first use.

**Pattern: Double-Check Locking**
```python
@lazy_init('ollama')
def initialize_ollama():
    # Called only once, even with concurrent requests
    
    # Thread 1: Check if initialized (fast path)
    if 'ollama' in _initialized:
        return
    
    # Thread 2: Get or create per-component lock
    with _init_locks.setdefault('ollama', Lock()):
        # Thread 3: Check again inside lock (safe)
        if 'ollama' not in _initialized:
            # Do initialization (only this thread)
            logger.info("Initializing Ollama...")
            mark_initialized('ollama')
```

**Benefits:**
- Fast path: Only acquire lock if not initialized
- Safe: Lock prevents race conditions
- Observable: `is_initialized()`, `get_initialized()` for introspection

### 6. Provider Registry (`providers/__init__.py`)

Central factory for creating provider clients.

**Provider Availability Tracking:**
```python
PROVIDER_REGISTRY = {
    'ollama': {
        'name': 'Ollama (local)',
        'system': True,
        'requires': [],
        'available': True,  # Always available (local)
    },
    'claude': {
        'name': 'Claude (Anthropic)',
        'system': True,
        'requires': [],
        'available': HAS_ANTHROPIC,  # Depends on import
    },
    'openai': {
        'name': 'ChatGPT / OpenAI',
        'system': False,
        'requires': ['api_key'],
        'available': HAS_OPENAI,
    },
    'mammouth': {
        'name': 'Mammouth',
        'system': False,
        'requires': ['api_endpoint'],
        'available': True,  # HTTP-based
    },
    'custom': {
        'name': 'Custom OpenAI-compatible',
        'system': False,
        'requires': ['api_endpoint'],
        'available': True,
    },
}

def get_client(provider_id, config):
    # Check availability (soft-fail on missing deps)
    if not PROVIDER_REGISTRY[provider_id].get('available'):
        raise ImportError(f"Install: pip install <provider-package>")
    
    # Lazy-init the provider
    # Then return client instance
```

### 7. Worker (`worker.py`)

Background tasks running in separate thread.

**Tasks:**
- **Queue drainer** — Process pending requests (every 60s)
- **Health checker** — Poll provider endpoints (every 30s)
- **Model sync** — Daily sync from ollama.com/search (every 24h)

**Architecture:**
```python
# Main thread
app = create_app()

# Spawns worker thread
worker.start(app)

# Worker runs in loop
while True:
    _tick()  # Check scheduled tasks
    time.sleep(1)

def _tick():
    # Every 60s: drain queue
    if time.time() - _state['last_queue_drain'] >= 60:
        queue_drain_worker()
    
    # Every 30s: health check
    if time.time() - _state['last_health_check'] >= 30:
        health_check_worker()
    
    # Every 86400s (24h): sync models
    if time.time() - _state['last_model_sync'] >= 86400:
        sync_ollama_models(app)
```

---

## Request Flow Example

### Chat Request with Auto-Loading

```
1. Client sends:
   POST /chat
   {
     "user_id": "user1",
     "provider": "ollama",
     "model": "mistral:7b",
     "messages": [{"role": "user", "content": "Hello"}]
   }

2. API Layer (chat_api.py):
   - require_token validates Authorization header
   - Extract body: user_id, provider, model, messages, max_tokens
   - Check required fields
   - Validate provider exists

3. Model Manager (auto-load if Ollama):
   if provider == 'ollama':
     - Check: OllamaLoadedModels.query.filter_by('mistral:7b')
     - Already loaded? → update last_used timestamp, continue
     - Not loaded? → get hardware profile
       - available_vram = GPU_VRAM or (SYSTEM_RAM / 2)
       - needed_vram = 7.0 * 1024 * 1.1 = 7,872 MB
       - If needed_vram > available_vram:
         - _try_unload_lru() → find oldest last_used model
         - unload_model(lru_model) → remove from DB
         - (Ollama will evict from cache over time)
       - subprocess.run(['ollama', 'pull', 'mistral:7b'])
         - Download if not local
         - Takes 5-10 min for first pull
       - Record in DB: OllamaLoadedModels(model='mistral:7b', size_gb=7.0, loaded_at=now, last_used=now)

4. Dispatcher (dispatcher.py):
   - Get config for user1 + ollama provider
   - Call: get_client('ollama', config)
     - Lazy-init: @lazy_init('ollama') on OllamaClient
       - If not initialized: create client, check health
     - Return client instance
   - Call: client.create_message('mistral:7b', messages, 600)
     - Compute num_ctx based on message length
     - POST to http://127.0.0.1:11434/api/chat
     - Return: {"message": {"content": "..."}, "eval_count": 25, ...}

5. Response Assembly (chat_api.py):
   - Extract: content, input_tokens, output_tokens
   - Format: {"result": {...}, "via": "ollama", "fallback_used": false}
   - Return 200 OK

6. Update timestamps:
   - OllamaLoadedModels.last_used = now (tracked for LRU)
   - RequestQueue entry (if queued) → status = "done"

Total time:
- First request (cold): 5-15 minutes (model pull)
- Subsequent requests: 100-500ms (depends on model)
```

---

## Startup Modes

### Lazy Mode (Default)
```python
# STARTUP_MODE=lazy

if STARTUP_MODE == 'lazy':
    logger.info("Lazy initialization enabled")
    # That's it. Providers init on first request.

# First request to /chat with provider=ollama:
@lazy_init('ollama')
def initialize_ollama():
    client = OllamaClient()
    if client.health():
        mark_initialized('ollama')
```

### Eager Mode
```python
# STARTUP_MODE=eager

if STARTUP_MODE == 'eager':
    _init_all_providers()
    # Initialize Claude, Ollama, OpenAI, Mammouth, Custom

def _init_all_providers():
    _init_claude()   # Load Anthropic SDK, check key
    _init_ollama()   # Create client, check health
    _init_openai()   # Check HAS_OPENAI flag
    # ... etc
```

### Minimal Mode
```python
# STARTUP_MODE=minimal

if STARTUP_MODE == 'minimal':
    _init_claude()  # Only Anthropic
    # Others lazy
```

---

## Data Model

### Tables

#### ProviderConfig
```
user_id (indexed) | provider_id | config_encrypted (Fernet) | fallback_provider | queue_when_unavailable | ...
─────────────────────────────────────────────────────────────────────────────────────────────────────────
user1            | ollama      | [encrypted JSON]          | claude            | true                  | ...
user1            | claude      | [encrypted JSON]          | NULL              | false                 | ...
user2            | openai      | [encrypted JSON]          | ollama            | true                  | ...
```

**Encrypted config_encrypted** contains:
```json
{
  "api_key": "sk-...",
  "api_endpoint": "https://...",
  "organization_id": "org-...",
  "name": "Custom name"
}
```

#### OllamaLoadedModels
```
model_name (indexed) | size_gb | loaded_at             | last_used             | ...
────────────────────────────────────────────────────────────────────────────────
mistral:7b          | 7.0     | 2024-01-15T10:00:00Z  | 2024-01-15T10:45:32Z  | ...
llama2:7b           | 7.0     | 2024-01-15T08:30:00Z  | 2024-01-15T09:15:00Z  | ...
neural-chat:7b      | 7.0     | 2024-01-14T16:20:00Z  | 2024-01-14T16:20:00Z  | ...
```

**LRU Eviction:** If VRAM full, unload the model with oldest `last_used`

#### OllamaModelRegistry
```
model_name (unique, indexed) | size_gb | use_case | is_multimodal | min_vram_mb | is_loaded | ...
──────────────────────────────────────────────────────────────────────────────────────────────────
mistral:7b                  | 7.0     | chat     | false         | 8192        | true      | ...
llama2:70b                  | 70.0    | chat     | false         | 78000       | false     | ...
llava:7b                    | 7.0     | vision   | true          | 10240       | false     | ...
```

**Synced daily** from ollama.com/search. Infers size, use_case, multimodal status, hardware requirements.

#### RequestQueue
```
id (UUID)                           | user_id | primary_provider | status    | attempts | last_error    | result | ...
───────────────────────────────────────────────────────────────────────────────────────────────────────────────────
a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c | user1   | ollama          | pending   | 0        | NULL          | NULL   | ...
f7e8d9c0-b1a2-3f4e-5d6c-7a8b9c0d1e | user2   | claude          | done      | 1        | NULL          | {...}  | ...
```

---

## Error Handling

### Provider Unavailable
```
User requests Claude but anthropic not installed
→ HAS_ANTHROPIC = False
→ get_client('claude') raises ImportError
→ Dispatcher catches, tries fallback
→ If no fallback, queues request
```

### Model Loading Fails
```
ModelManager.load_model('mistral:7b')
→ subprocess.run(['ollama', 'pull', ...]) returns non-zero
→ load_model() returns False
→ chat_api.py returns 400 with error message
→ Client retries or switches model
```

### VRAM Exhaustion
```
While loading model, available_vram < needed_vram:
→ _try_unload_lru() repeatedly called
→ If all models unloaded and still not enough:
  → load_model() returns False
  → Error: "Insufficient VRAM"
  → Consider larger GPU or smaller models
```

---

## Security

### Token Authentication
All API endpoints require:
```
Authorization: Bearer <SERVICE_TOKEN>
```

Validated in `@require_token` decorator.

### Config Encryption
User provider configs encrypted with Fernet (symmetric, AES-128):
```python
from cryptography.fernet import Fernet

cipher = Fernet(MASTER_KEY)
encrypted = cipher.encrypt(json_config.encode())
# Stored in DB

decrypted = cipher.decrypt(encrypted).decode()
config = json.loads(decrypted)
```

**MASTER_KEY** is 32-byte hex string, stored in environment, never logged.

### API Key Safety
- Never logged
- Never returned in safe_dict()
- Encrypted at rest in DB
- Only decrypted when creating provider client

---

## Performance Characteristics

### Latencies (Typical)

| Operation | Time | Notes |
|-----------|------|-------|
| /health check | 10ms | Simple ping |
| /chat (cold model) | 5-15 min | First-time pull |
| /chat (warm model) | 100-500ms | Depends on model size |
| /models/load (explicit) | 30s-5min | Depends on model & network |
| /models/unload | <10ms | DB delete |
| /models/status | <5ms | DB queries |

### Throughput

- **Single model:** 2-10 req/s (depends on model, hardware)
- **Multiple models:** Limited by VRAM
  - 24GB GPU: ~3 models (7B each)
  - With system RAM: More if spilling

### Memory

- **Service overhead:** ~50-100MB
- **Per model:** 110% of model size (10% overhead)
- **Model switch:** Unloads LRU to fit new model

---

## Monitoring & Debugging

### Logs

```bash
# Enable debug logging
LOG_LEVEL=DEBUG python app.py

# Watch specific module
grep "ModelManager" app.log
grep "Dispatcher" app.log
grep "Lazy-init" app.log
```

### Endpoints for Monitoring

```bash
# Service health
curl http://localhost:8767/health

# Provider health
curl http://localhost:8767/providers/ollama/health

# Model status
curl http://localhost:8767/models/status

# Queue depth
curl http://localhost:8767/queue?user_id=user1
```

### Database Queries

```sql
-- Loaded models
SELECT model_name, size_gb, last_used FROM ollama_loaded_models
ORDER BY last_used DESC;

-- Queue backlog
SELECT COUNT(*) FROM request_queue WHERE status = 'pending';

-- Provider configs
SELECT user_id, provider_id, configured FROM provider_configs;
```

---

## Future Enhancements

1. **Multi-Instance Sync** — Redis/PostgreSQL for shared state across instances
2. **Metrics Export** — Prometheus metrics for Grafana dashboards
3. **Model Versioning** — Support model:tag@version syntax
4. **Batch Processing** — Async batch API for bulk requests
5. **Cost Tracking** — Track VRAM × time, token usage per user
6. **Advanced Scheduling** — Priority queues, scheduled model preloading
7. **Dynamic Fallback** — ML-based provider selection based on latency history
