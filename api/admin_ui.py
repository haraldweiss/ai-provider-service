"""Admin UI — Jinja-rendered pages at /admin/ui.

Auth flow:
  1. GET /admin/ui?token=<ADMIN_TOKEN>  → validates, sets session cookie, redirects.
  2. Subsequent navigation uses session['admin']=True.
  3. GET /admin/ui/logout → clears session.

Single-admin scope. No login form posts a password; the URL-token bootstrap
plus a signed Flask session cookie is acceptable for this use case.
"""

from flask import (
    Blueprint, render_template, request, redirect, url_for, session, abort,
    jsonify, current_app,
)
from datetime import datetime, timedelta, timezone
from config import Config
from database import db
from storage.models import ProviderConfig, ProviderGrant, UsageEvent

admin_ui_bp = Blueprint(
    'admin_ui', __name__,
    url_prefix='/admin/ui',
    template_folder='../templates',
)


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

    token = request.args.get('token')
    if token:
        if Config.ADMIN_TOKEN and token == Config.ADMIN_TOKEN:
            session['admin'] = True
            return redirect(request.path)
        return redirect(url_for('admin_ui.login'))


@admin_ui_bp.get('/login')
def login():
    return render_template('admin/login.html')


@admin_ui_bp.get('/logout')
def logout():
    session.pop('admin', None)
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
    return render_template('admin/users.html', users=[])


@admin_ui_bp.get('/users/<user_id>')
def user_detail(user_id):
    redirect_resp = _require_admin_ui()
    if redirect_resp:
        return redirect_resp
    return render_template('admin/user_detail.html', user_id=user_id, user={})
