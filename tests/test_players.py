import json

import httpx
import pytest
from fastapi.testclient import TestClient

ACCOUNT = 39734273
STEAM = '76561198000000001'


def history(match_id=1, **changes):
    return dict(match_id=match_id, hero_id=6, start_time=1700000000 + match_id,
                player_kills=10, player_deaths=2, player_assists=4, net_worth=25000,
                match_duration_s=1800, match_result=0, player_team=0) | changes


@pytest.fixture
def player_client(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    from app.server.main import create_app
    with TestClient(create_app()) as client:
        calls = []
        async def get(url):
            calls.append(url)
            if 'steamcommunity' in url:
                return 200, '<profile><steamID>Игрок</steamID><avatarMedium>https://cdn.example/a.jpg</avatarMedium></profile>'.encode()
            return 200, json.dumps([history(1), history(2), history(3, match_result=1)]).encode()
        client.app.state.http_get = get
        yield client, calls


def test_profile_shape_and_states(player_client):
    client, calls = player_client
    path = client.app.state.storage.matches / '3'
    path.mkdir()
    (path / 'match.json').write_text('{}', encoding='utf-8')
    client.app.state.jobs.active['fetch:2'] = 'queued-job'
    response = client.get(f'/api/players/{STEAM}')
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {'accountId', 'name', 'avatar', 'matches'}
    assert data['accountId'] == ACCOUNT
    assert data['name'] == 'Игрок'
    assert data['avatar'] == 'https://cdn.example/a.jpg'
    assert [m['state'] for m in data['matches']] == ['processed', 'queued', 'available']
    assert data['matches'][-1] == dict(matchId=1, heroId=6, startTime=1700000001,
                                      kills=10, deaths=2, assists=4, netWorth=25000,
                                      durationS=1800, won=True, state='available')
    assert data['matches'][0]['won'] is False
    assert f'https://api.deadlock-api.com/v1/players/{ACCOUNT}/match-history' in calls


def test_cache_and_dynamic_states(player_client):
    client, calls = player_client
    assert client.get(f'/api/players/{ACCOUNT}').status_code == 200
    client.app.state.jobs.active['fetch:1'] = 'queued-job'
    assert client.get(f'/api/players/{STEAM}').json()['matches'][-1]['state'] == 'queued'
    assert len(calls) == 2  # one history + one Steam XML, shared across both ID forms


def test_cache_expiry_and_last_thirty(player_client, monkeypatch):
    client, calls = player_client
    now = [1000.0]
    monkeypatch.setattr('app.server.players.time.monotonic', lambda: now[0])
    async def get(url):
        calls.append(url)
        if 'steamcommunity' in url:
            return 200, b'<profile/>'
        return 200, json.dumps([history(i) for i in range(1, 36)]).encode()
    client.app.state.http_get = get
    matches = client.get(f'/api/players/{ACCOUNT}').json()['matches']
    assert len(matches) == 30
    assert matches[0]['matchId'] == 35 and matches[-1]['matchId'] == 6
    now[0] += 299
    assert client.get(f'/api/players/{ACCOUNT}').status_code == 200
    assert len(calls) == 2
    now[0] += 1
    assert client.get(f'/api/players/{ACCOUNT}').status_code == 200
    assert len(calls) == 4


@pytest.mark.parametrize('failure', ['status', 'network', 'json', 'shape', 'row', 'type'])
@pytest.mark.parametrize('route', [f'/api/players/{ACCOUNT}', '/api/remote/1'])
def test_upstream_error_is_503(player_client, failure, route):
    client, _ = player_client
    async def get(url):
        if failure == 'network':
            raise httpx.ConnectError('offline')
        payload = {'json': b'not json', 'shape': b'{}', 'row': b'[null]',
                   'type': json.dumps([history(hero_id={})]).encode()}.get(failure, b'[]')
        return (502 if failure == 'status' else 200), payload
    client.app.state.http_get = get
    response = client.get(route)
    assert response.status_code == 503
    assert response.json() == {'error': 'API временно недоступен'}
    assert client.get('/api/matches').status_code == 200


@pytest.mark.parametrize('payload', [b'<profile/>', b'bad xml', None])
def test_steam_profile_fallback(player_client, payload):
    client, _ = player_client
    async def get(url):
        if 'steamcommunity' in url:
            if payload is None:
                raise httpx.ReadTimeout('timeout')
            return 200, payload
        return 200, b'[]'
    client.app.state.http_get = get
    assert client.get(f'/api/players/{ACCOUNT}').json() == {
        'accountId': ACCOUNT, 'name': STEAM, 'avatar': '', 'matches': []}


@pytest.mark.parametrize('value', ['abc', '-1', '4294967296', '76561197960265727', '9' * 100])
def test_invalid_player_id(player_client, value):
    client, calls = player_client
    assert client.get('/api/players/' + value).status_code == 400
    assert not calls


def test_remote_shape_and_cache(player_client):
    client, calls = player_client
    async def get(url):
        calls.append(url)
        if '/players/steam' in url:
            return 200, json.dumps([{'account_id': ACCOUNT, 'personaname': 'Игрок'}]).encode()
        return 200, json.dumps({'match_info': {'duration_s': 1800, 'winning_team': 1,
            'players': [{'account_id': ACCOUNT, 'hero_id': 6, 'team': 0, 'kills': 3,
                         'deaths': 2, 'assists': 1, 'net_worth': 20000, 'ignore': 42}]}, 'ignore': 42}).encode()
    client.app.state.http_get = get
    expected = {'duration': 1800, 'winningTeam': 1, 'players': [
        {'accountId': ACCOUNT, 'heroId': 6, 'team': 0, 'kills': 3,
         'deaths': 2, 'assists': 1, 'netWorth': 20000, 'name': 'Игрок'}]}
    assert client.get('/api/remote/1').json() == expected
    assert client.get('/api/remote/1').json() == expected
    assert calls == ['https://api.deadlock-api.com/v1/matches/1/metadata',
                     f'https://api.deadlock-api.com/v1/players/steam?account_ids={ACCOUNT}']


@pytest.mark.parametrize('value', ['abc', '-1', '9' * 21])
def test_invalid_remote_id(player_client, value):
    client, calls = player_client
    assert client.get('/api/remote/' + value).status_code == 400
    assert not calls


@pytest.mark.parametrize('query,redirect', [
    ('133162474', '/m/133162474'), ('12345678901', '/m/12345678901'),
    (STEAM, f'/p/{ACCOUNT}'),
    (f'https://steamcommunity.com/profiles/{STEAM}', f'/p/{ACCOUNT}'),
    (f'steamcommunity.com/profiles/{STEAM}/', f'/p/{ACCOUNT}'),
])
def test_search_redirects(player_client, query, redirect):
    client, calls = player_client
    assert client.get('/api/search', params={'q': query}).json() == {'redirect': redirect}
    assert not calls


def test_search_vanity(player_client):
    client, _ = player_client
    response = client.get('/api/search', params={'q': 'https://steamcommunity.com/id/someone'})
    assert response.status_code == 400
    assert response.json() == {'error': 'Используйте ссылку вида /profiles/<число>'}


@pytest.mark.parametrize('query', ['', 'abc', '123456789012', 'https://evil.example/profiles/' + STEAM,
                                  '99999999999999999'])
def test_search_invalid(player_client, query):
    client, _ = player_client
    assert client.get('/api/search', params={'q': query}).status_code == 400


def test_profile_page_route(player_client, monkeypatch, tmp_path):
    client, _ = player_client
    # The page itself belongs to Task 4; verify routing with an isolated file.
    (tmp_path / 'profile.html').write_text('<html>Профиль</html>', encoding='utf-8')
    monkeypatch.setattr('app.server.main.WEB', tmp_path)
    assert client.get(f'/p/{ACCOUNT}').text == '<html>Профиль</html>'


def test_refresh_requires_login_and_is_limited(player_client):
    from auth_helpers import login
    client, calls = player_client
    assert client.post(f'/api/players/{ACCOUNT}/refresh').status_code == 401
    login(client, ACCOUNT)
    client.get(f'/api/players/{ACCOUNT}')
    response = client.post(f'/api/players/{ACCOUNT}/refresh')
    assert response.status_code == 200 and response.json()['count'] == 3
    assert any('force_refetch=true' in url for url in calls)
    second = client.post(f'/api/players/{ACCOUNT}/refresh')
    assert second.status_code == 429 and 'раз в час' in second.json()['error']


def test_refresh_upstream_failure(player_client):
    from auth_helpers import login
    client, _ = player_client
    login(client, ACCOUNT)
    async def down(url):
        return 500, b''
    client.app.state.http_get = down
    assert client.post(f'/api/players/{ACCOUNT}/refresh').status_code == 503
