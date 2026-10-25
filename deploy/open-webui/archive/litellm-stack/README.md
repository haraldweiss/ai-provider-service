# Archived LiteLLM stack — historical reference only

Retired in production in August 2026; archived here on 2026-10-25.
Open WebUI now calls ai-provider-service directly through its `/v1` API.
See [the current deployment entry point](../../README.md) for maintained guidance.

These examples preserve the old proxy, spend-tracking database, and dependent
Open WebUI configuration. They are not supported deployment templates:

- Quadlet files end in `.container.example`, which the Quadlet generator ignores.
- Their `[Install]` targets and installation instructions have been removed.
- `litellm-config.yaml.example` and `env.example` apply only to this retired stack.
- Image tags, model names, ports, paths, and defaults are historical values.

Do not rename these files to `.container` or copy them into a Quadlet search path.
Use Git history when investigating the original deployment procedure.
