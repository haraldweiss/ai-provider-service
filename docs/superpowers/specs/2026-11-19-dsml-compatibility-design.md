# DSML compatibility

Recover tool calls leaked as visible DSML by upstream providers, including
DeepSeek V4.1 `<｜DSML｜ calls>` and legacy `tool_calls` envelopes. The user's
request authorizes implementation and deployment; direct ClinePass connections
remain outside the gateway.

Use one conservative normalizer at the dispatch boundary, shared by `/chat`,
OpenAI JSON/SSE responses, primary requests and fallbacks. This avoids duplicating
parsers in each provider adapter and preserves provider billing/model metadata.
The existing Ollama JSON/XML compatibility remains unchanged.

Only complete invocations of explicitly offered tools are eligible. Support
single/double fullwidth and ASCII pipes, optional complete envelopes, multiple
calls, typed JSON parameters and parameter JSON fences. Keep incomplete,
malformed, unknown or fenced example calls as text. Preserve unknown calls in a
mixed envelope. Never execute recovered tools within the gateway.

Native structured calls retain their IDs and precedence. Suppress equivalent
DSML duplicates; repair an empty native argument placeholder only from an
unambiguous complete matching DSML call. Leave conflicting calls as text.
Preserve non-tool response metadata and prose. Do not convert truncated output.
