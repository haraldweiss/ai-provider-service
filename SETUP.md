# Setup Guide: ai-provider-service

Complete setup instructions for the centralized AI provider service with on-demand model loading.

## Quick Start

### Prerequisites
- **Python 3.9+**
- **Ollama** (optional, for local model inference)
- **pip** or **pipenv**

### Installation

```bash
# Clone repository
git clone <repo-url>
cd ai-provider-service

# Install dependencies
pip install -r requirements.txt

# Create .env file
cp .env.example .env
# Edit .env with your configuration (see below)

# Initialize database
python -c "from app import create_app; from database import db; app = create_app(); db.create_all()"

# Run service
python app.py
```

Service will start on `http://localhost:8767` by default.

### Verify Service
```bash
curl http://localhost:8767/health
```

---

## Configuration

### Environment Variables

All configuration uses environment variables. Set them in `.env` or pass directly.

#### Core Requirements
| Variable | Description | Example |
|----------|-------------|---------|
| `MASTER_KEY` | 32-byte encryption key for configs | `openssl rand -hex 16` |
| `SERVICE_TOKEN` | Bearer token for API authentication | Any secure string |
| `HOST` | Server bind address | `127.0.0.1` |
| `PORT` | Server port | `8767` |

#### Provider Configuration
| Variable | Description | Optional |
|----------|-------------|----------|
| `OLLAMA_URL` | Ollama server URL | Yes, default: `http://127.0.0.1:11434` |
| `ANTHROPIC_API_KEY` | Claude API key | Yes (required for Claude provider) |
| `OPENAI_API_KEY` | OpenAI API key | Yes (required for OpenAI provider) |

#### Startup Control
| Variable | Values | Default |
|----------|--------|---------|
| `STARTUP_MODE` | `lazy`, `eager`, `minimal` | `lazy` |

#### Queue & Health
| Variable | Description | Default |
|----------|-------------|---------|
| `QUEUE_TTL_HOURS` | Queue item expiration | `24` |
| `HEALTH_CHECK_INTERVAL_SEC` | Provider health poll interval | `30` |
| `QUEUE_DRAIN_INTERVAL_SEC` | Queue processing interval | `60` |

#### Database
| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | SQLAlchemy database URI | `sqlite:///storage.db` |

### Example `.env` File

```bash
# Core
MASTER_KEY=a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4
SERVICE_TOKEN=my-secure-service-token-here
HOST=127.0.0.1
PORT=8767

# Providers
OLLAMA_URL=http://127.0.0.1:11434
ANTHROPIC_API_KEY=sk-ant-...

# Startup
STARTUP_MODE=lazy

# Queue
QUEUE_TTL_HOURS=24
HEALTH_CHECK_INTERVAL_SEC=30
QUEUE_DRAIN_INTERVAL_SEC=60

# Database (optional)
DATABASE_URL=sqlite:///storage.db
```

---

## Startup Modes

Choose a startup mode via `STARTUP_MODE` environment variable.

### `STARTUP_MODE=lazy` (Default)

**Behavior:** Providers initialize on first request

**Startup time:** ~1 second  
**Memory overhead:** Minimal  
**Use case:** Development, single-user, resource-constrained environments

**Logs:**
```
Lazy initialization enabled (providers load on first use)
...
[Provider lazy-initialized] ollama
```

### `STARTUP_MODE=eager`

**Behavior:** All providers initialized at startup

**Startup time:** ~5-10 seconds (depends on provider availability)  
**Memory overhead:** All provider clients loaded  
**Use case:** Production, multi-user, warm-up before traffic

**Logs:**
```
Initializing all providers (eager mode)
Claude provider initialized
Ollama provider initialized (healthy)
OpenAI provider available
```

### `STARTUP_MODE=minimal`

**Behavior:** Only Claude provider initialized; others lazy

**Startup time:** ~2 seconds  
**Memory overhead:** Light  
**Use case:** Primarily Anthropic users, fast startup with fallback

**Logs:**
```
Claude provider initialized
Lazy initialization enabled for others
```

---

## Model Lifecycle Management

The service automatically manages Ollama model loading and memory.

### Auto-Loading (Transparent)

When you call `/chat` with provider=`ollama`:
1. ModelManager checks if model is loaded
2. If not loaded, unloads LRU models to free space
3. Executes `ollama pull <model>`
4. Tracks in database with timestamps

```bash
# Model auto-loads on first request
curl -X POST http://localhost:8767/chat \
  -H "Authorization: Bearer $SERVICE_TOKEN" \
  -d '{
    "provider": "ollama",
    "model": "mistral:7b",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

### Explicit Loading/Unloading

Pre-warm models or free memory manually:

```bash
# Pre-load model (useful before traffic spike)
curl -X POST http://localhost:8767/models/load \
  -H "Authorization: Bearer $SERVICE_TOKEN" \
  -d '{"model_name": "mistral:7b"}'

# Explicit unload to free VRAM
curl -X POST http://localhost:8767/models/unload \
  -H "Authorization: Bearer $SERVICE_TOKEN" \
  -d '{"model_name": "mistral:7b"}'

# Clear all loaded models
curl -X POST http://localhost:8767/models/unload-all \
  -H "Authorization: Bearer $SERVICE_TOKEN" \
  -d '{}'
```

### Check Status

```bash
curl http://localhost:8767/models/status \
  -H "Authorization: Bearer $SERVICE_TOKEN"
```

Response:
```json
{
  "loaded": ["mistral:7b", "llama2:7b"],
  "count": 2,
  "total_size_gb": 14.0,
  "hardware": {
    "gpu_vram_mb": 24000,
    "system_ram_mb": 64000,
    "cpu_cores": 8,
    "has_gpu": true,
    "gpu_type": "nvidia"
  },
  "utilization_pct": 58.3
}
```

---

## Client Library Setup

### For Python Clients

**Installation:**
```bash
pip install -r requirements.txt
# (or from PyPI when published)
```

**Set auth token:**
```bash
export SERVICE_TOKEN=your-token-here
```

**Usage:**
```python
from client_library import AIProviderClient

client = AIProviderClient(service_url='http://localhost:8767')

# Chat with auto-loading
response = client.chat(
    messages=[{"role": "user", "content": "What is Python?"}],
    provider="ollama",
    model="mistral:7b"
)
print(response['result']['content'][0]['text'])

# Pre-load model
client.load_model('llama2:7b')

# Check status
status = client.get_status()
print(f"Loaded models: {status['loaded']}")

# Unload to free memory
client.unload_model('llama2:7b')
```

See [client_library/README.md](client_library/README.md) for complete API reference.

---

## API Endpoints

### Chat Endpoint

**`POST /chat`** — Send message, auto-loads model if needed

**Headers:** `Authorization: Bearer <TOKEN>`

**Request:**
```json
{
  "user_id": "user-123",
  "provider": "ollama",
  "model": "mistral:7b",
  "messages": [
    {"role": "user", "content": "Hello"}
  ],
  "max_tokens": 600
}
```

**Response (sync):**
```json
{
  "result": {
    "content": [{"text": "Hi there!"}],
    "usage": {"input_tokens": 10, "output_tokens": 15}
  },
  "via": "ollama",
  "fallback_used": false
}
```

### Model Loading Endpoint

**`POST /models/load`** — Explicitly load model

**Request:**
```json
{"model_name": "mistral:7b", "force": false}
```

**Response:**
```json
{"loaded": true, "model_name": "mistral:7b"}
```

### Model Unloading Endpoint

**`POST /models/unload`** — Unload model

**Request:**
```json
{"model_name": "mistral:7b"}
```

**Response:**
```json
{"unloaded": true, "model_name": "mistral:7b"}
```

### Status Endpoint

**`GET /models/status`** — Check loaded models and hardware

**Response:**
```json
{
  "loaded": ["mistral:7b"],
  "count": 1,
  "total_size_gb": 7.0,
  "hardware": {...},
  "utilization_pct": 29.2
}
```

### Unload All Endpoint

**`POST /models/unload-all`** — Clear all loaded models

**Response:**
```json
{"unloaded_count": 2, "models": [...]}
```

---

## Testing

### Run Unit Tests

```bash
# All tests
pytest tests/ -v

# Specific module
pytest tests/providers/test_model_manager.py -v

# With coverage
pytest tests/ --cov=providers --cov=api --cov-report=html
```

### Run Integration Tests

Integration tests assume service is running:

```bash
# Start service in one terminal
python app.py

# In another terminal
pytest tests/api/ -v -m integration
```

---

## Troubleshooting

### Ollama Connection Issues

**Symptom:** "Connection refused" or "Network error"

**Check:**
```bash
# Verify Ollama is running
curl http://127.0.0.1:11434/api/tags

# Check OLLAMA_URL setting
echo $OLLAMA_URL
```

### VRAM Exhaustion

**Symptom:** Models fail to load, "insufficient VRAM" error

**Solution:**
- Unload largest models: `POST /models/unload` 
- Clear all: `POST /models/unload-all`
- Use smaller models (7B instead of 70B)
- Increase system RAM or use GPU with more VRAM

### Model Not Found

**Symptom:** "Model not in registry" or pull fails

**Solution:**
```bash
# Check available models
curl http://localhost:8767/models/status

# Manually pull model (if Ollama installed)
ollama pull mistral:7b
```

### Authentication Failures

**Symptom:** 401 Unauthorized on endpoints

**Check:**
```bash
# Verify SERVICE_TOKEN is set
echo $SERVICE_TOKEN

# Include in request headers
-H "Authorization: Bearer $SERVICE_TOKEN"
```

---

## Production Deployment

### Docker

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV STARTUP_MODE=eager
CMD ["gunicorn", "-b", "0.0.0.0:8767", "--workers", "4", "app:create_app()"]
```

### Environment Setup

For production, use environment variables from a secrets manager:

```bash
# Generate secure keys
MASTER_KEY=$(openssl rand -hex 16)
SERVICE_TOKEN=$(openssl rand -hex 32)

# Store in secrets manager (e.g., AWS Secrets Manager, Vault)
# Then set as environment variables at runtime
```

### Database

Use PostgreSQL in production instead of SQLite:

```bash
DATABASE_URL=postgresql://user:pass@localhost/ai_provider_service

# Create database
createdb ai_provider_service

# Run migrations (if any)
flask db upgrade
```

### Monitoring

Monitor these metrics:

- **VRAM utilization:** Check `/models/status` endpoint
- **Queue depth:** `GET /queue?user_id=<id>`
- **Provider health:** `GET /providers/<id>/health`
- **Error rate:** Check logs for 4xx/5xx responses

---

## Advanced Configuration

### Custom Provider Endpoint

```bash
# In .env
CUSTOM_API_ENDPOINT=https://my-llm-api.example.com
CUSTOM_API_KEY=my-key
```

### Multi-Instance Setup (HA)

For multiple service instances, use shared PostgreSQL and Redis:

```bash
DATABASE_URL=postgresql://shared-db.example.com/ai_provider
# Shared DB tracks OllamaLoadedModels across instances

# Optional: Use Redis for distributed state
REDIS_URL=redis://shared-redis.example.com:6379
```

---

## Next Steps

- See [client_library/README.md](client_library/README.md) for client library usage
- See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for technical deep-dive
- Check [README.md](README.md) for provider details and deployment options
