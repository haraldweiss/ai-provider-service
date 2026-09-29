"""Eval API — evaluation runs, leaderboard, and model recommendations."""

from __future__ import annotations
import logging
from flask import Blueprint, jsonify, request
from api.auth import require_admin, require_token
from config import Config
from database import db
from storage.models import EvalTask, EvalRun, EvalResult
from eval.runner import (run_evaluation, get_leaderboard, get_recommendations,
                         reap_stale_runs)

logger = logging.getLogger(__name__)

eval_bp = Blueprint('eval', __name__)


@eval_bp.route('/eval/tasks', methods=['GET'])
@require_token
def list_tasks():
    category = request.args.get('category')
    query = EvalTask.query.filter_by(is_active=True)
    if category:
        query = query.filter_by(category=category)
    tasks = query.order_by(EvalTask.category, EvalTask.name).all()
    return jsonify({'tasks': [t.to_dict() for t in tasks], 'count': len(tasks)})


@eval_bp.route('/eval/tasks', methods=['POST'])
@require_admin
def create_task():
    data = request.get_json(force=True)
    required = ('category', 'name', 'prompt')
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({'error': f'Missing fields: {", ".join(missing)}'}), 400

    task = EvalTask(
        category=data['category'],
        name=data['name'],
        prompt=data['prompt'],
        grading_criteria=data.get('grading_criteria'),
        expected_keywords=data.get('expected_keywords'),
        min_length=data.get('min_length'),
        max_length=data.get('max_length'),
        weight=float(data.get('weight', 1.0)),
    )
    db.session.add(task)
    db.session.commit()
    return jsonify(task.to_dict()), 201


@eval_bp.route('/eval/tasks/<int:task_id>', methods=['DELETE'])
@require_admin
def delete_task(task_id):
    task = EvalTask.query.get_or_404(task_id)
    task.is_active = False
    db.session.commit()
    return jsonify({'deleted': True, 'id': task_id})


@eval_bp.route('/eval/runs', methods=['GET'])
@require_token
def list_runs():
    # Self-heal runs abandoned by a dead/restarted worker before reporting them,
    # otherwise a crashed run keeps showing as "running" forever.
    reap_stale_runs()
    runs = (EvalRun.query
            .order_by(EvalRun.started_at.desc())
            .limit(50)
            .all())
    return jsonify({'runs': [r.to_dict() for r in runs]})


@eval_bp.route('/eval/run', methods=['POST'])
@require_admin
def start_run():
    """Start an evaluation run in the background.

    Body (all optional): categories[], model_ids[], max_models, force.
    max_models defaults to Config.EVAL_MAX_MODELS so a single request cannot
    evaluate every advertised model; pass `model_ids` for a targeted run or
    `force: true` to start a second run while another one is still active.
    """
    import threading
    from flask import current_app

    data = request.get_json(force=True, silent=True) or {}
    categories = data.get('categories')
    model_ids = data.get('model_ids')
    force = bool(data.get('force'))

    try:
        from storage.models import EvalRun
        from eval.runner import discover_available_models, run_eval_run, _active_tasks
        import uuid

        # A crashed run must not block new runs forever.
        reap_stale_runs()

        active = (EvalRun.query
                  .filter(EvalRun.status.in_(('pending', 'running')))
                  .order_by(EvalRun.started_at.desc())
                  .first())
        if active is not None and not force:
            return jsonify({
                'error': f'Run {active.id} is still {active.status}',
                'run_id': active.id,
                'hint': 'wait for it to finish, or POST {"force": true}',
            }), 409

        max_models = data.get('max_models') or int(Config.EVAL_MAX_MODELS)

        tasks = _active_tasks(categories)
        if not tasks:
            return jsonify({'error': 'No active eval tasks found'}), 400

        available = discover_available_models(Config.ADMIN_USER_ID)
        if model_ids:
            available = [m for m in available if m['model_id'] in model_ids]
        if max_models:
            available = available[:int(max_models)]

        if not available:
            return jsonify({'error': 'No available models found for evaluation'}), 400

        run = EvalRun(
            id=str(uuid.uuid4()),
            status='pending',
            total_models=len(available),
            total_tasks=len(tasks),
        )
        db.session.add(run)
        db.session.commit()

        # Only the app object + primitives (run id, plain dicts) cross the thread
        # boundary — ORM instances from this request session must not (that is
        # what killed run 034c4289 with "not bound to a Session").
        thread = threading.Thread(
            target=run_eval_run,
            args=(current_app._get_current_object(), run.id, available),
            kwargs={'categories': categories},
            daemon=True,
            name=f'eval-{run.id[:8]}',
        )
        thread.start()

        return jsonify({'run': run.to_dict(), 'message': 'Evaluation started in background'}), 202
    except Exception as e:
        logger.exception('Eval run could not be started')
        db.session.rollback()
        return jsonify({'error': str(e)}), 400


@eval_bp.route('/eval/runs/<run_id>', methods=['GET'])
@require_token
def get_run(run_id):
    run = EvalRun.query.get_or_404(run_id)
    results = (EvalResult.query
               .filter_by(run_id=run_id)
               .order_by(EvalResult.composite_score.desc())
               .all())
    return jsonify({
        'run': run.to_dict(),
        'results': [r.to_dict() for r in results],
    })


@eval_bp.route('/eval/leaderboard', methods=['GET'])
@require_token
def leaderboard():
    run_id = request.args.get('run_id')
    category = request.args.get('category')
    board = get_leaderboard(run_id=run_id, category=category)
    return jsonify({'leaderboard': board, 'count': len(board)})


@eval_bp.route('/recommend', methods=['GET'])
@require_token
def recommend():
    category = request.args.get('category')
    top_n = min(int(request.args.get('top_n', 5)), 20)
    recs = get_recommendations(category=category, top_n=top_n)
    return jsonify({
        'recommendations': recs,
        'category': category,
        'count': len(recs),
    })
