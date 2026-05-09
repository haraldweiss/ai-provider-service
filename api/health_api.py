"""/health Endpoint: Service-Status + alle Provider-Health."""

from flask import Blueprint, jsonify
import health_tracker
from providers import PROVIDER_REGISTRY

health_bp = Blueprint('health', __name__)


@health_bp.get('/health')
def health():
    """Public Health-Endpoint (kein Token nötig — für Monitoring)."""
    statuses = health_tracker.all_status()
    return jsonify({
        'service': 'ai-provider-service',
        'status': 'ok',
        'providers': {
            pid: {
                **statuses.get(pid, {'healthy': None, 'reason': 'not_checked'}),
                'name': meta['name'],
            }
            for pid, meta in PROVIDER_REGISTRY.items()
        }
    })
