"""Tests for eval API endpoints and runner."""

import json
import pytest
from datetime import datetime, timedelta, timezone
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


class TestEvalRunLifecycle:
    """POST /eval/run guard rails plus the stale-run reaper.

    Regression context: run 363e1cf2 (started 2026-09-03 with max_models unset,
    so 548 models x 7 tasks) never finished and stayed status='running' forever
    after its worker process died, and run 034c4289 failed instantly with
    "Instance <EvalTask ...> is not bound to a Session".
    """

    ADMIN = {'Authorization': 'Bearer admin-test-token'}

    def _seed_task(self, app):
        with app.app_context():
            db.session.add(EvalTask(category='coding', name='life', prompt='p'))
            db.session.commit()

    def test_active_run_blocks_a_new_run(self, app, client):
        self._seed_task(app)
        with app.app_context():
            db.session.add(EvalRun(id='active-run', status='running',
                                   total_models=1, total_tasks=1))
            db.session.commit()

        resp = client.post('/eval/run', headers=self.ADMIN, json={})
        assert resp.status_code == 409
        body = resp.get_json()
        assert body['run_id'] == 'active-run'
        assert 'force' in body['hint']

    def test_force_starts_second_run_and_max_models_is_capped(self, app, client):
        self._seed_task(app)
        with app.app_context():
            db.session.add(EvalRun(id='active-run', status='running',
                                   total_models=1, total_tasks=1))
            db.session.commit()

        many = [{'provider_id': 'ollama', 'model_name': f'm{i}', 'model_id': f'ollama/m{i}'}
                for i in range(50)]
        with patch('eval.runner.discover_available_models', return_value=many), \
             patch('eval.runner.run_eval_run') as mock_worker:
            resp = client.post('/eval/run', headers=self.ADMIN, json={'force': True})

        assert resp.status_code == 202
        run = resp.get_json()['run']
        # Config.EVAL_MAX_MODELS caps the run when the caller omits max_models.
        assert run['total_models'] == int(Config.EVAL_MAX_MODELS)
        assert run['total_models'] < len(many)
        mock_worker.assert_called_once()

    def test_explicit_max_models_wins(self, app, client):
        self._seed_task(app)
        many = [{'provider_id': 'ollama', 'model_name': f'm{i}', 'model_id': f'ollama/m{i}'}
                for i in range(50)]
        with patch('eval.runner.discover_available_models', return_value=many), \
             patch('eval.runner.run_eval_run'):
            resp = client.post('/eval/run', headers=self.ADMIN,
                               json={'max_models': 3})
        assert resp.status_code == 202
        assert resp.get_json()['run']['total_models'] == 3

    def test_stale_run_is_reaped_by_list_runs(self, app, client):
        with app.app_context():
            db.session.add(EvalRun(
                id='stuck-run', status='running', total_models=548, total_tasks=7,
                started_at=datetime.now(timezone.utc) - timedelta(hours=5),
            ))
            db.session.commit()

        resp = client.get('/eval/runs', headers={'Authorization': 'Bearer test-token'})
        assert resp.status_code == 200
        run = resp.get_json()['runs'][0]
        assert run['status'] == 'failed'
        assert 'stale' in run['error_message']
        assert run['finished_at'] is not None

    def test_fresh_run_is_not_reaped(self, app, client):
        with app.app_context():
            db.session.add(EvalRun(id='fresh-run', status='running',
                                   total_models=1, total_tasks=1))
            db.session.commit()

        resp = client.get('/eval/runs', headers={'Authorization': 'Bearer test-token'})
        assert resp.get_json()['runs'][0]['status'] == 'running'


class TestEvalWorker:
    """run_eval_run() — the background worker behind POST /eval/run."""

    MODEL = {'provider_id': 'ollama', 'model_name': 'm', 'model_id': 'ollama/m'}

    def _seed(self, app, run_id):
        with app.app_context():
            db.session.add(EvalTask(category='coding', name='w', prompt='p'))
            db.session.add(EvalRun(id=run_id, status='pending',
                                   total_models=1, total_tasks=1))
            db.session.commit()

    def _stored(self, app, run_id):
        with app.app_context():
            run = EvalRun.query.get(run_id)
            return {'status': run.status, 'error': run.error_message,
                    'finished': run.finished_at, 'models': run.completed_models}

    def test_completes_and_stamps_finished_at(self, app):
        self._seed(app, 'worker-run')
        from eval.runner import run_eval_run

        with patch('eval.runner._evaluate_model') as mock_eval:
            run_eval_run(app, 'worker-run', [self.MODEL])

        assert mock_eval.call_count == 1
        stored = self._stored(app, 'worker-run')
        assert stored['status'] == 'completed'
        assert stored['finished'] is not None
        assert stored['models'] == 1

    def test_marks_failed_on_evaluator_exception(self, app):
        self._seed(app, 'worker-fail')
        from eval.runner import run_eval_run

        with patch('eval.runner._evaluate_model', side_effect=RuntimeError('boom')):
            run_eval_run(app, 'worker-fail', [self.MODEL])

        stored = self._stored(app, 'worker-fail')
        assert stored['status'] == 'failed'
        assert 'RuntimeError: boom' in stored['error']
        assert stored['finished'] is not None

    def test_reloads_tasks_in_its_own_session(self, app):
        """The worker must not need ORM objects from the caller's session.

        The request teardown closes its session while the worker still runs; a
        captured EvalTask then raises "Instance ... is not bound to a Session".
        """
        self._seed(app, 'worker-detach')
        with app.app_context():
            db.session.remove()      # detach everything, like a request teardown

        seen = []

        def fake_eval(run_obj, model_info, tasks, user_id):
            seen.append([t.name for t in tasks])

        from eval.runner import run_eval_run
        with patch('eval.runner._evaluate_model', side_effect=fake_eval):
            run_eval_run(app, 'worker-detach', [self.MODEL])

        assert seen == [['w']]
        assert self._stored(app, 'worker-detach')['status'] == 'completed'

    def test_unknown_run_id_is_a_no_op(self, app):
        from eval.runner import run_eval_run
        run_eval_run(app, 'does-not-exist', [self.MODEL])   # must not raise
