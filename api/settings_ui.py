"""Self-service UI for managing personal provider API keys."""

import hmac
import secrets

from flask import (
    Blueprint, flash, redirect, render_template, request, session, url_for,
)

from database import db
from providers import PROVIDER_REGISTRY, get_client, provider_supports_personal_key
from storage.models import ProviderConfig
from storage.provider_configs import delete_provider_config, save_provider_config
from storage.user_tokens import (
    is_user_token_generation_active, resolve_user_token,
)
from api.ratelimit import rate_limit


_no_cache_headers = {
    'Cache-Control': 'no-cache, no-store, must-revalidate',
    'Pragma': 'no-cache',
    'Expires': '0',
}


settings_ui_bp = Blueprint(
    'settings_ui', __name__, url_prefix='/settings',
    template_folder='../templates',
)


@settings_ui_bp.after_request
def _apply_no_cache(response):
    response.headers.update(_no_cache_headers)
    return response


@settings_ui_bp.before_request
def _require_active_settings_session():
    if request.endpoint == 'settings_ui.login':
        return None
    user_id = session.get('settings_user_id')
    generation = session.get('settings_token_generation')
    if not user_id or not is_user_token_generation_active(user_id, generation):
        _clear_session()
        return redirect(url_for('settings_ui.login'))
    return None


def _clear_session():
    for key in ('settings_user_id', 'settings_token_generation', 'settings_csrf'):
        session.pop(key, None)


def _valid_csrf() -> bool:
    expected = session.get('settings_csrf', '')
    supplied = request.form.get('csrf_token', '')
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


def _csrf_failure():
    return render_template('settings/error.html', message='Invalid request token.'), 403


def _supported(provider_id: str) -> bool:
    return provider_supports_personal_key(provider_id)


@settings_ui_bp.route('/login', methods=['GET', 'POST'])
@rate_limit('settings:login', methods={'POST'})
def login():
    if request.method == 'POST':
        resolved = resolve_user_token(request.form.get('token', '').strip())
        if resolved:
            session.clear()
            session['settings_user_id'] = resolved[0]
            session['settings_token_generation'] = resolved[1]
            session['settings_csrf'] = secrets.token_urlsafe(32)
            return redirect(url_for('settings_ui.providers'))
        flash('Invalid personal access token.', 'error')
    return render_template('settings/login.html')


@settings_ui_bp.get('/providers')
def providers():
    user_id = session['settings_user_id']
    rows = {
        row.provider_id: row.to_safe_dict()
        for row in ProviderConfig.query.filter_by(user_id=user_id).all()
    }
    provider_rows = [
        {'id': provider_id, **meta, 'config': rows.get(provider_id)}
        for provider_id, meta in PROVIDER_REGISTRY.items()
        if meta.get('personal_api_key')
    ]
    return render_template(
        'settings/providers.html', providers=provider_rows,
        user_id=user_id, csrf_token=session['settings_csrf'],
    )


@settings_ui_bp.post('/providers/<provider_id>/save')
def save(provider_id):
    if not _valid_csrf():
        return _csrf_failure()
    if not _supported(provider_id):
        return render_template('settings/error.html', message='Unsupported provider.'), 404
    api_key = request.form.get('api_key', '').strip()
    if not api_key:
        flash('API key is required.', 'error')
        return redirect(url_for('settings_ui.providers'))
    save_provider_config(session['settings_user_id'], provider_id, {'api_key': api_key})
    db.session.commit()
    flash(f'{PROVIDER_REGISTRY[provider_id]["name"]} configured.', 'success')
    return redirect(url_for('settings_ui.providers'))


@settings_ui_bp.post('/providers/<provider_id>/remove')
def remove(provider_id):
    if not _valid_csrf():
        return _csrf_failure()
    if not _supported(provider_id):
        return render_template('settings/error.html', message='Unsupported provider.'), 404
    delete_provider_config(session['settings_user_id'], provider_id)
    flash(f'{PROVIDER_REGISTRY[provider_id]["name"]} removed.', 'success')
    return redirect(url_for('settings_ui.providers'))


@settings_ui_bp.post('/providers/<provider_id>/test')
def test_provider(provider_id):
    if not _valid_csrf():
        return _csrf_failure()
    if not _supported(provider_id):
        return render_template('settings/error.html', message='Unsupported provider.'), 404
    row = ProviderConfig.query.filter_by(
        user_id=session['settings_user_id'], provider_id=provider_id,
    ).first()
    if row is None:
        flash('Configure a personal key first.', 'error')
    else:
        try:
            models = get_client(provider_id, row.get_config()).get_models()
            flash(f'Connection successful ({len(models)} models).', 'success')
        except Exception:
            flash('Connection test failed.', 'error')
    return redirect(url_for('settings_ui.providers'))


@settings_ui_bp.post('/logout')
def logout():
    if not _valid_csrf():
        return _csrf_failure()
    _clear_session()
    return redirect(url_for('settings_ui.login'))
