"""Shared persistence operations for encrypted provider configurations."""

from database import db
from storage.models import ProviderConfig


def save_provider_config(user_id: str, provider_id: str, config_dict: dict) -> ProviderConfig:
    row = ProviderConfig.query.filter_by(
        user_id=user_id, provider_id=provider_id,
    ).first()
    data = dict(config_dict)
    if row and not data.get('api_key'):
        try:
            existing_key = row.get_config().get('api_key')
            if existing_key:
                data['api_key'] = existing_key
        except Exception:
            pass
    if row is None:
        row = ProviderConfig(user_id=user_id, provider_id=provider_id)
        db.session.add(row)
    row.set_config(data)
    return row


def delete_provider_config(user_id: str, provider_id: str) -> bool:
    row = ProviderConfig.query.filter_by(
        user_id=user_id, provider_id=provider_id,
    ).first()
    if row is None:
        return False
    db.session.delete(row)
    db.session.commit()
    return True
