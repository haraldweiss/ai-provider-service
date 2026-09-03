"""Eval API — evaluation runs, leaderboard, and model recommendations."""

from __future__ import annotations
import logging
from flask import Blueprint, jsonify, request
from api.auth import require_admin, require_token
from database import db
from storage.models import EvalTask, EvalRun, EvalResult
from eval.runner import run_evaluation, get_leaderboard, get_recommendations

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
    runs = (EvalRun.query
            .order_by(EvalRun.started_at.desc())
            .limit(50)
            .all())
    return jsonify({'runs': [r.to_dict() for r in runs]})


@eval_bp.route('/eval/run', methods=['POST'])
@require_admin
def start_run():
    data = request.get_json(force=True) if request.data else {}
    categories = data.get('categories')
    model_ids = data.get('model_ids')
    max_models = data.get('max_models')

    try:
        run = run_evaluation(
            categories=categories,
            model_ids=model_ids,
            max_models=max_models,
        )
        return jsonify({'run': run.to_dict()}), 202
    except ValueError as e:
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
