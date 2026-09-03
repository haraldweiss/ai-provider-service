"""Tests for eval API endpoints and runner."""

import json
import pytest
from unittest.mock import patch, MagicMock
from database import db
from storage.models import EvalTask, EvalRun, EvalResult
from config import Config


@pytest.fixture(autouse=True)
def _set_admin_token():
    Config.ADMIN_TOKEN = 'admin-test-token'


class TestEvalTaskAPI:
    def test_list_tasks_empty(self, app, client):
        resp = client.get('/eval/tasks',
                          headers={'Authorization': 'Bearer test-token'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['count'] == 0

    def test_create_task_requires_admin(self, app, client):
        resp = client.post('/eval/tasks',
                           headers={'Authorization': 'Bearer test-token'},
                           json={'category': 'test', 'name': 't', 'prompt': 'p'})
        assert resp.status_code == 403

    def test_create_task_as_admin(self, app, client):
        resp = client.post('/eval/tasks',
                           headers={'Authorization': 'Bearer admin-test-token'},
                           json={'category': 'coding', 'name': 'test-task',
                                 'prompt': 'Write hello world'})
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['category'] == 'coding'
        assert data['name'] == 'test-task'

    def test_create_task_missing_fields(self, app, client):
        resp = client.post('/eval/tasks',
                           headers={'Authorization': 'Bearer admin-test-token'},
                           json={'category': 'test'})
        assert resp.status_code == 400

    def test_delete_task(self, app, client):
        with app.app_context():
            task = EvalTask(category='test', name='del', prompt='p')
            db.session.add(task)
            db.session.commit()
            tid = task.id

        resp = client.delete(f'/eval/tasks/{tid}',
                             headers={'Authorization': 'Bearer admin-test-token'})
        assert resp.status_code == 200

        resp = client.get('/eval/tasks',
                          headers={'Authorization': 'Bearer test-token'})
        assert resp.get_json()['count'] == 0

    def test_list_tasks_filter_category(self, app, client):
        with app.app_context():
            db.session.add(EvalTask(category='coding', name='c1', prompt='p'))
            db.session.add(EvalTask(category='translation', name='t1', prompt='p'))
            db.session.commit()

        resp = client.get('/eval/tasks?category=coding',
                          headers={'Authorization': 'Bearer test-token'})
        data = resp.get_json()
        assert data['count'] == 1
        assert data['tasks'][0]['category'] == 'coding'


class TestEvalRunAPI:
    def test_list_runs_empty(self, app, client):
        resp = client.get('/eval/runs',
                          headers={'Authorization': 'Bearer test-token'})
        assert resp.status_code == 200
        assert resp.get_json()['runs'] == []

    def test_start_run_no_tasks(self, app, client):
        resp = client.post('/eval/run',
                           headers={'Authorization': 'Bearer admin-test-token'})
        assert resp.status_code == 400

    def test_get_run_not_found(self, app, client):
        resp = client.get('/eval/runs/nonexistent',
                          headers={'Authorization': 'Bearer test-token'})
        assert resp.status_code == 404


class TestLeaderboardAPI:
    def test_leaderboard_empty(self, app, client):
        resp = client.get('/eval/leaderboard',
                          headers={'Authorization': 'Bearer test-token'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['count'] == 0

    def test_leaderboard_with_results(self, app, client):
        with app.app_context():
            run = EvalRun(id='test-run', status='completed', total_models=2,
                          total_tasks=1, completed_models=2, completed_tasks=2)
            db.session.add(run)
            task = EvalTask(category='coding', name='t', prompt='p')
            db.session.add(task)
            db.session.commit()

            db.session.add(EvalResult(
                run_id='test-run', model_id='ollama/test', provider_id='ollama',
                model_name='test', task_id=task.id, category='coding',
                composite_score=4.5, grade='excellent', latency_ms=1000,
            ))
            db.session.add(EvalResult(
                run_id='test-run', model_id='opencode/test2', provider_id='opencode',
                model_name='test2', task_id=task.id, category='coding',
                composite_score=3.0, grade='acceptable', latency_ms=5000,
            ))
            db.session.commit()

        resp = client.get('/eval/leaderboard?run_id=test-run',
                          headers={'Authorization': 'Bearer test-token'})
        data = resp.get_json()
        assert data['count'] == 2
        assert data['leaderboard'][0]['model_id'] == 'ollama/test'
        assert data['leaderboard'][0]['avg_score'] == 4.5


class TestRecommendAPI:
    def test_recommend_empty(self, app, client):
        resp = client.get('/recommend',
                          headers={'Authorization': 'Bearer test-token'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['count'] == 0

    def test_recommend_with_data(self, app, client):
        with app.app_context():
            run = EvalRun(id='rec-run', status='completed', total_models=1,
                          total_tasks=1, completed_models=1, completed_tasks=1)
            db.session.add(run)
            task = EvalTask(category='coding', name='t', prompt='p')
            db.session.add(task)
            db.session.commit()

            db.session.add(EvalResult(
                run_id='rec-run', model_id='ollama/qwen', provider_id='ollama',
                model_name='qwen', task_id=task.id, category='coding',
                composite_score=4.0, grade='good', latency_ms=2000,
            ))
            db.session.commit()

        resp = client.get('/recommend?category=coding&top_n=3',
                          headers={'Authorization': 'Bearer test-token'})
        data = resp.get_json()
        assert data['count'] == 1
        assert data['recommendations'][0]['model_id'] == 'ollama/qwen'


class TestEvalRunner:
    def test_discover_filters_unhealthy(self, app):
        with patch('eval.runner.get_client') as mock_get_client, \
             patch('eval.runner._load_config') as mock_load, \
             patch('eval.runner.health_tracker') as mock_ht, \
             patch('eval.runner.PROVIDER_REGISTRY', {'ollama': {'system': True}, 'claude': {'system': True}}):

            mock_load.return_value = {}
            mock_client = MagicMock()
            mock_client.get_models.return_value = ['model1', 'model2']
            mock_get_client.return_value = mock_client
            mock_ht.is_healthy.side_effect = lambda p: p == 'ollama'

            from eval.runner import discover_available_models
            models = discover_available_models('test-user')
            assert len(models) == 2
            assert all(m['provider_id'] == 'ollama' for m in models)

    def test_score_to_grade_mapping(self):
        from eval.grader import score_to_grade
        assert score_to_grade(5.0) == 'excellent'
        assert score_to_grade(0.0) == 'very_poor'


class TestEvalSeedTasks:
    def test_seed_creates_tasks(self, app):
        from click.testing import CliRunner
        from cli import eval_seed_tasks_command

        runner = CliRunner()
        with app.app_context():
            result = runner.invoke(eval_seed_tasks_command)
            assert result.exit_code == 0
            assert 'Seeded' in result.output

            tasks = EvalTask.query.all()
            assert len(tasks) == 7

            categories = {t.category for t in tasks}
            assert 'coding' in categories
            assert 'translation' in categories
            assert 'reasoning' in categories

    def test_seed_idempotent(self, app):
        from click.testing import CliRunner
        from cli import eval_seed_tasks_command

        runner = CliRunner()
        with app.app_context():
            runner.invoke(eval_seed_tasks_command)
            result = runner.invoke(eval_seed_tasks_command)
            assert 'Seeded 0' in result.output
            assert EvalTask.query.count() == 7
