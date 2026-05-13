# PHP Integration Template

Complete PHP integration guide for the centralized AI provider service. Perfect for PHP backends like wolfini_de_web.

---

## PHP AIProviderClient Class

Create `lib/AIProviderClient.php` in your PHP project:

```php
<?php
/**
 * AI Provider Client - PHP wrapper for centralized AI service
 * 
 * Usage:
 *   $client = new AIProviderClient();
 *   $response = $client->chat(
 *       model: 'claude-3-5-sonnet-20241022',
 *       messages: [['role' => 'user', 'content' => 'Hello']]
 *   );
 *   echo $response['result']['content'][0]['text'];
 */

class AIProviderClient
{
    private string $serviceUrl;
    private string $token;
    private ?int $timeout;

    /**
     * Initialize AI Provider Client
     * 
     * @param string|null $serviceUrl Service URL (default: from env or http://localhost:8767)
     * @param string|null $token Auth token (default: from env)
     * @param int|null $timeout Request timeout in seconds (default: 60)
     */
    public function __construct(
        ?string $serviceUrl = null,
        ?string $token = null,
        ?int $timeout = null
    ) {
        $this->serviceUrl = $serviceUrl ?? (getenv('AI_PROVIDER_SERVICE_URL') ?: 'http://localhost:8767');
        $this->token = $token ?? getenv('AI_PROVIDER_SERVICE_TOKEN');
        $this->timeout = $timeout ?? 60;

        if (!$this->token) {
            throw new Exception('AI_PROVIDER_SERVICE_TOKEN not set in environment');
        }
    }

    /**
     * Send a chat request to the centralized service
     */
    public function chat(
        string $model,
        array $messages,
        string $provider = 'claude',
        int $maxTokens = 600,
        array $options = []
    ): array {
        $url = $this->serviceUrl . '/chat';
        
        $payload = array_merge([
            'provider' => $provider,
            'model' => $model,
            'messages' => $messages,
            'max_tokens' => $maxTokens
        ], $options);

        $response = $this->makeRequest('POST', $url, $payload);

        if (!isset($response['result'])) {
            throw new Exception('Invalid response format from service');
        }

        return $response;
    }

    /**
     * Get current service status and loaded models
     */
    public function getStatus(): array
    {
        $url = $this->serviceUrl . '/models/status';
        return $this->makeRequest('GET', $url);
    }

    /**
     * Pre-load a model into memory
     */
    public function loadModel(string $modelName): array
    {
        $url = $this->serviceUrl . '/models/load';
        $payload = ['model_name' => $modelName];
        return $this->makeRequest('POST', $url, $payload);
    }

    /**
     * Unload a model to free VRAM
     */
    public function unloadModel(string $modelName): array
    {
        $url = $this->serviceUrl . '/models/unload';
        $payload = ['model_name' => $modelName];
        return $this->makeRequest('POST', $url, $payload);
    }

    /**
     * Make HTTP request to service
     */
    private function makeRequest(string $method, string $url, ?array $payload = null): array
    {
        $headers = [
            'Authorization: Bearer ' . $this->token,
            'Content-Type: application/json'
        ];

        $ch = curl_init();
        curl_setopt($ch, CURLOPT_URL, $url);
        curl_setopt($ch, CURLOPT_CUSTOMREQUEST, $method);
        curl_setopt($ch, CURLOPT_HTTPHEADER, $headers);
        curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
        curl_setopt($ch, CURLOPT_TIMEOUT, $this->timeout);
        curl_setopt($ch, CURLOPT_FAILONERROR, false);

        if ($method === 'POST' && $payload !== null) {
            curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($payload));
        }

        $response = curl_exec($ch);
        $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $error = curl_error($ch);
        curl_close($ch);

        if ($error) {
            throw new Exception("Service request failed: $error");
        }

        if ($httpCode === 401) {
            throw new Exception('Authentication failed (401). Check AI_PROVIDER_SERVICE_TOKEN');
        }

        if ($httpCode >= 400) {
            throw new Exception("Service returned HTTP $httpCode: $response");
        }

        $decoded = json_decode($response, true);
        if ($decoded === null) {
            throw new Exception('Invalid JSON response from service');
        }

        return $decoded;
    }
}
```

---

## Integration in wolfini_de_web

### Before: Direct Anthropic SDK

```php
<?php
// OLD: backend/services/CategoryService.php
use Anthropic\Client;

class CategoryService
{
    private $client;

    public function __construct()
    {
        $this->client = new Client(apiKey: getenv('ANTHROPIC_API_KEY'));
    }

    public function categorizePost(string $content): string
    {
        $message = $this->client->messages->create(
            model: 'claude-3-5-sonnet-20241022',
            max_tokens: 100,
            messages: [['role' => 'user', 'content' => "Categorize: $content"]]
        );
        return $message->content[0]->text;
    }
}
```

### After: Centralized AI Service

```php
<?php
// NEW: backend/services/CategoryService.php
require 'lib/AIProviderClient.php';

class CategoryService
{
    private AIProviderClient $client;

    public function __construct()
    {
        $this->client = new AIProviderClient();
    }

    public function categorizePost(string $content): string
    {
        $response = $this->client->chat(
            model: 'claude-3-5-sonnet-20241022',
            messages: [['role' => 'user', 'content' => "Categorize: $content"]],
            maxTokens: 100
        );
        return $response['result']['content'][0]['text'];
    }
}
```

---

## Environment Configuration

### .env File

```bash
# Remove:
# ANTHROPIC_API_KEY=sk-ant-...

# Add:
AI_PROVIDER_SERVICE_URL=http://localhost:8767
AI_PROVIDER_SERVICE_TOKEN=<shared-service-token>
```

### composer.json

**Before:**
```json
{
  "require": {
    "anthropic-ai/anthropic-sdk": "^0.39.0"
  }
}
```

**After:**
```json
{
  "require": {
    "php": ">=8.1"
  }
}
```

Run: `composer update`

---

## Quick Migration Checklist for wolfini_de_web

- [ ] Copy AIProviderClient.php to lib/
- [ ] Update composer.json (remove anthropic SDK)
- [ ] Run: composer update
- [ ] Replace all Anthropic\Client() with new AIProviderClient()
- [ ] Replace ->messages->create() with ->chat()
- [ ] Update response access: ->text to ['result']['content'][0]['text']
- [ ] Add AI_PROVIDER_SERVICE_* to .env
- [ ] Run tests
- [ ] Update README

---

See MIGRATION.md for complete troubleshooting and testing guides.
