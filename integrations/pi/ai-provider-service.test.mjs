import test from 'node:test';
import assert from 'node:assert/strict';
import register from './ai-provider-service.ts';

test('Pi distinguishes Pass models and limits unknown Ollama context', async () => {
  process.env.AI_PROVIDER_SERVICE_URL = 'http://gateway.test';
  process.env.SERVICE_TOKEN = 'test-token';
  const previousFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ok: true, json: async () => ({data: [
    {id: 'cline/cline-pass/glm-5.3'},
    {id: 'cline/anthropic/claude-sonnet-4.6'},
    {id: 'ollama/qwen3.8:latest'},
  ]})});
  const registrations = [];
  try {
    await register({registerProvider: (id, config) => registrations.push({id, config})});
    const models = registrations[0].config.models;
    assert.equal(models[0].id, 'cline/cline-pass/glm-5.3');
    assert.match(models[0].name, /Cline Pass/);
    assert.doesNotMatch(models[1].name, /Cline Pass/);
    assert.equal(models[2].contextWindow, 32768);
    assert.deepEqual(models[2].input, ['text']);
  } finally {globalThis.fetch = previousFetch;}
});
