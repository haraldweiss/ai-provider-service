# DSML compatibility implementation plan

**Goal:** Return leaked complete DSML invocations as normal structured tool calls.
**Architecture:** Pure shared parser plus normalization immediately after provider
execution in `dispatcher._execute`; existing API JSON/SSE serializers are reused.
**Tech stack:** Python, Flask, pytest; no additional dependency.
**Spec:** docs/superpowers/specs/2026-11-19-dsml-compatibility-design.md

## Task 1: Parser and dispatch integration

- [x] Add regression tests using the real dispatcher and mocked upstream only;
  demonstrate RED for V4.1 and legacy DSML conversion.
- [x] Implement `providers/dsml.py`, integrate in `dispatcher.py`.
- [x] Cover offered-name guards, malformed/truncated data, mixed wrappers,
  native call deduplication/placeholder repair, and fenced examples.
- [x] Exercise real Cline adapter → dispatch → OpenAI JSON/SSE endpoint.
- [x] Run focused tests and full pytest suite with coverage (80% minimum).
- [x] Request independent Python/code review; address consequential findings.

## Task 2: Documentation, integration and deploy

- [x] Update README and AGENTS.md with semantics and evidence.
- [ ] Commit, push feature branch, create PR; wait for CI test and Docker smoke.
- [ ] Merge with Merge: commit; pull main on Oracle and build with build.sh.
- [ ] Verify candidate before recreation, preserve root-only rollback metadata;
  verify running code hashes, active services and live tool-call response shape.
- [ ] Record final deploy evidence and keep Git/README/AGENTS current.

## Review focus

No promotion of incomplete arguments; no duplicated native execution; unknown
calls retained; examples remain text; non-tool output and usage remain unchanged.

## Execution evidence

Task 1 complete: initial 17 regressions failed against the unmodified dispatcher;
three safety regressions and six review findings reproduced before fixes.
Final: 700 tests passed, 85.77% coverage; DSML + Ollama focused suite 59 passed.
Independent general and Python reviews completed; all consequential findings
fixed with RED→GREEN tests (quoted/list fences, incorrect fence closing, nested
wrappers, filtered responses, duplicate native content). No deferred findings.

The user added the provider docs alert review during implementation. Exact
06:17 UTC OpenAI overview alert was editorial/link-only; 67 relevant tests passed.
No provider protocol change warranted. Details in AGENTS.md and README.
