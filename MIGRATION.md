# Migration Guide: Integrating with Centralized AI Provider Service

This guide helps you migrate your repository from direct AI provider SDK calls (Anthropic, OpenAI) to the centralized **ai-provider-service**, enabling efficient on-demand model loading and shared VRAM management across all services.

## Architecture Overview

### Before: Monolithic (Direct SDK)
```
Your Repo A                 Your Repo B                 Your Repo C
    ↓                           ↓                           ↓
Direct SDK calls        Direct SDK calls            Direct SDK calls
(Anthropic, OpenAI)    (Anthropic, OpenAI)        (Anthropic, OpenAI)
    ↓                           ↓                           ↓
Each loads/unloads  Each loads/unloads      Each loads/unloads
own models + VRAM   own models + VRAM      own models + VRAM
(Duplicate effort, Memory waste)
```

### After: Centralized (Hub-and-Spoke)
```
Your Repo A         Your Repo B         Your Repo C
(lightweight)       (lightweight)       (lightweight)
    ↓                   ↓                   ↓
  AIProviderClient    AIProviderClient    AIProviderClient
          ↓                   ↓                   ↓
    ┌─────────────────────────────────────────┐
    │  Centralized AI Provider Service        │
    │  - Model lifecycle management           │
    │  - Shared VRAM pool with LRU eviction   │
    │  - Multi-provider dispatch              │
    │  - Hardware-aware routing               │
    └─────────────────────────────────────────┘
            ↓
        Ollama, Claude, OpenAI APIs
        (Shared, efficient loading)
```

---

## Benefits

1. **Reduced Memory Footprint:** Only necessary models loaded into VRAM at any given time
2. **Intelligent LRU Eviction:** When VRAM pressure hits, least-recently-used models unload automatically
3. **Minimal Dependencies:** Client repos depend only on `requests`, not heavy SDK libraries
4. **Shared Resource Pool:** All repos benefit from centralized hardware detection and optimization
5. **Fallback Support:** Service can route to alternative providers if primary unavailable
6. **Cost Optimization:** Batch API support for non-urgent requests (50% savings)

---

## Migration Checklist

- [ ] **Step 1:** Understand current AI integration in your repo
- [ ] **Step 2:** Identify all direct SDK imports (Anthropic, OpenAI)
- [ ] **Step 3:** Create response format adapter (if needed)
- [ ] **Step 4:** Replace SDK client with AIProviderClient
- [ ] **Step 5:** Update environment configuration
- [ ] **Step 6:** Test against local ai-provider-service instance
- [ ] **Step 7:** Update documentation and README
- [ ] **Step 8:** Remove direct SDK dependencies from requirements.txt

---

## Step 1: Identify Your Current Integration

Look for patterns like:

### Python Backend
```python
# ❌ Direct SDK usage
from anthropic import Anthropic
from openai import OpenAI

client = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
response = client.messages.create(
    model="claude-3-5-sonnet-20241022",
    messages=[{"role": "user", "content": "..."}]
)
```

### TypeScript/Node.js Frontend
```typescript
// ❌ Direct API calls
const response = await fetch('https://api.anthropic.com/v1/messages', {
  headers: { 'x-api-key': process.env.ANTHROPIC_API_KEY },
  body: JSON.stringify({...})
});
```

---

## Step 2: Before/After Migration Patterns

### Python Backend Migration

**Before:**
```python
# backend/claude_integration.py
from anthropic import Anthropic
import json

class ClaudeService:
    def __init__(self):
        self.client = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
    
    def analyze(self, text: str, model: str = "claude-3-5-sonnet-20241022") -> str:
        response = self.client.messages.create(
            model=model,
            max_tokens=1000,
            messages=[
                {"role": "user", "content": f"Analyze: {text}"}
            ]
        )
        return response.content[0].text

# Usage in routes
claude_service = ClaudeService()
result = claude_service.analyze("some text")
```

**After:**
```python
# backend/claude_integration.py
from client_library import AIProviderClient
import json

class ClaudeService:
    def __init__(self):
        # Read from environment; ai-provider-service runs locally or remotely
        self.client = AIProviderClient(
            service_url=os.getenv('AI_PROVIDER_SERVICE_URL', 'http://localhost:8767'),
            token=os.getenv('AI_PROVIDER_SERVICE_TOKEN')
        )
    
    def analyze(self, text: str, model: str = "claude-3-5-sonnet-20241022") -> str:
        response = self.client.chat(
            provider="claude",
            model=model,
            max_tokens=1000,
            messages=[
                {"role": "user", "content": f"Analyze: {text}"}
            ]
        )
        # Response structure: {"result": {...}, "via": "claude", "fallback_used": false}
        return response['result']['content'][0]['text']

# Usage in routes (no change needed here!)
claude_service = ClaudeService()
result = claude_service.analyze("some text")
```

**Key Differences:**
| Aspect | Before | After |
|--------|--------|-------|
| **Client Init** | `Anthropic(api_key=...)` | `AIProviderClient(service_url=..., token=...)` |
| **Method** | `client.messages.create()` | `client.chat()` |
| **Parameters** | `model=`, `max_tokens=`, `messages=` | Same, plus `provider=` |
| **Response** | Message object with `.content[0].text` | Dict: `response['result']['content'][0]['text']` |
| **Multi-Provider** | ❌ Single provider | ✅ Can fallback to alternative providers |

---

### TypeScript/Node.js Migration

**Before (Direct API):**
```typescript
// lib/claudeClient.ts
export class ClaudeClient {
  private apiKey: string;

  constructor() {
    this.apiKey = process.env.ANTHROPIC_API_KEY!;
  }

  async chat(messages: Array<{role: string; content: string}>): Promise<string> {
    const response = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'x-api-key': this.apiKey,
        'anthropic-version': '2023-06-01',
        'content-type': 'application/json'
      },
      body: JSON.stringify({
        model: 'claude-3-5-sonnet-20241022',
        max_tokens: 1024,
        messages
      })
    });

    const data = await response.json();
    return data.content[0].text;
  }
}
```

**After (Centralized Service):**
```typescript
// lib/aiProviderClient.ts
export class AIProviderClient {
  private serviceUrl: string;
  private token: string;

  constructor(
    serviceUrl?: string,
    token?: string
  ) {
    this.serviceUrl = serviceUrl || process.env.AI_PROVIDER_SERVICE_URL || 'http://localhost:8767';
    this.token = token || process.env.AI_PROVIDER_SERVICE_TOKEN!;
  }

  async chat(options: {
    provider?: string;
    model: string;
    messages: Array<{ role: string; content: string }>;
    max_tokens?: number;
  }): Promise<string> {
    const response = await fetch(`${this.serviceUrl}/chat`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${this.token}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        provider: options.provider || 'claude',
        model: options.model,
        messages: options.messages,
        max_tokens: options.max_tokens || 600
      })
    });

    if (!response.ok) {
      throw new Error(`AI Provider error: ${response.statusText}`);
    }

    const data = await response.json();
    // Response: {"result": {...}, "via": "claude", "fallback_used": false}
    return data.result.content[0].text;
  }

  async getStatus(): Promise<any> {
    const response = await fetch(`${this.serviceUrl}/models/status`, {
      headers: { 'Authorization': `Bearer ${this.token}` }
    });
    return response.json();
  }

  async loadModel(modelName: string): Promise<any> {
    const response = await fetch(`${this.serviceUrl}/models/load`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${this.token}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ model_name: modelName })
    });
    return response.json();
  }
}
```

**Usage remains the same:**
```typescript
const client = new AIProviderClient();
const result = await client.chat({
  model: 'claude-3-5-sonnet-20241022',
  messages: [{ role: 'user', content: 'Hello' }]
});
```

---

## Step 3: Response Format Adapter

The centralized service returns a slightly different response structure. If your code expects the old format, create an adapter:

**Python Adapter:**
```python
class ResponseAdapter:
    """Normalize old SDK response format to new centralized format."""
    
    @staticmethod
    def normalize(centralized_response: dict) -> dict:
        """
        Input (from ai-provider-service):
        {
            "result": {
                "content": [{"text": "..."}],
                "usage": {"input_tokens": 10, "output_tokens": 5}
            },
            "via": "claude",
            "fallback_used": false
        }
        
        Output (mimics old SDK):
        {
            "content": [{"text": "..."}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "provider": "claude",
            "fallback": false
        }
        """
        result = centralized_response.get('result', {})
        return {
            'content': result.get('content', []),
            'usage': result.get('usage', {}),
            'provider': centralized_response.get('via', 'unknown'),
            'fallback': centralized_response.get('fallback_used', False)
        }

# Usage
response = client.chat(...)
normalized = ResponseAdapter.normalize(response)
text = normalized['content'][0]['text']  # Works with old code
```

---

## Step 4: Environment Configuration

### Setup ai-provider-service

**Prerequisite:** Run ai-provider-service on your local machine or central server:

```bash
# In ai-provider-service directory
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Start service (lazy mode = fastest startup)
STARTUP_MODE=lazy python app.py
# Service runs on http://localhost:8767 by default
```

### Configure Your Repo

Create or update `.env`:

```bash
# Remove these (no longer needed):
# ANTHROPIC_API_KEY=sk-ant-...
# OPENAI_API_KEY=sk-...

# Add these (centralized service):
AI_PROVIDER_SERVICE_URL=http://localhost:8767
AI_PROVIDER_SERVICE_TOKEN=<shared-service-token>  # Same token as ai-provider-service

# Optional (if ai-provider-service on remote machine):
# AI_PROVIDER_SERVICE_URL=https://ai-service.example.com:8767
# AI_PROVIDER_SERVICE_TOKEN=<remote-service-token>
```

**Note:** All repos share the same `SERVICE_TOKEN` as the centralized service. If you need per-repo authentication, the service can support an allowlist in the database (advanced feature).

### Update requirements.txt

**Before:**
```
anthropic>=0.39.0
openai>=1.54.0
flask>=3.0.0
requests>=2.31.0
```

**After:**
```
flask>=3.0.0
requests>=2.31.0
# Remove: anthropic, openai (no longer direct dependencies)
# Just copy the AIProviderClient from ai-provider-service/client_library/
```

---

## Step 5: Model Routing & Selection

If your repo has intelligent model selection logic (like Bewerbungstracker), you can either:

### Option A: Keep Local Routing
Query the centralized service status, make routing decision locally:

```python
from client_library import AIProviderClient

client = AIProviderClient()

def select_model(task_complexity: str) -> str:
    """Select model based on task and available hardware."""
    status = client.get_status()  # Get hardware info
    
    if task_complexity == 'simple':
        return 'claude-3-5-haiku-20241022'
    elif task_complexity == 'complex':
        return 'claude-3-5-sonnet-20241022'
    else:
        return 'claude-3-5-opus-20250514'

# Usage
model = select_model('simple')
response = client.chat(model=model, messages=[...])
```

### Option B: Centralize Routing (Future Enhancement)
Add a `/models/select` endpoint to the service that applies routing logic server-side. This would require extending ai-provider-service with routing rules.

---

## Step 6: Testing Against Centralized Service

### Test 1: Service Startup Verification

```bash
# Terminal 1: Start service
cd ~/projects/ai-provider-service
STARTUP_MODE=lazy SERVICE_TOKEN=test-token python app.py

# Terminal 2: Verify service is running
curl http://localhost:8767/health
# Expected: {"status": "ok"}
```

### Test 2: Basic Chat Request

```bash
curl -X POST http://localhost:8767/chat \
  -H "Authorization: Bearer test-token" \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "claude",
    "model": "claude-3-5-haiku-20241022",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 100
  }' | jq '.result.content[0].text'
```

### Test 3: Python Integration Test

```python
# test_integration.py
import os
from client_library import AIProviderClient

def test_local_service_connection():
    """Verify your repo can connect to ai-provider-service."""
    client = AIProviderClient(
        service_url='http://localhost:8767',
        token='test-token'
    )
    
    response = client.chat(
        provider='claude',
        model='claude-3-5-haiku-20241022',
        messages=[{'role': 'user', 'content': 'Hello'}],
        max_tokens=100
    )
    
    assert 'result' in response
    assert 'content' in response['result']
    assert len(response['result']['content']) > 0
    assert 'text' in response['result']['content'][0]
    print(f"✅ Response: {response['result']['content'][0]['text']}")

def test_model_loading():
    """Verify model auto-loading works."""
    client = AIProviderClient(
        service_url='http://localhost:8767',
        token='test-token'
    )
    
    status_before = client.get_status()
    print(f"Before: {status_before['loaded']}")
    
    # This request should auto-load the model
    response = client.chat(
        provider='claude',
        model='claude-3-5-haiku-20241022',
        messages=[{'role': 'user', 'content': 'Hi'}],
        max_tokens=50
    )
    
    status_after = client.get_status()
    print(f"After: {status_after['loaded']}")
    # Model should be loaded or still loading
    assert response['result']['content'][0]['text']

if __name__ == '__main__':
    test_local_service_connection()
    test_model_loading()
```

Run it:
```bash
export AI_PROVIDER_SERVICE_URL=http://localhost:8767
export AI_PROVIDER_SERVICE_TOKEN=test-token
python test_integration.py
```

### Test 4: TypeScript Integration Test

```typescript
// lib/aiProviderClient.test.ts
import { AIProviderClient } from './aiProviderClient';

async function testLocalServiceConnection() {
  const client = new AIProviderClient(
    'http://localhost:8767',
    'test-token'
  );

  const response = await client.chat({
    provider: 'claude',
    model: 'claude-3-5-haiku-20241022',
    messages: [{ role: 'user', content: 'Hello' }],
    max_tokens: 100
  });

  console.log('✅ Response:', response);
  if (response.result?.content?.[0]?.text) {
    console.log('✅ Text:', response.result.content[0].text);
  }
}

async function testModelLoading() {
  const client = new AIProviderClient(
    'http://localhost:8767',
    'test-token'
  );

  const statusBefore = await client.getStatus();
  console.log('Before:', statusBefore.loaded);

  const response = await client.chat({
    model: 'claude-3-5-haiku-20241022',
    messages: [{ role: 'user', content: 'Hi' }],
    max_tokens: 50
  });

  const statusAfter = await client.getStatus();
  console.log('After:', statusAfter.loaded);
}

testLocalServiceConnection().catch(console.error);
testModelLoading().catch(console.error);
```

---

## Step 7: Update Documentation

Update your repo's README.md:

**Before:**
```markdown
### Setup

1. Set environment variables:
   ```bash
   export ANTHROPIC_API_KEY=sk-ant-...
   ```

2. Install dependencies:
   ```bash
   pip install anthropic openai
   ```
```

**After:**
```markdown
### Setup

1. **Start centralized AI provider service** (one-time or shared):
   ```bash
   cd ~/projects/ai-provider-service
   STARTUP_MODE=lazy python app.py
   ```

2. Set environment variables:
   ```bash
   export AI_PROVIDER_SERVICE_URL=http://localhost:8767
   export AI_PROVIDER_SERVICE_TOKEN=<shared-service-token>
   ```

3. Install dependencies (no heavy SDKs needed):
   ```bash
   pip install requests  # Only dependency for client library
   ```
```

---

## Step 8: Remove Old Dependencies

### Python
```bash
pip uninstall anthropic openai
rm requirements.txt  # Re-create without these
```

### TypeScript/Node.js
```bash
npm uninstall @anthropic-ai/sdk openai
```

Then update `package.json` and `requirements.txt` to remove these.

---

## Troubleshooting

### Error: Connection Refused
```
requests.exceptions.ConnectionError: HTTPConnectionPool(...): Max retries exceeded
```

**Solution:**
- Verify ai-provider-service is running: `curl http://localhost:8767/health`
- Check `AI_PROVIDER_SERVICE_URL` environment variable
- If remote service, verify network connectivity and firewall rules

### Error: 401 Unauthorized
```
requests.exceptions.HTTPError: 401 Client Error: Unauthorized
```

**Solution:**
- Verify `AI_PROVIDER_SERVICE_TOKEN` matches service's `SERVICE_TOKEN`
- Check `Authorization` header is being sent correctly
- Regenerate token if needed

### Error: Model Not Found
```json
{"error": "Model not found or not available"}
```

**Solution:**
- Verify model name (e.g., `claude-3-5-haiku-20241022`, not `Haiku`)
- Check available models: `curl http://localhost:8767/models/status`
- Ensure Ollama is running if using Ollama provider

### Error: VRAM Exhausted
```
"error": "Failed to load model; insufficient VRAM"
```

**Solution:**
- Check status: `client.get_status()` → `utilization_pct`
- Unload unused models: `client.unload_model(model_name)`
- Use smaller models if available
- Increase system VRAM or use GPU with more memory

---

## FAQ

**Q: Do I lose backward compatibility with the old API?**
A: No, you can create a response adapter (see Step 3) to normalize responses. This ensures existing code works without changes.

**Q: Can I use both direct SDK and centralized service?**
A: Yes, but not recommended. It defeats the purpose of centralized management. Recommended: migrate fully to centralized service.

**Q: What if the centralized service is down?**
A: You lose AI functionality. The service supports fallback providers (if configured), but if all providers are down, requests fail. Consider implementing retry logic or queueing.

**Q: How do I handle per-repo authentication?**
A: Currently all repos share one `SERVICE_TOKEN`. For per-repo security, the service can support an allowlist in the database (future enhancement).

**Q: Can I run my own centralized service instance?**
A: Yes! Clone ai-provider-service and run it on your own infrastructure. Set `AI_PROVIDER_SERVICE_URL` to point to your instance.

**Q: What happens to my cost tracking?**
A: Update your cost tracking to query `/models/status` endpoint for usage info. The service tracks model loads and last-used times, which you can use to estimate costs.

**Q: Can I pre-warm models before traffic spikes?**
A: Yes! Use `client.load_model(model_name)` before handling bulk requests. This ensures models are ready and avoids initial load delays.

---

## Next Steps

1. **Review the Architecture Section** above to understand the benefits
2. **Choose your repo type** (Python backend or TypeScript/Node.js)
3. **Follow the Before/After examples** for your language
4. **Update your environment** and requirements files
5. **Test against local ai-provider-service** using the test scripts
6. **Update README and documentation**
7. **Deploy to your environment**

---

## Support

For issues or questions:
- Check SETUP.md in ai-provider-service for service configuration
- Review ARCHITECTURE.md for technical deep-dive
- Check logs: ai-provider-service logs show what models are loading/unloading
