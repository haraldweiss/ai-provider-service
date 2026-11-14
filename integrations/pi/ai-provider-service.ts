/// <reference types="@earendil-works/pi-coding-agent" />

/**
 * Pi extension: registers the ai-provider-service gateway as an OpenAI-compatible provider.
 *
 * Config (in ~/.pi/agent/.env):
 *   SERVICE_TOKEN=<token>               (required — gleicher Token wie im Service)
 *   AI_PROVIDER_SERVICE_URL=<url>       (default: http://localhost:8767)
 *
 * Die .env-Datei wird automatisch geladen, falls die Variablen nicht bereits
 * als echte Environment-Variablen gesetzt sind.
 *
 * Der Service läuft auf dem oracle-vm unter
 * https://ai-provider-service.wolfinisoftware.de/
 *
 * Stellt alle Provider des Gateways als openai-completions-Modelle bereit:
 * z.ai/GLM, Ollama (lokal+tunnel), Claude (Server-Key), Custom, etc.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { readFileSync, existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

/**
 * Load ~/.pi/agent/.env into process.env as fallback when env vars are not set.
 * Pi does NOT auto-load .env files, so extensions must do it themselves.
 */
function loadDotenv(): void {
  const envPath = join(homedir(), ".pi", "agent", ".env");
  if (!existsSync(envPath)) return;

  try {
    const content = readFileSync(envPath, "utf-8");
    for (const line of content.split("\n")) {
      const trimmed = line.trim();
      // Skip comments and empty lines
      if (!trimmed || trimmed.startsWith("#")) continue;

      const eqIdx = trimmed.indexOf("=");
      if (eqIdx === -1) continue;

      const key = trimmed.slice(0, eqIdx).trim();
      let value = trimmed.slice(eqIdx + 1).trim();

      // Strip surrounding quotes if present
      if (
        (value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'"))
      ) {
        value = value.slice(1, -1);
      }

      // Only set if not already present in environment
      if (key && !process.env[key]) {
        process.env[key] = value;
      }
    }
  } catch (err) {
    console.warn(
      `[ai-provider-service] Failed to read .env file: ${err}`
    );
  }
}

export default async function (pi: ExtensionAPI) {
  // Load .env fallback before reading env vars
  loadDotenv();

  const baseUrl = process.env.AI_PROVIDER_SERVICE_URL ?? "http://localhost:8767";
  const apiKey = process.env.SERVICE_TOKEN ?? "";

  if (!apiKey) {
    console.warn(
      "[ai-provider-service] SERVICE_TOKEN not set — skipping registration. " +
        "Set SERVICE_TOKEN in ~/.pi/agent/.env or environment."
    );
    return;
  }

  let usedFallback = false;

  // Fetch available models from the service
  let models: Array<{
    id: string;
    name: string;
    reasoning: boolean;
    input: ("text" | "image")[];
    cost: {
      input: number;
      output: number;
      cacheRead: number;
      cacheWrite: number;
    };
    contextWindow: number;
    maxTokens: number;
  }> = [];

  try {
    const response = await fetch(`${baseUrl}/v1/models`, {
      headers: { Authorization: `Bearer ${apiKey}` },
    });

    if (!response.ok) {
      console.warn(
        `[ai-provider-service] /v1/models returned ${response.status} — ` +
          "falling back to hardcoded model list."
      );
      throw new Error(`HTTP ${response.status}`);
    }

    const payload = (await response.json()) as {
      data: Array<{ id: string; owned_by?: string }>;
    };

    // Default model metadata
    const knownModels: Record<string, { contextWindow?: number; reasoning?: boolean }> = {
      "zai/glm-4.5-flash": { contextWindow: 128000, reasoning: false },
      "zai/glm-4-flash": { contextWindow: 128000, reasoning: false },
      "zai/glm-4": { contextWindow: 128000, reasoning: true },
      "ollama/qwen3.6:latest": { contextWindow: 32000, reasoning: false },
      "ollama/dev-coder:latest": { contextWindow: 32000, reasoning: false },
      "ollama/soc-analyst:latest": { contextWindow: 32000, reasoning: false },
      "ollama/soc-detect:latest": { contextWindow: 32000, reasoning: false },
      "ollama/qwen3-coder:latest": { contextWindow: 32000, reasoning: false },
      "ollama/qwen3-coder-cc:latest": { contextWindow: 32000, reasoning: false },
      "ollama/mistral-nemo-cc:latest": { contextWindow: 32000, reasoning: false },
      "ollama/glm-4.7-flash:latest": { contextWindow: 32000, reasoning: false },
      "omlx/Devstral-Small-2-24B-Instruct-2512-4bit": { contextWindow: 32768, reasoning: false },
      "claude/claude-sonnet-4-6-20250514": { contextWindow: 200000, reasoning: true },
      "claude/claude-sonnet-4-20250514": { contextWindow: 200000, reasoning: true },
      "claude/claude-haiku-4-5-20251001": { contextWindow: 200000, reasoning: false },
      "claude/claude-opus-4-6-20250514": { contextWindow: 200000, reasoning: true },
    };

    models = payload.data.map((m) => {
      const known = knownModels[m.id] ?? {};
      const isZAI = m.id.startsWith("zai/");
      const isClaude = m.id.startsWith("claude/");
      const isOllama = m.id.startsWith("ollama/") || m.id.startsWith("wolfinichat/");

      return {
        id: m.id,
        name: m.id.startsWith("cline/cline-pass/")
          ? `Cline Pass · ${m.id.slice("cline/cline-pass/".length)}`
          : m.id,
        reasoning: known.reasoning ?? false,
        input: (isOllama ? ["text"] : ["text", "image"]) as ("text" | "image")[],
        cost: {
          input: isZAI ? 0 : isClaude ? 3.0 : 0,
          output: isZAI ? 0 : isClaude ? 15.0 : 0,
          cacheRead: 0,
          cacheWrite: 0,
        },
        contextWindow: known.contextWindow ?? (isOllama ? 32768 : 128000),
        maxTokens: 4096,
      };
    });
  } catch (err) {
    usedFallback = true;
    console.warn(
      `[ai-provider-service] Failed to fetch models: ${err}. Using hardcoded list.`
    );
    // Hardcoded fallback — mirrors the service's /v1/models response
    models = [
      {
        id: "zai/glm-4.5-flash",
        name: "GLM-4.5 Flash (z.ai)",
        reasoning: false,
        input: ["text", "image"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 128000,
        maxTokens: 4096,
      },
      {
        id: "zai/glm-4-flash",
        name: "GLM-4 Flash (z.ai)",
        reasoning: false,
        input: ["text", "image"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 128000,
        maxTokens: 4096,
      },
      {
        id: "zai/glm-4",
        name: "GLM-4 (z.ai)",
        reasoning: true,
        input: ["text", "image"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 128000,
        maxTokens: 4096,
      },
      {
        id: "claude/claude-sonnet-4-6-20250514",
        name: "Claude Sonnet 4.6 (proxy via ai-provider)",
        reasoning: true,
        input: ["text", "image"],
        cost: { input: 3.0, output: 15.0, cacheRead: 0.3, cacheWrite: 3.75 },
        contextWindow: 200000,
        maxTokens: 8192,
      },
      {
        id: "claude/claude-sonnet-4-20250514",
        name: "Claude Sonnet 4 (proxy via ai-provider)",
        reasoning: true,
        input: ["text", "image"],
        cost: { input: 3.0, output: 15.0, cacheRead: 0.3, cacheWrite: 3.75 },
        contextWindow: 200000,
        maxTokens: 8192,
      },
      {
        id: "claude/claude-haiku-4-5-20251001",
        name: "Claude Haiku 4.5 (proxy via ai-provider)",
        reasoning: false,
        input: ["text", "image"],
        cost: { input: 0.8, output: 4.0, cacheRead: 0.08, cacheWrite: 1.0 },
        contextWindow: 200000,
        maxTokens: 8192,
      },
      {
        id: "claude/claude-opus-4-6-20250514",
        name: "Claude Opus 4.6 (proxy via ai-provider)",
        reasoning: true,
        input: ["text", "image"],
        cost: { input: 15.0, output: 75.0, cacheRead: 1.5, cacheWrite: 18.75 },
        contextWindow: 200000,
        maxTokens: 8192,
      },
      {
        id: "ollama/qwen3.6:latest",
        name: "Qwen 3.6 (local Ollama via ai-provider)",
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 32000,
        maxTokens: 4096,
      },
      {
        id: "ollama/dev-coder:latest",
        name: "Dev Coder (local Ollama via ai-provider)",
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 32000,
        maxTokens: 4096,
      },
      {
        id: "ollama/soc-analyst:latest",
        name: "SOC Analyst (local Ollama via ai-provider)",
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 32000,
        maxTokens: 4096,
      },
      {
        id: "ollama/soc-detect:latest",
        name: "SOC Detect (local Ollama via ai-provider)",
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 32000,
        maxTokens: 4096,
      },
      {
        id: "ollama/qwen3-coder:latest",
        name: "Qwen 3 Coder (local Ollama via ai-provider)",
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 32000,
        maxTokens: 4096,
      },
      {
        id: "ollama/qwen3-coder-cc:latest",
        name: "Qwen 3 Coder CC (local Ollama via ai-provider)",
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 32000,
        maxTokens: 4096,
      },
      {
        id: "ollama/mistral-nemo-cc:latest",
        name: "Mistral Nemo CC (local Ollama via ai-provider)",
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 32000,
        maxTokens: 4096,
      },
      {
        id: "ollama/glm-4.7-flash:latest",
        name: "GLM 4.7 Flash (local Ollama via ai-provider)",
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 32000,
        maxTokens: 4096,
      },
      {
        id: "wolfinichat/qwen3.6:latest",
        name: "Qwen 3.6 (WolfiniChat via ai-provider)",
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 32000,
        maxTokens: 4096,
      },
      {
        id: "wolfinichat/dev-coder:latest",
        name: "Dev Coder (WolfiniChat via ai-provider)",
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 32000,
        maxTokens: 4096,
      },
    ];
  }

  // ── Register primary provider ──────────────────────────────
  if (models.length === 0) {
    console.warn("[ai-provider-service] No models available — skipping registration.");
    return;
  }

  pi.registerProvider("ai-provider-service", {
    name: "AI Provider Service (local gateway)",
    baseUrl: `${baseUrl}/v1`,
    apiKey: apiKey,
    api: "openai-completions",
    authHeader: true,
    models,
  });

  console.log(
    `[ai-provider-service] Registered ${models.length} models at ${baseUrl}`
  );

  // ── Safety net: local Ollama fallback provider ───────────────────
  // When the proxy/gateway is unreachable AND local Ollama is running,
  // register an additional provider that bypasses the gateway entirely.
  // This gives Pi working local models even if the proxy is down.
  if (usedFallback) {
    try {
      const ollamaResp = await fetch("http://localhost:11434/api/tags");
      if (ollamaResp.ok) {
        const ollamaData = (await ollamaResp.json()) as { models?: Array<{ name: string }> };
        const ollamaModels = (ollamaData.models || []).map((m: { name: string }) => ({
          id: m.name,
          name: m.name,
          reasoning: false,
          input: ["text"] as ("text" | "image")[],
          cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
          contextWindow: 32768,
          maxTokens: 4096,
        }));

        if (ollamaModels.length > 0) {
          pi.registerProvider("ollama-local", {
            name: "Ollama Local (direkt — kein Gateway)",
            baseUrl: "http://localhost:11434/v1",
            apiKey: "ollama",
            api: "openai-completions",
            authHeader: false,
            models: ollamaModels,
          });
          console.log(
            `[ai-provider-service] Also registered ${ollamaModels.length} local Ollama model(s) as fallback provider.`
          );
        }
      }
    } catch (ollamaErr) {
      console.warn(
        `[ai-provider-service] Local Ollama not available: ${ollamaErr}`
      );
    }
  }
}
