"""Evaluation runner — discovers available models, dispatches tasks, grades responses."""

from __future__ import annotations
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from database import db
from storage.models import EvalTask, EvalRun, EvalResult, ProviderConfig
from providers import PROVIDER_REGISTRY, get_client
from dispatcher import _load_config, _execute
import health_tracker
from eval.grader import (
    deterministic_score, llm_judge_score, composite_score, score_to_grade,
)
from config import Config

logger = logging.getLogger(__name__)

EVAL_REQUEST_DELAY = float(Config.EVAL_REQUEST_DELAY)

EVAL_USER_ID = '_eval'


def discover_available_models(user_id: str) -> list[dict]:
    """Return list of {provider_id, model_name, model_id} for all healthy providers."""
    models = []
    for provider_id in PROVIDER_REGISTRY:
        cfg = _load_config(user_id, provider_id)
        if cfg is None:
            continue
        if not health_tracker.is_healthy(provider_id):
            logger.info('Eval: skipping unhealthy provider %s', provider_id)
            continue
        try:
            client = get_client(provider_id, cfg)
            provider_models = client.get_models()
        except Exception as e:
            logger.info('Eval: skipping unavailable provider %s: %s', provider_id, e)
            continue
        for model_name in provider_models:
            if not model_name:
                continue
            models.append({
                'provider_id': provider_id,
                'model_name': str(model_name),
                'model_id': f'{provider_id}/{model_name}',
            })
    return models


def _judge_dispatch(messages: list) -> str:
    """Dispatch a judge request using a strong model. Falls back gracefully."""
    judge_provider = Config.EVAL_JUDGE_PROVIDER or 'opencode'
    judge_model = Config.EVAL_JUDGE_MODEL or 'deepseek-v4-flash-free'

    cfg = _load_config(Config.ADMIN_USER_ID, judge_provider)
    if cfg is None:
        return ''
    try:
        client = get_client(judge_provider, cfg)
        result = client.create_message(judge_model, messages, max_tokens=200)
        if isinstance(result, dict):
            content = result.get('content')
            if isinstance(content, list):
                return ' '.join(
                    b.get('text', '') for b in content if isinstance(b, dict)
                )
            if isinstance(content, str):
                return content
        return ''
    except Exception as e:
        logger.warning('Judge dispatch failed: %s', e)
        return ''


def run_evaluation(
    user_id: str = None,
    categories: list[str] = None,
    model_ids: list[str] = None,
    max_models: int = None,
) -> EvalRun:
    """Run a full evaluation: all active tasks against all available models.

    Args:
        user_id: which user's provider configs to use (default: admin)
        categories: filter tasks by category (default: all active)
        model_ids: filter models (default: all available)
        max_models: cap number of models to evaluate (for cost control)
    """
    user_id = user_id or Config.ADMIN_USER_ID

    query = EvalTask.query.filter_by(is_active=True)
    if categories:
        query = query.filter(EvalTask.category.in_(categories))
    tasks = query.all()
    if not tasks:
        raise ValueError('No active eval tasks found')

    available = discover_available_models(user_id)
    if model_ids:
        available = [m for m in available if m['model_id'] in model_ids]
    if max_models:
        available = available[:max_models]

    if not available:
        raise ValueError('No available models found for evaluation')

    run = EvalRun(
        id=str(uuid.uuid4()),
        status='running',
        total_models=len(available),
        total_tasks=len(tasks),
    )
    db.session.add(run)
    db.session.commit()

    logger.info('Eval run %s: %d models x %d tasks', run.id, len(available), len(tasks))

    try:
        for model_info in available:
            _evaluate_model(run, model_info, tasks, user_id)
            run.completed_models += 1
            db.session.commit()

        run.status = 'completed'
        run.finished_at = datetime.now(timezone.utc)
    except Exception as e:
        logger.error('Eval run %s failed: %s', run.id, e)
        run.status = 'failed'
        run.error_message = str(e)[:1000]
        run.finished_at = datetime.now(timezone.utc)

    db.session.commit()
    return run


def _evaluate_model(
    run: EvalRun,
    model_info: dict,
    tasks: list[EvalTask],
    user_id: str,
) -> None:
    """Evaluate a single model against all tasks."""
    provider_id = model_info['provider_id']
    model_name = model_info['model_name']
    model_id = model_info['model_id']

    logger.info('Evaluating %s (%d tasks)', model_id, len(tasks))

    for task in tasks:
        run.completed_tasks += 1
        result = _evaluate_single(run.id, model_info, task, user_id)
        db.session.add(result)
        db.session.commit()
        
        # Rate limit protection: delay between requests
        if EVAL_REQUEST_DELAY > 0:
            time.sleep(EVAL_REQUEST_DELAY)


def _evaluate_single(
    run_id: str,
    model_info: dict,
    task: EvalTask,
    user_id: str,
) -> EvalResult:
    """Run a single task against a single model and grade the response."""
    provider_id = model_info['provider_id']
    model_name = model_info['model_name']
    model_id = model_info['model_id']

    eval_result = EvalResult(
        run_id=run_id,
        model_id=model_id,
        provider_id=provider_id,
        model_name=model_name,
        task_id=task.id,
        category=task.category,
    )

    messages = [{'role': 'user', 'content': task.prompt}]
    started = time.monotonic()

    try:
        cfg = _load_config(user_id, provider_id)
        if cfg is None:
            eval_result.error_message = f'No config for {provider_id}'
            eval_result.composite_score = 0.0
            eval_result.grade = 'very_poor'
            return eval_result

        client = get_client(provider_id, cfg)
        raw_result = client.create_message(model_name, messages, max_tokens=1024)
        latency_ms = int((time.monotonic() - started) * 1000)

        response_text = ''
        if isinstance(raw_result, dict):
            content = raw_result.get('content')
            if isinstance(content, list):
                response_text = ' '.join(
                    b.get('text', '') for b in content if isinstance(b, dict)
                )
            elif isinstance(content, str):
                response_text = content

        usage = (raw_result or {}).get('usage') or {}
        input_tokens = usage.get('input_tokens')
        output_tokens = usage.get('output_tokens')

        eval_result.response_text = response_text[:5000]
        eval_result.latency_ms = latency_ms
        eval_result.input_tokens = input_tokens
        eval_result.output_tokens = output_tokens

        det_score = deterministic_score(
            response_text,
            expected_keywords=task.expected_keywords,
            min_length=task.min_length,
            max_length=task.max_length,
        )

        judge_result = llm_judge_score(
            task.prompt, response_text,
            grading_criteria=task.grading_criteria,
            judge_dispatch_fn=_judge_dispatch,
        )

        eval_result.accuracy_score = judge_result['accuracy']
        eval_result.quality_score = judge_result['quality']
        eval_result.composite_score = composite_score(
            accuracy=judge_result['accuracy'],
            quality=judge_result['quality'],
            deterministic=det_score,
            latency_ms=latency_ms,
        )
        eval_result.grade = score_to_grade(eval_result.composite_score)

    except Exception as e:
        latency_ms = int((time.monotonic() - started) * 1000)
        logger.warning('Eval %s/%s failed: %s', model_id, task.name, e)
        eval_result.error_message = f'{type(e).__name__}: {str(e)[:500]}'
        eval_result.latency_ms = latency_ms
        eval_result.composite_score = 0.0
        eval_result.grade = 'very_poor'

    return eval_result


def get_leaderboard(run_id: str = None, category: str = None) -> list[dict]:
    """Aggregate results into a model leaderboard.

    Returns list of {model_id, avg_score, grade, tasks_evaluated, avg_latency_ms}
    sorted by avg_score descending.
    """
    from sqlalchemy import func

    query = db.session.query(
        EvalResult.model_id,
        func.avg(EvalResult.composite_score).label('avg_score'),
        func.avg(EvalResult.latency_ms).label('avg_latency_ms'),
        func.count(EvalResult.id).label('tasks_evaluated'),
    )

    if run_id:
        query = query.filter(EvalResult.run_id == run_id)
    else:
        latest_run = (EvalRun.query
                      .filter_by(status='completed')
                      .order_by(EvalRun.finished_at.desc())
                      .first())
        if latest_run:
            query = query.filter(EvalResult.run_id == latest_run.id)
        else:
            return []

    if category:
        query = query.filter(EvalResult.category == category)

    query = query.filter(EvalResult.error_message.is_(None))
    rows = query.group_by(EvalResult.model_id).all()

    leaderboard = []
    for row in rows:
        avg = float(row.avg_score) if row.avg_score else 0.0
        leaderboard.append({
            'model_id': row.model_id,
            'avg_score': round(avg, 2),
            'grade': score_to_grade(avg),
            'tasks_evaluated': row.tasks_evaluated,
            'avg_latency_ms': int(row.avg_latency_ms) if row.avg_latency_ms else None,
        })

    leaderboard.sort(key=lambda x: x['avg_score'], reverse=True)
    return leaderboard


def get_recommendations(category: str = None, top_n: int = 5) -> list[dict]:
    """Get top-ranked models for a task category (or overall)."""
    leaderboard = get_leaderboard(category=category)
    return leaderboard[:top_n]
