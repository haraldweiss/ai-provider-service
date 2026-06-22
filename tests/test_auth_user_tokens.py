from storage.user_tokens import issue_user_token


def _headers(token):
    return {'Authorization': f'Bearer {token}'}


def test_user_token_resolves_bound_principal(client, app):
    raw = issue_user_token('lisa')
    response = client.get('/configs/lisa', headers=_headers(raw))
    assert response.status_code == 200


def test_user_token_cannot_assert_another_path_user(client, app):
    raw = issue_user_token('lisa')
    response = client.get('/configs/eve', headers=_headers(raw))
    assert response.status_code == 403
    assert response.get_json()['error'] == 'identity_mismatch'


def test_user_token_cannot_assert_another_json_user(client, app):
    from flask import jsonify
    from api.auth import require_token

    @app.post('/_t/user-token-json')
    @require_token
    def protected():
        return jsonify({'ok': True})

    raw = issue_user_token('lisa')
    response = client.post(
        '/_t/user-token-json', json={'user_id': 'eve'}, headers=_headers(raw),
    )
    assert response.status_code == 403

