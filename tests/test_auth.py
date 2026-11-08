import base64
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

STEAM_ID = '76561198000000001'
ACCOUNT_ID = 39734273
ORIGIN = 'https://stats.example'


@pytest.fixture
def auth_client(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setenv('PUBLIC_URL', ORIGIN)
    monkeypatch.setenv('SESSION_SECRET', 'test-secret')
    from app.server.main import create_app
    with TestClient(create_app(), base_url=ORIGIN) as client:
        async def verify(params):
            return True
        client.app.state.steam_verify = verify
        yield client


def callback_params(**changes):
    params = {'openid.mode': 'id_res', 'openid.op_endpoint': 'https://steamcommunity.com/openid/login',
              'openid.claimed_id': f'https://steamcommunity.com/openid/id/{STEAM_ID}',
              'openid.identity': f'https://steamcommunity.com/openid/id/{STEAM_ID}',
              'openid.return_to': ORIGIN + '/auth/callback'}
    return params | changes


def test_login_redirects_to_steam(auth_client):
    response = auth_client.get('/auth/login', follow_redirects=False)
    assert response.status_code == 302
    assert response.headers['location'].startswith('https://steamcommunity.com/openid/login?')
    params = parse_qs(urlparse(response.headers['location']).query)
    assert params['openid.return_to'] == [ORIGIN + '/auth/callback']
    assert params['openid.realm'] == [ORIGIN + '/']


def test_callback_valid_sets_cookie(auth_client):
    response = auth_client.get('/auth/callback', params=callback_params(), follow_redirects=False)
    assert response.status_code == 302
    assert response.headers['location'] == f'/p/{ACCOUNT_ID}'
    cookie = response.headers['set-cookie']
    for flag in ('HttpOnly', 'Secure', 'SameSite=lax', 'Max-Age=2592000'):
        assert flag in cookie
    assert auth_client.get('/api/me').json() == {'accountId': ACCOUNT_ID, 'steamId': STEAM_ID}


def test_callback_rejected_when_steam_says_invalid(auth_client):
    async def invalid(params):
        return False
    auth_client.app.state.steam_verify = invalid
    assert auth_client.get('/auth/callback', params=callback_params()).status_code == 400
    assert auth_client.get('/api/me').json() == {'accountId': None}


@pytest.mark.parametrize('field,value', [
    ('openid.claimed_id', 'https://evil.example/76561198000000001'),
    ('openid.claimed_id', 'https://steamcommunity.com/openid/id/123'),
    ('openid.op_endpoint', 'https://evil.example'),
    ('openid.identity', 'https://steamcommunity.com/openid/id/76561198000000002'),
    ('openid.mode', 'cancel'),
])
def test_callback_rejects_bad_claimed_id(auth_client, field, value):
    async def forbidden(params):
        pytest.fail('Malformed assertions must be rejected before verification')
    auth_client.app.state.steam_verify = forbidden
    assert auth_client.get('/auth/callback', params=callback_params(**{field: value})).status_code == 400
    assert 'dls_session' not in auth_client.cookies


def test_callback_rejects_wrong_return_to(auth_client):
    assert auth_client.get('/auth/callback', params=callback_params(**{
        'openid.return_to': 'https://evil.example/auth/callback'})).status_code == 400


def test_tampered_cookie_is_logged_out(auth_client):
    auth_client.get('/auth/callback', params=callback_params(), follow_redirects=False)
    cookie = auth_client.cookies.get('dls_session')
    auth_client.cookies.clear()
    auth_client.cookies.set('dls_session', cookie + 'x')
    assert auth_client.get('/api/me').json() == {'accountId': None}


def test_expired_cookie_is_logged_out(auth_client):
    body = base64.urlsafe_b64encode(json.dumps({'a': ACCOUNT_ID, 'exp': time.time() - 1}).encode()).decode()
    signature = hmac.new(b'test-secret', body.encode(), hashlib.sha256).hexdigest()
    auth_client.cookies.set('dls_session', body + '.' + signature)
    assert auth_client.get('/api/me').json() == {'accountId': None}


@pytest.mark.parametrize('cookie', ['garbage', 'x.y', '..', '%FF.x'])
def test_malformed_cookie_is_logged_out(auth_client, cookie):
    assert auth_client.get('/api/me', headers={'cookie': 'dls_session=' + cookie}).json() == {'accountId': None}


def test_logout_clears(auth_client):
    auth_client.get('/auth/callback', params=callback_params(), follow_redirects=False)
    assert auth_client.post('/auth/logout').status_code == 200
    assert auth_client.get('/api/me').json() == {'accountId': None}
