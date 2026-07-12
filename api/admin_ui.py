"""Admin UI — Jinja-rendered pages at /admin/ui.

Auth flow:
  1. Apache Basic Auth (ai-admin.wolfinisoftware.de) → X-Forwarded-User header → auto-auth.
  2. GET /admin/ui?token=<ADMIN_TOKEN>  → validates, sets session cookie, redirects.
  3. POST /admin/ui/login with username/password or token → validates, sets session.
  4. Subsequent navigation uses session['admin']=True.
  5. GET /admin/ui/logout → clears session.
"""

from flask import (
    Blueprint, render_template, request, redirect, url_for, session, abort,
    jsonify, current_app, flash,
)
from werkzeug.security import check_password_hash, generate_password_hash
from datetime import datetime, timedelta, timezone
import secrets
from config import Config
from database import db
from storage.models import ProviderConfig, ProviderGrant, UsageEvent, UserAccessToken


_no_cache_headers = {
    'Cache-Control': 'no-cache, no-store, must-revalidate',
    'Pragma': 'no-cache',
    'Expires': '0',
}

admin_ui_bp = Blueprint(
    'admin_ui', __name__,
    url_prefix='/admin/ui',
    template_folder='../templates',
)


@admin_ui_bp.after_request
def _apply_no_cache(response):
    response.headers.update(_no_cache_headers)
    return response


def _is_authed() -> bool:
    return bool(session.get('admin'))


def _require_admin_ui():
    if not _is_authed():
        return redirect(url_for('admin_ui.login'))
    return None


@admin_ui_bp.before_request
def _entry():
    if request.endpoint in ('admin_ui.login', 'admin_ui.logout'):
        return None

    # Auto-auth via Apache Basic Auth (X-Forwarded-User set by ai-admin vhost).
    # Only trusted when explicitly enabled via Config.TRUST_FORWARDED_USER and
    # the request originates from localhost (Apache reverse proxy).
    if Config.TRUST_FORWARDED_USER:
        forwarded_user = request.headers.get('X-Forwarded-User')
        if forwarded_user and request.remote_addr in ('127.0.0.1', '::1', 'localhost'):
            if not _is_authed():
                session['admin'] = True
                session['admin_csrf'] = secrets.token_urlsafe(32)
            return None

    token = request.args.get('token')
    if token:
        if Config.ADMIN_TOKEN and token == Config.ADMIN_TOKEN:
            session['admin'] = True
            session['admin_csrf'] = secrets.token_urlsafe(32)
            kwargs = request.view_args or {}
            return redirect(url_for(request.endpoint, **kwargs))
        return redirect(url_for('admin_ui.login'))


@admin_ui_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        token = request.form.get('token', '')

        # Username + password auth
        if username and password:
            if (username == Config.ADMIN_USER_ID and Config.ADMIN_PASSWORD
                    and password == Config.ADMIN_PASSWORD):
                session['admin'] = True
                session['admin_csrf'] = secrets.token_urlsafe(32)
                return redirect(url_for('admin_ui.root'))
            flash('Invalid username or password', 'error')
            return render_template('admin/login.html')

        # Token auth (form field)
        if token:
            if Config.ADMIN_TOKEN and token == Config.ADMIN_TOKEN:
                session['admin'] = True
                session['admin_csrf'] = secrets.token_urlsafe(32)
                return redirect(url_for('admin_ui.root'))
            flash('Invalid admin token', 'error')
            return render_template('admin/login.html')

        flash('Enter username + password or admin token', 'error')
        return render_template('admin/login.html')
    return render_template('admin/login.html')


@admin_ui_bp.get('/logout')
def logout():
    session.pop('admin', None)
    session.pop('admin_csrf', None)
    return redirect(url_for('admin_ui.login'))


@admin_ui_bp.get('/')
def root():
    if not _is_authed():
        return redirect(url_for('admin_ui.login'))
    return redirect(url_for('admin_ui.users'))


@admin_ui_bp.get('/users')
def users():
    redirect_resp = _require_admin_ui()
    if redirect_resp:
        return redirect_resp
    from api.admin_api import build_overview
    return render_template('admin/users.html', users=build_overview())


@admin_ui_bp.get('/users/<user_id>')
def user_detail(user_id):
    redirect_resp = _require_admin_ui()
    if redirect_resp:
        return redirect_resp

    from providers import PROVIDER_REGISTRY
    configured = ProviderConfig.query.filter_by(user_id=user_id).all()
    active_grants = {
        g.provider_id: g for g in ProviderGrant.query.filter_by(user_id=user_id)
        .filter(ProviderGrant.revoked_at.is_(None)).all()
    }

    provider_rows = []
    for pid, meta in PROVIDER_REGISTRY.items():
        ungated = pid in Config.UNGATED_PROVIDERS
        granted = active_grants.get(pid)
        provider_rows.append({
            'provider_id': pid,
            'name': meta['name'],
            'ungated': ungated,
            'granted': granted is not None,
            'grant': granted.to_dict() if granted else None,
        })

    is_admin = (user_id == Config.ADMIN_USER_ID)
    token_row = db.session.get(UserAccessToken, user_id)

    return render_template(
        'admin/user_detail.html',
        user_id=user_id,
        is_admin=is_admin,
        provider_rows=provider_rows,
        configured=[r.to_safe_dict() for r in configured],
        token_status=token_row.to_safe_dict() if token_row else None,
        admin_csrf=session.get('admin_csrf', ''),
    )
