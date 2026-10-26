# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| main    | :white_check_mark: |

## Reporting a Vulnerability

Please report security vulnerabilities to **Harald Weiss** via one of the following channels:

- **GitHub private vulnerability reporting** (when enabled): Use "Report a vulnerability" under this repository's "Security" tab. Otherwise, use the email address below.
- **Email**: opensource@wolfinisoftware.de

### What to include

- A clear description of the vulnerability
- Steps to reproduce (including environment variables and provider configuration)
- The affected component(s) (dispatcher, API, queue, eval, grant-request workflow)
- Potential impact (key exposure, unauthorized provider access, data leakage)
- Suggested fix or mitigation (if known)

### Response timeline

- **Acknowledgment**: Within 3 business days
- **Initial assessment**: Within 7 business days
- **Fix / patch**: Depends on severity; you will be kept informed of progress

## Security best practices for contributors

- All commits must be signed off (`git commit -s`) per the Developer Certificate of Origin (DCO).
- Never commit `.env`, `MASTER_KEY`, or API keys. Use `.env.example` as a template.
- API keys are encrypted at rest with Fernet (per-user encryption).
- MASTER_KEY rotation: export configs with old key → set new MASTER_KEY → re-encrypt configs.
- Provider gating precedence: gate kill switch → ungated provider → admin → owning personal key → active grant.
- Review the current provider access rules in the README when changing keys, grants, or provider gating.
- Grant-request workflow allows user self-service with admin review.
- Dependency updates are managed via Dependabot (see `.github/dependabot.yml`).

## Disclosure policy

We follow coordinated disclosure. Please do not publicly disclose a vulnerability until we have had a reasonable window to investigate and release a fix. Credit will be given upon request.
