# Integration Templates: Ready-to-Use Code Snippets

Copy and adapt these templates into your repo to integrate with the centralized AI provider service.

---

## Python Backend Template

### 1. Create `lib/ai_client.py` in Your Repo

```python
"""
AI Provider Client - Wrapper for centralized AI service.
Replaces direct Anthropic/OpenAI SDK usage.
"""

import os
import requests
from typing import Optional, List, Dict, Any


class AIProviderClient:
    """
    Lightweight client for centralized AI provider service.
    
    Usage:
        from lib.ai_client import AIProviderClient
        
        client = AIProviderClient()
        response = client.chat(
            model="claude-3-5-sonnet-20241022",
            messages=[{"role": "user", "content": "Hello"}],
            provider="claude"
        )
        print(response['result']['content'][0]['text'])
    """
    
    def __init__(
        self,
        service_url: Optional[str] = None,
        token: Optional[str] = None
    ):
        self.service_url = service_url or os.getenv(
            'AI_PROVIDER_SERVICE_URL', 
            'http://localhost:8767'
        )
        self.token = token or os.getenv('AI_PROVIDER_SERVICE_TOKEN')
        
        if not self.token:
            raise ValueError("AI_PROVIDER_SERVICE_TOKEN not set in environment")
    
    def chat(
        self,
        messages: List[Dict[str, str]],
        model: str,
        provider: str = "claude",
        max_tokens: int = 600,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Send a chat request to the centralized service.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Model name (e.g., 'claude-3-5-sonnet-20241022')
            provider: Provider ID (default: 'claude')
            max_tokens: Max response tokens (default: 600)
            **kwargs: Additional parameters passed to service
        
        Returns:
            Response dict with structure:
            {
                "result": {
                    "content": [{"text": "..."}],
                    "usage": {"input_tokens": N, "output_tokens": M}
                },
                "via": "claude",
                "fallback_used": false
            }
        
        Raises:
            requests.RequestException: If service call fails
            ValueError: If response format is invalid
        """
        url = f"{self.service_url}/chat"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "provider": provider,
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            **kwargs
        }
        
        response = requests.post(url, json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        
        return response.json()
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get current service status and loaded models.
        
        Returns:
            Status dict with:
            {
                "loaded": ["model1", "model2"],
                "count": 2,
                "total_size_gb": 14.0,
                "hardware": {...},
                "utilization_pct": 58.3
            }
        """
        url = f"{self.service_url}/models/status"
        headers = {"Authorization": f"Bearer {self.token}"}
        
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        return response.json()
    
    def load_model(self, model_name: str) -> Dict[str, Any]:
        """
        Pre-load a model into memory (useful before bulk operations).
        
        Args:
            model_name: Name of model to load
        
        Returns:
            {"loaded": bool, "model_name": str}
        """
        url = f"{self.service_url}/models/load"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
        
        payload = {"model_name": model_name}
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        
        return response.json()
    
    def unload_model(self, model_name: str) -> Dict[str, Any]:
        """
        Unload a model to free VRAM.
        
        Args:
            model_name: Name of model to unload
        
        Returns:
            {"unloaded": bool, "model_name": str}
        """
        url = f"{self.service_url}/models/unload"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
        
        payload = {"model_name": model_name}
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        
        return response.json()


# Adapter to normalize responses back to old SDK format (optional)
class ResponseAdapter:
    """Normalize centralized service responses to mimic old SDK format."""
    
    @staticmethod
    def normalize(response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert centralized service response format to something more familiar.
        
        This allows existing code to work with minimal changes.
        """
        result = response.get('result', {})
        return {
            'content': result.get('content', []),
            'usage': result.get('usage', {}),
            'provider': response.get('via', 'unknown'),
            'fallback': response.get('fallback_used', False)
        }


# Example usage in your Flask app
if __name__ == '__main__':
    # Initialize client
    client = AIProviderClient()
    
    # Basic chat
    response = client.chat(
        model="claude-3-5-haiku-20241022",
        messages=[
            {"role": "user", "content": "What is Python?"}
        ]
    )
    print("Response:", response['result']['content'][0]['text'])
    
    # Check status before bulk operation
    status = client.get_status()
    print(f"Loaded models: {status['loaded']}")
    print(f"VRAM usage: {status['utilization_pct']}%")
    
    # Pre-warm model for bulk operation
    client.load_model('claude-3-5-sonnet-20241022')
    print("Model warmed up, ready for bulk requests")
```

### 2. Update Your Flask Routes

**Before:**
```python
# backend/routes.py
from anthropic import Anthropic

claude = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))

@app.route('/api/analyze', methods=['POST'])
def analyze():
    data = request.get_json()
    response = claude.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=1000,
        messages=[
            {"role": "user", "content": data['text']}
        ]
    )
    return {'result': response.content[0].text}
```

**After:**
```python
# backend/routes.py
from lib.ai_client import AIProviderClient

ai_client = AIProviderClient()

@app.route('/api/analyze', methods=['POST'])
def analyze():
    data = request.get_json()
    response = ai_client.chat(
        model="claude-3-5-sonnet-20241022",
        max_tokens=1000,
        messages=[
            {"role": "user", "content": data['text']}
        ]
    )
    return {'result': response['result']['content'][0]['text']}
```

### 3. Update `.env`

**Before:**
```bash
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```

**After:**
```bash
AI_PROVIDER_SERVICE_URL=http://localhost:8767
AI_PROVIDER_SERVICE_TOKEN=<shared-service-token>

# Optional (if remote):
# AI_PROVIDER_SERVICE_URL=https://ai.example.com:8767
```

### 4. Update `requirements.txt`

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
```

---

## TypeScript/Node.js Template

### 1. Create `lib/aiProviderClient.ts` in Your Repo

```typescript
/**
 * AI Provider Client - TypeScript wrapper for centralized AI service
 * 
 * Usage:
 *   const client = new AIProviderClient();
 *   const response = await client.chat({
 *     model: 'claude-3-5-sonnet-20241022',
 *     messages: [{ role: 'user', content: 'Hello' }]
 *   });
 *   console.log(response.result.content[0].text);
 */

interface ChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
}

interface ChatOptions {
  provider?: string;
  model: string;
  messages: ChatMessage[];
  max_tokens?: number;
  [key: string]: any;
}

interface ChatResponse {
  result: {
    content: Array<{ text: string }>;
    usage: {
      input_tokens: number;
      output_tokens: number;
    };
  };
  via: string;
  fallback_used: boolean;
}

interface StatusResponse {
  loaded: string[];
  count: number;
  total_size_gb: number;
  hardware: {
    gpu_vram_mb: number;
    system_ram_mb: number;
    cpu_cores: number;
    has_gpu: boolean;
    gpu_type: string;
  };
  utilization_pct: number;
}

export class AIProviderClient {
  private serviceUrl: string;
  private token: string;

  constructor(
    serviceUrl?: string,
    token?: string
  ) {
    this.serviceUrl = serviceUrl || process.env.AI_PROVIDER_SERVICE_URL || 'http://localhost:8767';
    this.token = token || process.env.AI_PROVIDER_SERVICE_TOKEN || '';
    
    if (!this.token) {
      throw new Error('AI_PROVIDER_SERVICE_TOKEN not set in environment');
    }
  }

  /**
   * Send a chat request to the centralized service.
   * 
   * @param options Chat options (model, messages, etc)
   * @returns Promise resolving to chat response
   * @throws Error if service call fails
   */
  async chat(options: ChatOptions): Promise<ChatResponse> {
    const url = `${this.serviceUrl}/chat`;
    const headers = {
      'Authorization': `Bearer ${this.token}`,
      'Content-Type': 'application/json'
    };

    const payload = {
      provider: options.provider || 'claude',
      model: options.model,
      messages: options.messages,
      max_tokens: options.max_tokens || 600,
      ...options
    };

    const response = await fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify(payload)
    });

    if (!response.ok) {
      const error = await response.text();
      throw new Error(`AI Provider error: ${response.statusText} - ${error}`);
    }

    return response.json() as Promise<ChatResponse>;
  }

  /**
   * Get current service status and loaded models.
   * 
   * @returns Promise resolving to status info
   */
  async getStatus(): Promise<StatusResponse> {
    const url = `${this.serviceUrl}/models/status`;
    const headers = {
      'Authorization': `Bearer ${this.token}`
    };

    const response = await fetch(url, { headers });

    if (!response.ok) {
      throw new Error(`Failed to get status: ${response.statusText}`);
    }

    return response.json() as Promise<StatusResponse>;
  }

  /**
   * Pre-load a model into memory.
   * Useful before handling bulk requests to avoid initial load delay.
   * 
   * @param modelName Name of model to load
   * @returns Promise resolving to load result
   */
  async loadModel(modelName: string): Promise<any> {
    const url = `${this.serviceUrl}/models/load`;
    const headers = {
      'Authorization': `Bearer ${this.token}`,
      'Content-Type': 'application/json'
    };

    const response = await fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify({ model_name: modelName })
    });

    if (!response.ok) {
      throw new Error(`Failed to load model: ${response.statusText}`);
    }

    return response.json();
  }

  /**
   * Unload a model to free VRAM.
   * 
   * @param modelName Name of model to unload
   * @returns Promise resolving to unload result
   */
  async unloadModel(modelName: string): Promise<any> {
    const url = `${this.serviceUrl}/models/unload`;
    const headers = {
      'Authorization': `Bearer ${this.token}`,
      'Content-Type': 'application/json'
    };

    const response = await fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify({ model_name: modelName })
    });

    if (!response.ok) {
      throw new Error(`Failed to unload model: ${response.statusText}`);
    }

    return response.json();
  }
}

// Example usage
if (require.main === module) {
  (async () => {
    const client = new AIProviderClient();

    // Basic chat
    const response = await client.chat({
      model: 'claude-3-5-haiku-20241022',
      messages: [
        { role: 'user', content: 'What is TypeScript?' }
      ]
    });
    console.log('Response:', response.result.content[0].text);

    // Check status
    const status = await client.getStatus();
    console.log(`Loaded models: ${status.loaded}`);
    console.log(`VRAM usage: ${status.utilization_pct}%`);

    // Pre-warm model
    await client.loadModel('claude-3-5-sonnet-20241022');
    console.log('Model warmed up');
  })();
}
```

### 2. Update Your Express Routes

**Before:**
```typescript
// routes/api.ts
import Anthropic from '@anthropic-ai/sdk';

const anthropic = new Anthropic({
  apiKey: process.env.ANTHROPIC_API_KEY
});

router.post('/api/analyze', async (req, res) => {
  const { text } = req.body;
  
  const message = await anthropic.messages.create({
    model: 'claude-3-5-sonnet-20241022',
    max_tokens: 1024,
    messages: [
      { role: 'user', content: text }
    ]
  });

  res.json({ result: message.content[0].type === 'text' ? message.content[0].text : '' });
});
```

**After:**
```typescript
// routes/api.ts
import { AIProviderClient } from '../lib/aiProviderClient';

const aiClient = new AIProviderClient();

router.post('/api/analyze', async (req, res) => {
  const { text } = req.body;
  
  const response = await aiClient.chat({
    model: 'claude-3-5-sonnet-20241022',
    max_tokens: 1024,
    messages: [
      { role: 'user', content: text }
    ]
  });

  res.json({ result: response.result.content[0].text });
});
```

### 3. Update `.env`

**Before:**
```bash
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```

**After:**
```bash
AI_PROVIDER_SERVICE_URL=http://localhost:8767
AI_PROVIDER_SERVICE_TOKEN=<shared-service-token>
```

### 4. Update `package.json`

**Before:**
```json
{
  "dependencies": {
    "@anthropic-ai/sdk": "^0.39.0",
    "openai": "^1.54.0",
    "express": "^4.18.0"
  }
}
```

**After:**
```json
{
  "dependencies": {
    "express": "^4.18.0"
  }
}
```

Then run: `npm uninstall @anthropic-ai/sdk openai`

---

## Testing Template

### Python Test

```python
# tests/test_ai_integration.py
import os
import pytest
from lib.ai_client import AIProviderClient


@pytest.fixture
def ai_client():
    """Provide AI client for tests."""
    return AIProviderClient(
        service_url='http://localhost:8767',
        token='test-token'
    )


def test_basic_chat(ai_client):
    """Test basic chat functionality."""
    response = ai_client.chat(
        model='claude-3-5-haiku-20241022',
        messages=[
            {'role': 'user', 'content': 'Say hello'}
        ],
        max_tokens=50
    )
    
    assert 'result' in response
    assert 'content' in response['result']
    assert len(response['result']['content']) > 0
    assert 'text' in response['result']['content'][0]


def test_get_status(ai_client):
    """Test status endpoint."""
    status = ai_client.get_status()
    
    assert 'loaded' in status
    assert 'count' in status
    assert 'hardware' in status
    assert 'utilization_pct' in status


def test_model_loading(ai_client):
    """Test model pre-loading."""
    result = ai_client.load_model('claude-3-5-haiku-20241022')
    
    assert 'loaded' in result
    assert result['model_name'] == 'claude-3-5-haiku-20241022'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
```

### TypeScript Test

```typescript
// tests/aiProviderClient.test.ts
import { AIProviderClient } from '../lib/aiProviderClient';

describe('AIProviderClient', () => {
  let client: AIProviderClient;

  beforeEach(() => {
    client = new AIProviderClient(
      'http://localhost:8767',
      'test-token'
    );
  });

  test('basic chat request', async () => {
    const response = await client.chat({
      model: 'claude-3-5-haiku-20241022',
      messages: [
        { role: 'user', content: 'Say hello' }
      ],
      max_tokens: 50
    });

    expect(response.result).toBeDefined();
    expect(response.result.content).toBeDefined();
    expect(response.result.content.length).toBeGreaterThan(0);
    expect(response.result.content[0].text).toBeDefined();
  });

  test('get service status', async () => {
    const status = await client.getStatus();

    expect(status.loaded).toBeDefined();
    expect(status.count).toBeDefined();
    expect(status.hardware).toBeDefined();
    expect(status.utilization_pct).toBeDefined();
  });

  test('load model', async () => {
    const result = await client.loadModel('claude-3-5-haiku-20241022');

    expect(result.loaded).toBeDefined();
    expect(result.model_name).toBe('claude-3-5-haiku-20241022');
  });
});
```

---

## Environment Setup Checklist

For each repo migrating to the centralized service:

1. **Start ai-provider-service:**
   ```bash
   cd ~/projects/ai-provider-service
   STARTUP_MODE=lazy python app.py
   ```

2. **Set shared SERVICE_TOKEN:**
   ```bash
   export SERVICE_TOKEN=$(openssl rand -hex 32)
   # Or use an existing token if already running
   ```

3. **In your repo, create `.env`:**
   ```bash
   AI_PROVIDER_SERVICE_URL=http://localhost:8767
   AI_PROVIDER_SERVICE_TOKEN=<value-from-step-2>
   ```

4. **Copy client library template** (from above)

5. **Update imports** in your code (see Before/After examples)

6. **Run tests** to verify integration

---

## Quick Conversion Checklist

- [ ] Copy `AIProviderClient` template to your repo
- [ ] Update imports: `from lib.ai_client import AIProviderClient`
- [ ] Replace `Anthropic(api_key=...)` with `AIProviderClient()`
- [ ] Change `client.messages.create()` to `client.chat()`
- [ ] Update response access: `response['result']['content'][0]['text']`
- [ ] Add `AI_PROVIDER_SERVICE_URL` and `AI_PROVIDER_SERVICE_TOKEN` to `.env`
- [ ] Remove `anthropic` and `openai` from requirements.txt / package.json
- [ ] Run integration tests
- [ ] Update README with new setup instructions

---

## Files You'll Have

After integration:
```
your-repo/
├── .env (updated with AI_PROVIDER_SERVICE_*)
├── requirements.txt (or package.json) - simplified
├── lib/
│   └── ai_client.py (or aiProviderClient.ts)
├── routes/ (or similar)
│   └── api.py (updated to use AIProviderClient)
├── tests/
│   └── test_ai_integration.py
└── README.md (updated with setup instructions)
```

That's it! Your repo now uses the centralized service instead of embedded SDK dependencies.
