"""News-Agent Runner — orchestriert den Tool-Loop über dispatcher.dispatch().

Verantwortlichkeit: Dispatcher bleibt one-shot pro Iteration. Der Runner führt
die Tool-Calls aus und reicht die Resultate als nächste User-Message zurück.
"""
from __future__ import annotations
import argparse
import json
import logging
import os
import sys
import time
from typing import Any

logger = logging.getLogger(__name__)

# dispatch + execute_tool are imported at module level so tests can patch them
# as 'agents.news.runner.dispatch' / 'agents.news.runner.execute_tool'.
from dispatcher import dispatch
from agents.news.tool_schemas import TOOLS
from agents.news.prompts import NEWS_SYSTEM_PROMPT
from agents.news import tools as news_tools


_TOOL_FUNCTIONS = {
    'web_search': news_tools.web_search,
    'web_fetch': news_tools.web_fetch,
    'publish_to_wordpress': news_tools.publish_to_wordpress,
}


def execute_tool(name: str, payload: dict, dry_run: bool = False) -> Any:
    """Dispatch a single tool call. Unknown tools return an error dict (never raise)."""
    fn = _TOOL_FUNCTIONS.get(name)
    if fn is None:
        return {'error': f'unknown tool: {name}'}
    try:
        if name == 'publish_to_wordpress':
            return fn(dry_run=dry_run, **(payload or {}))
        return fn(**(payload or {}))
    except TypeError as e:
        return {'error': f'invalid tool input: {e}'}
    except Exception as e:
        logger.exception(f'tool {name} raised unexpectedly')
        return {'error': f'tool failed: {type(e).__name__}: {e}'}


def _max_iterations() -> int:
    return int(os.getenv('NEWS_AGENT_MAX_ITERATIONS', '40'))


def _model_for(provider: str) -> str:
    if provider == 'claude':
        return os.getenv('NEWS_AGENT_MODEL_CLAUDE', 'claude-sonnet-4-6')
    if provider == 'ollama':
        return os.getenv('NEWS_AGENT_MODEL_OLLAMA', 'qwen3.6:latest')
    raise ValueError(f'unsupported provider: {provider}')


def run_news_agent(dry_run: bool = False) -> dict:
    """Run one full news-roundup. Returns summary dict, raises RuntimeError on divergence."""
    primary = os.getenv('NEWS_AGENT_PROVIDER', 'claude')
    fallback = os.getenv('NEWS_AGENT_FALLBACK', '').strip() or None
    primary_model = _model_for(primary)
    fallback_model = _model_for(fallback) if fallback else None
    max_iter = _max_iterations()

    user_kickoff = ('Erstelle den heutigen News-Roundup für das Local-LLM-Ökosystem '
                    '(Ollama, llama.cpp, supporting tools). Halte dich an die Layout-'
                    'Vorgaben im System-Prompt und schließe mit publish_to_wordpress ab.')

    messages: list[dict] = [
        {'role': 'system', 'content': NEWS_SYSTEM_PROMPT},
        {'role': 'user', 'content': user_kickoff},
    ]

    start = time.monotonic()
    tool_counts: dict[str, int] = {}
    final_post: dict | None = None

    for iteration in range(max_iter):
        result_envelope = dispatch(
            user_id='news-agent',
            provider_id=primary,
            model=primary_model,
            messages=messages,
            max_tokens=8192,
            tools=TOOLS,
            fallback_provider_override=fallback,
            fallback_model_override=fallback_model,
            origin_app='news-agent',
        )
        msg = result_envelope['result']
        stop_reason = msg.get('stop_reason', 'end_turn')

        # Append the assistant turn to the conversation.
        # Skip empty text blocks — Anthropic rejects them on the next turn
        # with "text content blocks must be non-empty". The provider client
        # always emits at least one text block (possibly empty) as a defensive
        # default, but we must not echo empties back into the message history.
        assistant_blocks: list[dict] = []
        for c in msg.get('content', []):
            text = c.get('text', '')
            if text:
                assistant_blocks.append({'type': 'text', 'text': text})
        for tc in msg.get('tool_calls', []) or []:
            assistant_blocks.append({'type': 'tool_use',
                                     'id': tc['id'],
                                     'name': tc['name'],
                                     'input': tc.get('input', {})})
        messages.append({'role': 'assistant', 'content': assistant_blocks})

        if stop_reason != 'tool_use':
            duration = time.monotonic() - start
            logger.info(f'news-agent run complete: iterations={iteration} '
                        f'tool_counts={tool_counts} duration={duration:.1f}s '
                        f'via={result_envelope.get("via")} '
                        f'fallback_used={result_envelope.get("fallback_used")}')
            return {
                'iterations': iteration,
                'final_stop_reason': stop_reason,
                'tool_counts': tool_counts,
                'duration_seconds': duration,
                'final_post': final_post,
                'via': result_envelope.get('via'),
                'fallback_used': result_envelope.get('fallback_used'),
            }

        tool_results = []
        for call in msg.get('tool_calls', []) or []:
            tool_counts[call['name']] = tool_counts.get(call['name'], 0) + 1
            tr = execute_tool(call['name'], call.get('input', {}), dry_run=dry_run)
            if call['name'] == 'publish_to_wordpress' and isinstance(tr, dict):
                if tr.get('post_id') or tr.get('dry_run'):
                    final_post = tr
            tool_results.append({
                'type': 'tool_result',
                'tool_use_id': call['id'],
                'content': json.dumps(tr, ensure_ascii=False)[:50_000],
            })
        messages.append({'role': 'user', 'content': tool_results})

    raise RuntimeError(f'Tool-Loop did not converge after {max_iter} iterations')


def main() -> int:
    parser = argparse.ArgumentParser(description='Run the news-agent once.')
    parser.add_argument('--dry-run', action='store_true',
                        help='Skip actual WordPress publish; print payload only.')
    args = parser.parse_args()

    logging.basicConfig(
        level=os.getenv('LOG_LEVEL', 'INFO'),
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )

    # We need a Flask app context for the UsageEvent DB writes inside dispatch().
    from app import create_app
    app = create_app()
    with app.app_context():
        try:
            summary = run_news_agent(dry_run=args.dry_run)
        except Exception as e:
            logger.exception('news-agent run failed')
            print(f'FAIL: {type(e).__name__}: {e}', file=sys.stderr)
            return 1

    print(json.dumps(summary, default=str, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
