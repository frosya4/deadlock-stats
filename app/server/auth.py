import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

STEAM_BASE = 76561197960265728
STEAM_ENDPOINT = 'https://steamcommunity.com/openid/login'
COOKIE = 'dls_session'
MAX_AGE = 30 * 86400
router = APIRouter()


def load_secret(root):
    configured = os.environ.get('SESSION_SECRET')
    if configured:
        return configured.encode('utf-8')
    path = root / 'session_secret'
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_bytes()
    with os.fdopen(fd, 'wb') as output:
        secret = secrets.token_bytes(32)
        output.write(secret)
    return secret


def session_cookie(account_id, secret):
    payload = base64.urlsafe_b64encode(json.dumps({'a': account_id, 'exp': int(time.time()) + MAX_AGE}).encode()).decode()
    return payload + '.' + hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()


def current_account(request):
    try:
        payload, signature = request.cookies.get(COOKIE, '').split('.')
        expected = hmac.new(request.app.state.session_secret, payload.encode('ascii'), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        data = json.loads(base64.b64decode(payload, altchars=b'-_', validate=True))
        account = data['a']
        if type(account) is int and 0 <= account <= 0xffffffff and type(data['exp']) in (int, float) and data['exp'] > time.time():
            return account
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, binascii.Error):
        pass
    return None


async def steam_verify(params):
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(STEAM_ENDPOINT, data=params | {'openid.mode': 'check_authentication'})
        return response.status_code == 200 and 'is_valid:true' in response.text.splitlines()


def callback_url(request):
    return request.app.state.public_url + '/auth/callback'


@router.get('/auth/login')
def login(request: Request):
    params = {'openid.ns': 'http://specs.openid.net/auth/2.0', 'openid.mode': 'checkid_setup',
              'openid.return_to': callback_url(request), 'openid.realm': request.app.state.public_url + '/',
              'openid.identity': 'http://specs.openid.net/auth/2.0/identifier_select',
              'openid.claimed_id': 'http://specs.openid.net/auth/2.0/identifier_select'}
    return RedirectResponse(STEAM_ENDPOINT + '?' + urlencode(params), status_code=302)


@router.get('/auth/callback')
async def callback(request: Request):
    params = dict(request.query_params)
    claimed = params.get('openid.claimed_id', '')
    valid = (params.get('openid.mode') == 'id_res'
             and params.get('openid.op_endpoint') == STEAM_ENDPOINT
             and re.fullmatch(r'https://steamcommunity\.com/openid/id/[0-9]{17}', claimed)
             and params.get('openid.identity') == claimed
             and {'op_endpoint', 'claimed_id', 'identity', 'return_to'}.issubset(
                 params.get('openid.signed', '').split(','))
             and params.get('openid.return_to') == callback_url(request))
    account = int(claimed.rsplit('/', 1)[-1]) - STEAM_BASE if valid else -1
    if valid and 0 <= account <= 0xffffffff:
        try:
            valid = await request.app.state.steam_verify(params)
        except (httpx.HTTPError, TimeoutError, ValueError):
            valid = False
        if valid:
            response = RedirectResponse(f'/p/{account}', status_code=302)
            response.set_cookie(COOKIE, session_cookie(account, request.app.state.session_secret),
                                max_age=MAX_AGE, httponly=True, secure=request.app.state.cookie_secure,
                                samesite='lax')
            return response
    return JSONResponse({'error': 'Не удалось подтвердить вход через Steam'}, status_code=400)


@router.post('/auth/logout')
def logout(request: Request):
    response = JSONResponse({'ok': True})
    response.delete_cookie(COOKIE, httponly=True, secure=request.app.state.cookie_secure, samesite='lax')
    return response


@router.get('/api/me')
def me(request: Request):
    account = current_account(request)
    return {'accountId': None} if account is None else {'accountId': account, 'steamId': str(STEAM_BASE + account)}


def is_admin(account):
    """Accounts in ADMIN_ACCOUNTS (comma-separated account ids) skip per-user limits."""
    admins = {a.strip() for a in os.environ.get('ADMIN_ACCOUNTS', '').split(',') if a.strip()}
    return account is not None and str(account) in admins
