# Pi gateway extension

`ai-provider-service.ts` is the maintained copy of the Pi extension installed at
`~/.pi/agent/extensions/ai-provider-service.ts`. It reads `SERVICE_TOKEN` and
`AI_PROVIDER_SERVICE_URL` from Pi's existing `.env`; credentials stay outside Git.
Install by copying the TypeScript file to that location, then run `/reload` in Pi.

Cline Pass models appear as `Cline Pass · <model>` under `ai-provider-service`.
Their IDs remain `cline/cline-pass/<model>` for gateway routing. The gateway merges
the separately maintained Pass catalog into Cline's public model list, which omits
those subscription IDs. Subscription eligibility and quotas still apply.

For Pi's direct `ollama` provider in `~/.pi/agent/models.json`, use
`"baseUrl": "http://localhost:11434/v1"` with `"api": "openai-completions"`.
The native Ollama server root is not an OpenAI API base. Keep explicit model IDs
in `scopedModels` consistent with their `:latest` suffix. Unknown Ollama models
from the gateway default to a 32768-token context rather than 128000, to avoid
oversizing local agent prompts. This does not guarantee that every long prompt
finishes within the gateway timeout; compact or start a new chat when switching
from a large cloud-model conversation.

Validation: `node --test integrations/pi/ai-provider-service.test.mjs` (Node 22.18+
with native TypeScript support). The test replaces network access and checks the
registered model labels, IDs and local context budget.
