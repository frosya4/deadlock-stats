import asyncio
import hashlib
import json
import sqlite3

import pytest
from starlette.requests import Request

from app.server.limits import HourlyLimit, body_chunks, client_ip
from app.server.storage import Storage
from test_server import DEMO, UPLOAD_ID, client, done, remote_http, upload


def test_upload_never_calls_network(client, monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail('An upload must not call upstream')
    monkeypatch.setattr('app.server.jobs.salts', forbidden)
    monkeypatch.setattr('app.server.jobs.download', forbidden)
    result = done(client, upload(client).json()['job'])
    assert result['matchId'] == UPLOAD_ID
    report = client.get('/api/matches/' + UPLOAD_ID).json()
    assert report['match']['verified'] is False
    assert report['match']['claimedMatchId'] == '133162474'
    assert 'official-fetch' in client.get('/m/' + UPLOAD_ID).text


@pytest.mark.parametrize('order', ['fetch-first', 'upload-first'])
def test_same_bytes_have_distinct_identities(client, remote_http, order):
    remote_http()
    def official():
        return done(client, client.post('/api/fetch', json={'matchId': '133162474'}).json()['job'])
    if order == 'fetch-first':
        assert official()['matchId'] == '133162474'
        original = client.get('/api/matches/133162474').content
    result = done(client, upload(client).json()['job'])
    assert result['matchId'] == UPLOAD_ID
    if order == 'upload-first':
        assert official()['matchId'] == '133162474'
        original = client.get('/api/matches/133162474').content
    assert upload(client).json()['matchId'] == UPLOAD_ID
    assert client.get('/api/matches/133162474').content == original
    page = client.get('/m/' + UPLOAD_ID).text
    assert 'Официальная версия: /m/133162474' in page
    assert 'Скачать официальную запись у Valve' in page


@pytest.mark.parametrize('endpoint', ['/api/upload', '/api/fetch'])
@pytest.mark.parametrize('limit_kind', ['deadline', 'rate'])
def test_stalled_body_releases_reservation(client, monkeypatch, endpoint, limit_kind):
    if limit_kind == 'deadline':
        monkeypatch.setenv('UPLOAD_DEADLINE_S', '.02')
    else:
        monkeypatch.setattr('app.server.main.body_chunks',
                            lambda request, deadline, kbps: body_chunks(request, deadline, kbps, grace=.01))
    async def run():
        calls = 0
        async def receive():
            nonlocal calls
            calls += 1
            if calls == 1:
                return {'type': 'http.request', 'body': DEMO if endpoint.endswith('upload') else b'{', 'more_body': True}
            await asyncio.sleep(10)
        sent = []
        async def send(message):
            sent.append(message)
        await client.app({'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
                          'method': 'POST', 'scheme': 'http', 'path': endpoint, 'raw_path': endpoint.encode(),
                          'query_string': b'', 'headers': [(b'content-type', b'application/octet-stream'),
                              (b'cookie', ('dls_session=' + client.cookies.get('dls_session')).encode())],
                          'client': ('8.8.8.8', 1), 'server': ('localhost', 80)}, receive, send)
        assert sent[0]['status'] == 408
    asyncio.run(run())
    assert client.app.state.jobs.inflight == 0
    assert not list(client.app.state.storage.tmp.iterdir())


def test_minimum_rate_with_injectable_clock():
    now = [0]
    class Slow:
        async def stream(self):
            yield DEMO
            now[0] = 31
            yield b'x'
    async def run():
        with pytest.raises(TimeoutError):
            async for _ in body_chunks(Slow(), 900, 64, clock=lambda: now[0]):
                pass
    asyncio.run(run())


def test_silent_body_rate_timer():
    class Silent:
        async def stream(self):
            yield DEMO
            await asyncio.sleep(10)
    async def run():
        with pytest.raises(TimeoutError):
            async for _ in body_chunks(Silent(), 900, 64, grace=.01):
                pass
    asyncio.run(run())


def test_failure_after_body_releases_reservation(client, monkeypatch):
    def fail(digest):
        raise RuntimeError('index unavailable')
    monkeypatch.setattr(client.app.state.storage, 'lookup', fail)
    with pytest.raises(RuntimeError, match='index unavailable'):
        upload(client)
    assert client.app.state.jobs.inflight == 0
    assert not list(client.app.state.storage.tmp.iterdir())


@pytest.mark.parametrize('phase', ['salts', 'download', 'combined'])
def test_absolute_fetch_deadline(client, monkeypatch, phase):
    monkeypatch.setenv('FETCH_DEADLINE_S', '.05')
    async def salts(match_id):
        await asyncio.sleep(.03 if phase == 'combined' else .1 if phase == 'salts' else 0)
        return {'demo_url': 'unused'}
    async def download(url, output, *args, **kwargs):
        output.write(DEMO)
        await asyncio.sleep(.03 if phase == 'combined' else .1)
        return hashlib.sha256(DEMO).hexdigest(), DEMO[:8]
    monkeypatch.setattr('app.server.jobs.salts', salts)
    monkeypatch.setattr('app.server.jobs.download', download)
    result = done(client, client.post('/api/fetch', json={'matchId': '133162474'}).json()['job'])
    assert result == {'state': 'error', 'position': 0, 'error': 'Превышено время скачивания записи'}
    assert not list(client.app.state.storage.tmp.iterdir())
    assert not client.app.state.jobs.active


@pytest.mark.parametrize('peer,xff,expected', [
    ('8.8.8.8', '1.1.1.1', '8.8.8.8'),
    ('172.18.0.2', '1.1.1.1', '1.1.1.1'),
    ('192.168.1.2', '9.9.9.9, 1.1.1.1', '1.1.1.1'),
    ('127.0.0.1', '1.1.1.1, 172.18.0.2', '1.1.1.1'),
    ('::1', '2001:4860:4860::8888', '2001:4860:4860::8888'),
    ('100.64.0.1', '1.1.1.1', '100.64.0.1'),
    ('172.18.0.2', 'invalid', '172.18.0.2'),
])
def test_proxy_trust(peer, xff, expected):
    request = Request({'type': 'http', 'client': (peer, 1), 'headers': [(b'x-forwarded-for', xff.encode())]})
    assert client_ip(request) == expected


def test_hourly_limit_expires():
    now = [0]
    limiter = HourlyLimit(clock=lambda: now[0])
    assert limiter.take('one', 1) == 0
    assert limiter.take('one', 1) == 3600
    assert limiter.take('two', 1) == 0
    now[0] = 3600
    assert limiter.take('one', 1) == 0
    assert list(limiter.entries) == ['one']


def test_per_ip_limit_and_retry_header(client, monkeypatch):
    monkeypatch.setenv('PER_IP_JOBS_PER_HOUR', '1')
    assert upload(client, b'bad').status_code == 400
    response = upload(client)
    assert response.status_code == 429
    assert int(response.headers['retry-after']) > 0
    assert 'IP' in response.json()['error']


def test_upstream_budget(client, monkeypatch, remote_http):
    remote_http()
    monkeypatch.setenv('FETCH_PER_HOUR', '2')
    result = done(client, client.post('/api/fetch', json={'matchId': '133162474'}).json()['job'])
    assert result['state'] == 'done'
    assert client.post('/api/fetch', json={'matchId': '133162474'}).status_code == 200
    response = client.post('/api/fetch', json={'matchId': '133162475'})
    assert response.status_code == 429
    assert int(response.headers['retry-after']) > 0
    assert client.app.state.jobs.inflight == 0


def test_sqlite_pruning_and_storage_size(tmp_path):
    storage = Storage(tmp_path)
    target = storage.matches / UPLOAD_ID
    target.mkdir()
    (target / 'match.json').write_text('{}')
    digest = hashlib.sha256(DEMO).hexdigest()
    storage.record(digest, UPLOAD_ID)
    storage.record(digest, UPLOAD_ID)
    assert storage.lookup(digest) == UPLOAD_ID
    assert storage.size() >= storage.index.stat().st_size + 2
    with sqlite3.connect(storage.index) as db:
        assert db.execute('SELECT count(*) FROM hashes').fetchone()[0] == 1
    (target / 'match.json').unlink()
    assert storage.lookup(digest) is None
    with sqlite3.connect(storage.index) as db:
        assert db.execute('SELECT count(*) FROM hashes').fetchone()[0] == 0
    storage.record(digest, UPLOAD_ID)
    Storage(tmp_path)
    with sqlite3.connect(storage.index) as db:
        assert db.execute('SELECT count(*) FROM hashes').fetchone()[0] == 0
    with pytest.raises(ValueError):
        storage.record(digest, '133162474')
