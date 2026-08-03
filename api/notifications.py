"""Notification service for admin alerts (email + in-app)."""

import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
from typing import Optional
from flask import current_app

logger = logging.getLogger(__name__)


class NotificationService:
    """Sends admin notifications via email and stores in-app."""

    def __init__(self, app=None):
        self.app = app
        if app:
            self.init_app(app)

    def _create_in_app(self, type_: str, user_id: str, title: str, message: str, provider_id: str = None):
        """Create in-app admin notification."""
        try:
            from storage.models import AdminNotification, db
            notif = AdminNotification(
                type=type_,
                user_id=user_id,
                provider_id=provider_id,
                title=title,
                message=message,
            )
            db.session.add(notif)
            db.session.commit()
        except Exception as e:
            logger.warning(f"Failed to create in-app notification: {e}")

    def notify_grant_request(self, user_id: str, provider_id: str, reason: str = None,
                             admin_token: str = None, admin_ui_url: str = None):
        """Send notification for a new grant request."""
        subject = f"[ai-provider] New Grant Request: {user_id} → {provider_id}"
        body = f"""
New provider access request received:

User: {user_id}
Provider: {provider_id}
Reason: {reason or '(none)'}
Time: {datetime.now(timezone.utc).isoformat()}

Approve via admin API:
POST /admin/grants
{{"user_id": "{user_id}", "provider_id": "{provider_id}"}}

Or via Admin UI:
{admin_ui_url or 'https://ai-admin.wolfinisoftware.de/admin/ui'}

---
ai-provider-service notification
"""
        if self.app.config.get('NOTIFY_ON_GRANT_REQUEST', True):
            self._send_email(subject, body)

        # In-app notification
        self._create_in_app(
            type_='grant_request',
            user_id=user_id,
            provider_id=provider_id,
            title=f"Neuer Zugriffsantrag: {provider_id}",
            message=f"User {user_id} beantragt Zugriff auf {provider_id}. Grund: {reason or '(keiner)'}",
        )

    def notify_user_registration(self, user_id: str, admin_token: str = None,
                                  admin_ui_url: str = None):
        """Send notification for a new user registration."""
        subject = f"[ai-provider] New User Registered: {user_id}"
        body = f"""
New user registered in ai-provider-service:

User ID: {user_id}
Time: {datetime.now(timezone.utc).isoformat()}

View in Admin UI:
{admin_ui_url or 'https://ai-admin.wolfinisoftware.de/admin/ui'}

---
ai-provider-service notification
"""
        if self.app.config.get('NOTIFY_ON_USER_REGISTER', True):
            self._send_email(subject, body)

        # In-app notification
        self._create_in_app(
            type_='user_registered',
            user_id=user_id,
            title=f"Neuer Benutzer: {user_id}",
            message=f"Benutzer {user_id} hat sich registriert.",
        )

    def notify_token_issued(self, user_id: str, admin_ui_url: str = None):
        """Send notification when a user access token is issued."""
        subject = f"[ai-provider] Access Token Issued for {user_id}"
        body = f"""
Access token issued for user:

User ID: {user_id}
Time: {datetime.now(timezone.utc).isoformat()}

View in Admin UI:
{admin_ui_url or 'https://ai-admin.wolfinisoftware.de/admin/ui'}

---
ai-provider-service notification
"""
        self._send_email(subject, body)

        # In-app notification
        self._create_in_app(
            type_='token_issued',
            user_id=user_id,
            title=f"Token ausgestellt: {user_id}",
            message=f"Für Benutzer {user_id} wurde ein Access-Token ausgestellt.",
        )

    def init_app(self, app):
        self.app = app

    def _send_email(self, subject: str, body: str, to_email: Optional[str] = None) -> bool:
        """Send email via SMTP. Returns True on success."""
        if not self.app:
            return False

        smtp_host = self.app.config.get('SMTP_HOST')
        smtp_port = self.app.config.get('SMTP_PORT', 587)
        smtp_user = self.app.config.get('SMTP_USER')
        smtp_password = self.app.config.get('SMTP_PASSWORD')
        smtp_from = self.app.config.get('SMTP_FROM', smtp_user)
        smtp_tls = self.app.config.get('SMTP_TLS', True)
        admin_email = to_email or self.app.config.get('ADMIN_EMAIL')

        if not all([smtp_host, smtp_user, smtp_password, admin_email]):
            logger.warning("Email not configured — skipping notification")
            return False

        try:
            msg = MIMEMultipart()
            msg['From'] = smtp_from
            msg['To'] = admin_email
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain', 'utf-8'))

            with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
                if smtp_tls:
                    server.starttls()
                server.login(smtp_user, smtp_password)
                server.send_message(msg)

            logger.info(f"Notification email sent to {admin_email}: {subject}")
            return True

        except Exception as e:
            logger.error(f"Failed to send notification email: {e}")
            return False

    def notify_grant_request(self, user_id: str, provider_id: str, reason: str = None,
                             admin_token: str = None, admin_ui_url: str = None):
        """Send notification for a new grant request."""
        subject = f"[ai-provider] New Grant Request: {user_id} → {provider_id}"
        body = f"""
New provider access request received:

User: {user_id}
Provider: {provider_id}
Reason: {reason or '(none)'}
Time: {datetime.now(timezone.utc).isoformat()}

Approve via admin API:
POST /admin/grants
{{"user_id": "{user_id}", "provider_id": "{provider_id}"}}

Or via Admin UI:
{admin_ui_url or 'https://ai-admin.wolfinisoftware.de/admin/ui'}

---
ai-provider-service notification
"""
        if self.app.config.get('NOTIFY_ON_GRANT_REQUEST', True):
            self._send_email(subject, body)

    def notify_user_registration(self, user_id: str, admin_token: str = None,
                                  admin_ui_url: str = None):
        """Send notification for a new user registration."""
        subject = f"[ai-provider] New User Registered: {user_id}"
        body = f"""
New user registered in ai-provider-service:

User ID: {user_id}
Time: {datetime.now(timezone.utc).isoformat()}

View in Admin UI:
{admin_ui_url or 'https://ai-admin.wolfinisoftware.de/admin/ui'}

---
ai-provider-service notification
"""
        if self.app.config.get('NOTIFY_ON_USER_REGISTER', True):
            self._send_email(subject, body)

    def notify_token_issued(self, user_id: str, admin_ui_url: str = None):
        """Send notification when a user access token is issued."""
        subject = f"[ai-provider] Access Token Issued for {user_id}"
        body = f"""
Access token issued for user:

User ID: {user_id}
Time: {datetime.now(timezone.utc).isoformat()}

View in Admin UI:
{admin_ui_url or 'https://ai-admin.wolfinisoftware.de/admin/ui'}

---
ai-provider-service notification
"""
        self._send_email(subject, body)


# Singleton instance
_notification_service: Optional[NotificationService] = None


def get_notification_service() -> Optional[NotificationService]:
    global _notification_service
    return _notification_service


def init_notification_service(app) -> NotificationService:
    global _notification_service
    _notification_service = NotificationService(app)
    return _notification_service
