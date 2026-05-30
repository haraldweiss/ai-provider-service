# AIProviderClient — Python SDK

Lightweight client library for connecting to a remote ai-provider-service instance. Minimal dependencies (only `requests`).

## Installation

### From Source
```bash
# Copy client_library/ to your project
pip install -r ../requirements.txt  # Gets requests
```

### From Package (Future)
```bash
pip install ai-provider-service[client]
```

## Quick Start

### Setup

Set your service token as an environment variable:
```bash
export SERVICE_TOKEN="your-service-token-here"
```

### Basic Usage

```python
from client_library import AIProviderClient

# Connect to service
client = AIProviderClient(service_url='http://localhost:8767')

# Send chat request (model auto-loads)
response = client.chat(
    messages=[{"role": "user", "content": "What is Python?"}],
    provider="ollama",
    model="mistral:7b"
)

# Extract response
answer = response['result']['content'][0]['text']
print(answer)
```

---

## API Reference

### `AIProviderClient(service_url, token=None)`

Initialize client for remote service.

**Parameters:**
- `service_url` (str): Base URL of ai-provider-service. Default: `http://localhost:8767`
- `token` (str): Auth token. Default: reads `SERVICE_TOKEN` env var

**Raises:**
- `ValueError`: If no token provided and `SERVICE_TOKEN` env var not set

**Example:**
```python
import os
from client_library import AIProviderClient

# Auto-detect token from environment
client = AIProviderClient()

# Or explicit token
client = AIProviderClient(
    service_url='http://api.example.com:8767',
    token='sk-your-token'
)
```

---

### `chat(messages, provider, model, max_tokens, user_id)`

Send a chat request. Model auto-loads if not already loaded.

**Parameters:**
- `messages` (list): Message history with `role` and `content`
- `provider` (str): Provider ID (default: `"ollama"`)
- `model` (str): Model name (default: `"mistral:7b"`)
- `max_tokens` (int): Max output tokens (default: `600`)
- `user_id` (str): User identifier (default: `"anonymous"`)

**Returns:** Response dict with structure:
```python
{
    "result": {
        "content": [{"text": "..."}],
        "usage": {"input_tokens": 10, "output_tokens": 5}
    },
    "via": "ollama",
    "fallback_used": false
}
```

**Raises:**
- `requests.RequestException`: If service unavailable or request fails

**Example:**
```python
response = client.chat(
    messages=[
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi!"},
        {"role": "user", "content": "How are you?"}
    ],
    model="llama2:7b",
    max_tokens=200
)

text = response['result']['content'][0]['text']
print(f"Assistant: {text}")
```

---

### `load_model(model_name, force=False)`

Explicitly load a model into Ollama memory. Useful for pre-warming before requests.

**Parameters:**
- `model_name` (str): Model name (e.g., `"mistral:7b"`)
- `force` (bool): Force reload even if already loaded (default: `False`)

**Returns:** Dict with `{"loaded": bool, "model_name": str, "status": str}`

**Raises:**
- `requests.RequestException`: If service call fails

**Example:**
```python
# Pre-warm model before traffic spike
result = client.load_model('mistral:7b')
if result['loaded']:
    print(f"Model ready: {result['model_name']}")
else:
    print("Failed to load model")

# Force reload
client.load_model('mistral:7b', force=True)
```

---

### `unload_model(model_name)`

Explicitly unload a model to free VRAM.

**Parameters:**
- `model_name` (str): Model name to unload

**Returns:** Dict with `{"unloaded": bool, "model_name": str, "status": str}`

**Raises:**
- `requests.RequestException`: If service call fails

**Example:**
```python
# Free up VRAM
result = client.unload_model('llama2:70b')
if result['unloaded']:
    print("Model unloaded, VRAM freed")
else:
    print("Model not found or already unloaded")
```

---

### `get_status()`

Check which models are currently loaded and hardware capacity.

**Returns:** Status dict with:
- `loaded` (list): Model names currently in memory
- `count` (int): Number of loaded models
- `total_size_gb` (float): Combined size of loaded models
- `hardware` (dict): GPU VRAM, system RAM, CPU cores, GPU type
- `utilization_pct` (float): VRAM usage percentage

**Raises:**
- `requests.RequestException`: If service call fails

**Example:**
```python
status = client.get_status()

print(f"Loaded: {status['loaded']}")
print(f"VRAM usage: {status['utilization_pct']}%")
print(f"GPU: {status['hardware']['gpu_type']}")

if status['utilization_pct'] > 80:
    print("⚠️  High VRAM usage!")
```

---

### `unload_all()`

Clear all loaded models to reclaim VRAM.

**Returns:** Dict with `{"unloaded_count": int, "models": list, "status": str}`

**Raises:**
- `requests.RequestException`: If service call fails

**Example:**
```python
# Clean slate before heavy operation
result = client.unload_all()
print(f"Unloaded {result['unloaded_count']} models")
```

---

### `list_loadable_models(use_case=None, max_size_gb=None)`

Query models that could be loaded based on hardware constraints.

**Parameters:**
- `use_case` (str, optional): Filter by use case ("chat", "reasoning", "vision", "embedding")
- `max_size_gb` (float, optional): Max model size in GB

**Returns:** Dict with `{"models": ["model1", "model2", ...]}`

**Raises:**
- `requests.RequestException`: If service call fails

**Example:**
```python
# Get vision models that fit GPU
vision_models = client.list_loadable_models(
    use_case='vision',
    max_size_gb=16.0
)

# Get reasoning models (no size limit)
reasoning = client.list_loadable_models(use_case='reasoning')
```

---

## Error Handling

### Connection Errors

```python
import requests

try:
    response = client.chat(
        messages=[{"role": "user", "content": "Hi"}],
        model="mistral:7b"
    )
except requests.ConnectionError:
    print("❌ Service unreachable. Is it running?")
except requests.Timeout:
    print("❌ Request timed out. Service is slow.")
except requests.RequestException as e:
    print(f"❌ Request failed: {e}")
```

### Authentication Errors

```python
try:
    response = client.get_status()
except requests.HTTPError as e:
    if e.response.status_code == 401:
        print("❌ Invalid SERVICE_TOKEN")
    else:
        print(f"❌ HTTP error: {e}")
```

### Model Errors

```python
try:
    response = client.chat(model="nonexistent:model")
except requests.HTTPError as e:
    if e.response.status_code == 400:
        print("❌ Model not found or invalid request")
    else:
        print(f"❌ Error: {e}")
```

---

## Examples

### Multi-Turn Conversation

```python
from client_library import AIProviderClient

client = AIProviderClient()
messages = []

while True:
    user_input = input("You: ")
    messages.append({"role": "user", "content": user_input})
    
    response = client.chat(messages=messages, model="mistral:7b")
    assistant_text = response['result']['content'][0]['text']
    messages.append({"role": "assistant", "content": assistant_text})
    
    print(f"Assistant: {assistant_text}\n")
```

### Compare Models

```python
query = "Explain quantum computing in one sentence"
models = ["mistral:7b", "llama2:7b", "neural-chat:7b"]

status = client.get_status()
print(f"Loaded: {status['loaded']}")

for model in models:
    print(f"\n--- {model} ---")
    response = client.chat(
        messages=[{"role": "user", "content": query}],
        model=model,
        max_tokens=100
    )
    print(response['result']['content'][0]['text'])

print(f"\nFinal VRAM usage: {client.get_status()['utilization_pct']}%")
```

### Model Warmup & Cleanup

```python
# Pre-load models before heavy workload
print("🔥 Warming up models...")
client.load_model('mistral:7b')
client.load_model('llama2:7b')

status = client.get_status()
print(f"Ready: {status['loaded']}")
print(f"VRAM usage: {status['utilization_pct']}%\n")

# Do work...
response = client.chat(
    messages=[{"role": "user", "content": "Hello"}],
    model="mistral:7b"
)
print(response['result']['content'][0]['text'])

# Cleanup
print("\n🧹 Cleaning up...")
client.unload_all()
print(f"Final VRAM usage: {client.get_status()['utilization_pct']}%")
```

### Hardwar-Aware Model Selection

```python
# Choose model based on available hardware
status = client.get_status()
available_vram = status['hardware']['gpu_vram_mb']

if available_vram >= 24000:
    model = "mistral:7b"  # ~7 GB
elif available_vram >= 16000:
    model = "mistral:tiny"  # ~4 GB
else:
    model = "phi:latest"  # ~3 GB

print(f"Using {model} ({available_vram}MB VRAM available)")
response = client.chat(
    messages=[{"role": "user", "content": "What's the weather?"}],
    model=model
)
```

---

## Configuration

### Service URL

```python
# Local development
client = AIProviderClient(service_url='http://localhost:8767')

# Remote server
client = AIProviderClient(service_url='https://api.example.com')

# Custom port
client = AIProviderClient(service_url='http://192.168.1.100:9000')
```

### Authentication

```python
# From environment variable (recommended)
import os
os.environ['SERVICE_TOKEN'] = 'my-token'
client = AIProviderClient()

# Direct passing
client = AIProviderClient(token='my-token')

# Check token
print(client.token)  # (not recommended for security)
```

---

## Troubleshooting

### Service Unavailable

```
requests.exceptions.ConnectionError: HTTPConnectionPool(...): Max retries exceeded
```

**Solution:**
- Ensure ai-provider-service is running
- Check `service_url` is correct
- Verify network connectivity

### Authentication Failed

```
requests.exceptions.HTTPError: 401 Client Error: Unauthorized
```

**Solution:**
- Set `SERVICE_TOKEN` env var: `export SERVICE_TOKEN=<token>`
- Verify token matches service's `SERVICE_TOKEN`

### Model Not Found

```
requests.exceptions.HTTPError: 400 Client Error: Bad Request
```

**Solution:**
- Check model name (e.g., `mistral:7b`, not `Mistral 7B`)
- Verify model is available: `ollama pull mistral:7b`
- Check with `list_loadable_models()`

### VRAM Exhausted

```
"error": "Failed to load model; insufficient VRAM or model not found"
```

**Solution:**
```python
# Check utilization
status = client.get_status()
if status['utilization_pct'] > 85:
    # Free space
    client.unload_all()
    # Try again with smaller model
```

---

## API Stability

This client library is stable and follows semantic versioning. Breaking changes will bump the major version.

Current version: **0.1.0**

---

## Support

- 📖 See [SETUP.md](../SETUP.md) for service configuration
- 🏗️ See [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) for technical details
- 🐛 Report issues on GitHub
