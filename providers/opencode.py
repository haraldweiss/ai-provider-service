"""Opencode.ai (Zen) Client — OpenAI-compatible hosted gateway.
Auto-retry with -free model variant when balance is insufficient.
"""

from __future__ import annotations
import logging
import re
import subprocess
from openai import OpenAI, AuthenticationError
from providers.base import BaseClient
from config import Config

logger = logging.getLogger(__name__)

_MODEL_PREFIX_RE = re.compile(r'^(?:opencode-go/|opencode-)', re.IGNORECASE)
_BALANCE_ERR_RE = re.compile(r'insufficient balance|CreditsError', re.IGNORECASE)

NOTIFY_EMAIL = 'harald.weiss@wolfinisoftware.de'


def _send_notification(subject: str, body: str) -> None:
    try:
        msg = f'Subject: {subject}\nFrom: ai-provider@wolfinisoftware.de\nTo: {NOTIFY_EMAIL}\n\n{body}\n'
        subprocess.run(
            ['/usr/sbin/sendmail', '-t'],
            input=msg, capture_output=True, timeout=10, text=True,
        )
        logger.info('Notification sent: %s', subject)
    except Exception as e:
        logger.warning('Failed to send notification: %s', e)


class OpencodeClient(BaseClient):
    def __init__(self, config: dict):
        api_key = config.get('api_key')
        if not api_key:
            raise ValueError("Opencode: api_key erforderlich")
        base_url = config.get('api_endpoint') or Config.OPENCODE_BASE_URL
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def get_models(self) -> list[str]:
        try:
            return sorted(m.id for m in self.client.models.list().data)
        except Exception as e:
            logger.warning(f'Opencode get_models failed: {e}')
            return []

    def create_message(self, model: str, messages: list[dict], max_tokens: int = 600) -> dict:
        clean = _MODEL_PREFIX_RE.sub('', model)
        if clean != model:
            logger.debug('Opencode model normalized: %s -> %s', model, clean)

        try:
            r = self.client.chat.completions.create(
                model=clean, messages=messages, max_tokens=max_tokens
            )
            return {
                'content': [{'text': r.choices[0].message.content}],
                'usage': {
                    'input_tokens': r.usage.prompt_tokens,
                    'output_tokens': r.usage.completion_tokens,
                },
            }
        except AuthenticationError as e:
            err_body = str(e)
            if _BALANCE_ERR_RE.search(err_body) and not clean.endswith('-free'):
                free_model = clean + '-free'
                logger.warning(
                    'Balance insufficient for model=%s, retrying with %s',
                    clean, free_model,
                )
                _send_notification(
                    'opencode.ai: Auto-Failover zu Free-Modell',
                    f'Das bezahlte Modell "{clean}" hat kein Guthaben mehr.\n'
                    f'Automatischer Failover auf Free-Variante "{free_model}".\n\n'
                    f'Fehler: {err_body[:300]}\n\n'
                    f'Guthaben aufladen: https://opencode.ai/workspace/wrk_01KSKQJKEA4AQ3KV75MPTVNR3R/billing',
                )
                r2 = self.client.chat.completions.create(
                    model=free_model, messages=messages, max_tokens=max_tokens
                )
                return {
                    'content': [{'text': r2.choices[0].message.content}],
                    'usage': {
                        'input_tokens': r2.usage.prompt_tokens,
                        'output_tokens': r2.usage.completion_tokens,
                    },
                    'balance_failover': True,
                }
            raise

    def health(self) -> bool:
        try:
            self.client.models.list()
            return True
        except Exception:
            return False
