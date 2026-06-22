from database import db


def test_issue_stores_hash_and_resolves_identity(app):
    from storage.models import UserAccessToken
    from storage.user_tokens import issue_user_token, resolve_user_token

    raw = issue_user_token('lisa')
    row = db.session.get(UserAccessToken, 'lisa')

    assert raw.startswith('aips_')
    assert raw not in row.token_hash
    assert row.token_prefix == raw[:12]
    assert resolve_user_token(raw) == ('lisa', row.generation)


def test_rotation_and_revocation_invalidate_tokens(app):
    from storage.user_tokens import (
        issue_user_token, resolve_user_token, revoke_user_token,
    )

    first = issue_user_token('lisa')
    second = issue_user_token('lisa')

    assert resolve_user_token(first) is None
    assert resolve_user_token(second)[0] == 'lisa'
    assert revoke_user_token('lisa') is True
    assert resolve_user_token(second) is None


def test_generation_check_fails_after_rotation(app):
    from storage.user_tokens import (
        is_user_token_generation_active, issue_user_token, resolve_user_token,
    )

    first = issue_user_token('lisa')
    user_id, generation = resolve_user_token(first)
    assert is_user_token_generation_active(user_id, generation) is True

    issue_user_token('lisa')
    assert is_user_token_generation_active(user_id, generation) is False

