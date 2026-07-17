# Colibri provider POC design

**Status:** Approved for specification; not approved for deployment or production traffic.

## Purpose

Evaluate [JustVugg/colibri](https://github.com/JustVugg/colibri) as a private,
local provider for GLM-5.2 through `ai-provider-service`.  The proof of concept
must support OpenAI-compatible text chat, tool calls, and true SSE streaming
without weakening the gateway's authentication, identity binding, fallback, or
health-monitoring behaviour.

The target is the Mac Studio currently represented by the oracle-vm Ollama
tunnel on port 11440.  The expected hardware is 32 GB unified memory and a
1 TB SSD.  This makes the work an integration and recovery POC, not a claim of
interactive or production-grade throughput.  Colibri's model requires roughly
370 GB of local disk storage, so the preflight must prove that at least 400 GB
is available on a stable, directly attached volume before any model is
downloaded.

## Scope

Included:

- A distinct, system-level `colibri` provider and model namespace
  `colibri/glm-5.2-colibri`.
- An authenticated Colibri service on the Mac Studio, reachable only through a
  dedicated reverse-SSH tunnel and the existing oracle-vm Docker bridge.
- Tool declaration forwarding, structured tool-call normalization, and native
  downstream SSE streaming for `POST /v1/chat/completions`.
- Bounded provider admission, explicit 429/503 behaviour, parallel health
  checks, automated tests, and a measured acceptance decision.

Excluded:

- Production routing, automatic fallback *to* Colibri, pricing, personal
  Colibri keys, images/audio, multi-user throughput, and high-concurrency
  batching.
- Changing or reusing the three Ollama tunnel ports (11434, 11435, 11440).
- Any permanent fix made directly inside a running gateway container.

## Current observations

On 2026-07-17, oracle-vm was listening on 127.0.0.1 and the Docker bridge for
ports 11434, 11435, and 11440.  However, `GET
http://127.0.0.1:11440/api/tags` timed out after five seconds.  Thus the
Mac-Studio tunnel exists but does not currently provide a responsive Ollama
backend.  The POC must begin with a preflight and must not assume the existing
Ollama installation is healthy.

## Architecture

```text
authenticated client
  -> ai-provider-service /v1/chat/completions
    -> ColibriClient (provider=colibri)
      -> host.docker.internal:18000 inside ai-provider container
        -> oracle-vm bridge: 172.17.0.1:18000 -> 127.0.0.1:18000
          -> reverse SSH tunnel from Mac Studio
            -> Colibri: 127.0.0.1:18000
              -> local GLM-5.2 model files
```

The port number is intentionally separate from Ollama.  The Mac Studio binds
Colibri to loopback and sets `COLI_API_KEY`; it never binds an inference API to
a public interface.  The reverse tunnel uses a new launchd service with logs
under `~/Library/Logs`, never below `~/.ollama`.  The oracle-vm bridge follows
the existing socat pattern but has a separately named listener and service
configuration.

The Colibri API key is a server-to-server secret.  It is supplied to the
gateway only through the root-owned environment file at container creation and
is never returned by an endpoint, logged, or written into repository files.

## Provider behaviour

`colibri` is a `system: true` provider.  It initially requires explicit
enablement through a configuration flag and is not added to
`UNGATED_PROVIDERS`; the POC is accessible only to the admin user.  This
prevents a slow single-machine test backend from becoming an unbounded shared
resource.

`ColibriClient` implements the standard provider interface plus a streaming
adapter:

- `get_models()` calls `GET /v1/models` with its bearer key and accepts only
  the configured model ID.
- `health()` calls Colibri's `GET /health` with a short timeout.  It returns a
  boolean only and cannot block periodic parallel health checks.
- Non-streaming chat calls forward `model`, normalized messages,
  `max_completion_tokens`, `temperature`, `top_p`, `tools`, and `tool_choice`.
  The adapter maps the first OpenAI choice into the gateway's internal content,
  usage, stop-reason, and tool-call format.
- Tool calls are accepted only if their function name occurs in the `tools`
  supplied by the client.  The gateway does not execute a tool; it merely
  returns a structured OpenAI tool call.  Unoffered names remain ordinary text
  or cause a provider response validation error.
- Streaming chat opens Colibri's SSE response with `stream=true`, forwards
  validated data frames to the gateway OpenAI endpoint as they arrive, and
  emits the existing opening role chunk, `finish_reason: null` interim chunks,
  one final finish chunk, and `[DONE]`.  The adapter must preserve a Colibri
  upstream error before a stream starts as an OpenAI-shaped HTTP error.

The generic `CustomClient` is deliberately not used: it currently ignores its
`tools` argument, only accepts buffered JSON, and cannot preserve native
streaming.

## Queueing, timeout, and failure rules

Colibri is configured for one active inference request, one waiting request,
and a 60-second admission timeout.  These values are POC-specific and are not
inherited from the Ollama pool.  Queue full and queue deadline expiration map
to 429.  A missing tunnel, failed health check, connection error, invalid
upstream response, or engine crash maps to `ProviderUnavailableError` and
therefore 503 when no eligible fallback exists.

The gateway may keep existing client-controlled fallback behaviour, but no
provider configuration may name `colibri` as a default fallback during the
POC.  Slow requests are not queued in SQLite: that queue exists for recoverable
Ollama work and is unsuitable for retaining arbitrary, potentially long
Colibri agent sessions.

The Mac Studio is reserved for Colibri during each measurement window.  Its
Ollama process is stopped or the Studio endpoint is removed from active Ollama
routing before Colibri starts.  This avoids competing model residency within
32 GB unified memory.  The existing tunnel and configuration are restored when
the window ends.

## Preflight and operations

Before installing Colibri or downloading model data, collect read-only facts
from the Mac Studio:

1. OS/CPU architecture, physical memory, and free space on the intended model
   volume.
2. Volume mount stability, filesystem, direct-attachment status, and measured
   random-read throughput using Colibri's provided I/O benchmark.
3. Existing Ollama process, launchd job, reverse tunnel status, and memory
   pressure.
4. Colibri build self-test plus `coli doctor` and `coli plan` for the proposed
   RAM budget.

The model volume may be external only if it is reliably mounted before the
launchd service starts.  Logs remain on internal storage.  Failure of a
preflight condition ends the POC before model download or gateway changes.

The gateway deploy follows the repository's normal discipline: tests and
Docker smoke first, `build.sh <commit-sha>`, then recreate the container with
the root-owned environment file.  A container restart alone is not sufficient
for the new environment variables.  No container-local hotfix is permitted.

## Verification matrix

| Case | Required result |
| --- | --- |
| Colibri unavailable | Provider becomes unhealthy; model is hidden; a request yields 503 or a configured eligible fallback. |
| Model discovery | Authenticated admin sees exactly `colibri/glm-5.2-colibri` only while `/v1/models` succeeds. |
| Plain completion | Non-streaming reply has normalized content, usage, and a concrete finish reason. |
| Tool call | An offered function returns structured OpenAI `tool_calls`; an unoffered function cannot become executable output. |
| Streaming | Client receives multiple SSE chunks before completion, `finish_reason: null` on interim choices, a final concrete finish reason, and `[DONE]`. |
| Queue full / deadline | Gateway returns an OpenAI-shaped 429 without hanging a Gunicorn worker. |
| Tunnel or engine loss mid-request | Client sees a bounded failure; health recovers when the dependency recovers; unrelated providers continue serving. |
| Regression | Focused provider/API tests, full `pytest -q`, Docker build/boot/`/health` smoke, and `git diff --check` are clean. |

## Success decision and rollback

The POC is successful only when every verification-matrix row passes and the
Mac Studio sustains a coherent tool-call conversation without swap, OOM, or
unbounded queueing.  Throughput and first-token latency are recorded but have
no minimum acceptance threshold; the expected 32 GB limitation is the subject
of the experiment.

The POC is declined for production if the preflight fails, requests induce
memory pressure/swap, streaming cannot stay bounded through the gateway, or
the measured latency is unsuitable for the intended interactive workload.

Rollback is fully reversible: remove `colibri` from the environment enablement
list, recreate the gateway container from its prior SHA-tagged image, stop and
unload the dedicated Colibri and tunnel launchd jobs, remove the corresponding
oracle-vm bridge listener, and restore the former Ollama routing.  Model files
are retained until the owner explicitly requests deletion; no model data is
removed automatically.
