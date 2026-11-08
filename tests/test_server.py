import hashlib
import concurrent.futures
import os
import shlex
import shutil
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from auth_helpers import login

ROOT = Path(__file__).resolve().parents[1]
DEMO = b'PBDEMS2\0' + b'\0' * 8
UPLOAD_ID = 'u' + hashlib.sha256(DEMO).hexdigest()[:12]

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('MAX_UPLOAD_MB', '1')
    monkeypatch.setenv('MIN_FREE_GB', '0')
    fake = tmp_path / 'fake.py'
    fake.write_text('''import gzip, pathlib, shutil, sys, time\ntime.sleep(0.15)\nout = pathlib.Path(sys.argv[2])\nfor name in ("msgs.tsv", "ents.jsonl"):\n    with gzip.open(pathlib.Path(%r) / ("133162474." + name + ".gz"), "rb") as src, (out / name).open("wb") as dst:\n        shutil.copyfileobj(src, dst)\n''' % str(ROOT / 'tests' / 'fixtures'), encoding='utf-8')
    monkeypatch.setenv('DLDUMP_BIN', shlex.join([sys.executable, str(fake)]))
    from app.server.main import create_app
    with TestClient(create_app()) as c:
        login(c)
        yield c


def upload(client, data=DEMO):
    return client.post('/api/upload', content=data, headers={'Content-Type': 'application/octet-stream'})


def done(client, job):
    for _ in range(300):
        result = client.get('/api/jobs/' + job).json()
        if result['state'] in ('done', 'error'):
            return result
        time.sleep(.05)
    pytest.fail('job did not finish')


def test_upload_rejects_non_demo(client):
    response = upload(client, b'not a demo')
    assert response.status_code == 400
    assert response.json() == {'error': 'Это не демка Deadlock'}


def test_upload_too_large(client):
    assert upload(client, DEMO + b'x' * (1024 * 1024)).status_code == 413


def test_full_flow(client):
    response = upload(client)
    assert response.status_code == 202
    result = done(client, response.json()['job'])
    assert result['state'] == 'done', result
    assert result['matchId'] == UPLOAD_ID
    assert any(str(m['matchId']) == UPLOAD_ID for m in client.get('/api/matches').json())
    assert client.get('/api/matches/' + UPLOAD_ID).status_code == 200
    assert client.get('/api/matches/' + UPLOAD_ID + '/files/players.csv').status_code == 200


def test_duplicate_same_hash(client):
    result = done(client, upload(client).json()['job'])
    assert result['state'] == 'done'
    response = upload(client)
    assert response.status_code == 200
    assert response.json() == {'matchId': UPLOAD_ID, 'duplicate': True}


def test_concurrent_duplicate(client):
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: upload(client), range(2)))
    assert all(r.status_code == 202 for r in responses)
    assert responses[0].json()['job'] == responses[1].json()['job']
    assert done(client, responses[0].json()['job'])['state'] == 'done'


def test_parser_failure_cleans_up(client, monkeypatch):
    monkeypatch.setenv('DLDUMP_BIN', shlex.join([sys.executable, '-c', 'import sys; sys.exit(1)']))
    result = done(client, upload(client).json()['job'])
    assert result['state'] == 'error'
    assert result['error'] == 'Не удалось разобрать демку (возможно, вышел патч игры)'
    assert not list((Path(os.environ['DATA_DIR']) / 'tmp').iterdir())


def test_aborted_upload_cleanup(client):
    from app.server.main import save_upload
    import asyncio
    class Broken:
        async def stream(self):
            if not hasattr(self, 'read_once'):
                self.read_once = True
                yield DEMO
            raise ConnectionError('disconnect')
    with pytest.raises(ConnectionError):
        asyncio.run(save_upload(Broken(), Path(os.environ['DATA_DIR']) / 'tmp', 1024))
    assert not list((Path(os.environ['DATA_DIR']) / 'tmp').iterdir())


def test_deleted_match_folder(client):
    result = done(client, upload(client).json()['job'])
    assert result['state'] == 'done'
    shutil.rmtree(Path(os.environ['DATA_DIR']) / 'matches' / str(result['matchId']))
    assert client.get('/api/matches').json() == []
    assert client.get('/api/matches/133162474').status_code == 404
    assert upload(client).status_code == 202


def test_robots(client):
    assert client.get('/robots.txt').text == 'User-agent: *\nDisallow: /'


def test_queue_full_before_body(client, monkeypatch):
    monkeypatch.setenv('MAX_QUEUE', '0')
    assert upload(client).status_code == 429


def test_low_disk_before_body(client, monkeypatch):
    monkeypatch.setenv('MIN_FREE_GB', '99999999')
    assert upload(client).status_code == 507


def test_storage_budget(client, monkeypatch):
    monkeypatch.setenv('MAX_STORAGE_GB', '0')
    assert upload(client).status_code == 507


def test_oversize_cleanup(client):
    assert upload(client, DEMO + b'x' * 1048576).status_code == 413
    assert not list((Path(os.environ['DATA_DIR']) / 'tmp').iterdir())


def test_unverified_cannot_overwrite(client, remote_http):
    import json
    remote_http()
    verified = done(client, client.post('/api/fetch', json={'matchId': '133162474'}).json()['job'])
    assert verified['matchId'] == '133162474'
    target = Path(os.environ['DATA_DIR']) / 'matches' / '133162474' / 'match.json'
    original = target.read_bytes()
    result = done(client, upload(client, DEMO + b'different').json()['job'])
    assert result['matchId'].startswith('u')
    assert target.read_bytes() == original
    assert client.get('/api/matches/' + result['matchId']).status_code == 200
    assert any(m.get('verified') is False for m in client.get('/api/matches').json())


def test_deadline_cleanup(client, monkeypatch):
    monkeypatch.setenv('PARSE_TIMEOUT_S', '.2')
    monkeypatch.setenv('DLDUMP_BIN', shlex.join([sys.executable, '-c', 'import time; time.sleep(30)']))
    assert done(client, upload(client).json()['job'])['state'] == 'error'
    assert not list((Path(os.environ['DATA_DIR']) / 'tmp').iterdir())


def test_startup_cleanup(tmp_path, monkeypatch):
    root = tmp_path / 'startup'
    for directory in ['tmp/raw-old', 'matches/.old.tmp', 'matches/133162474']:
        (root / directory).mkdir(parents=True)
        (root / directory / 'keep').write_text('x')
    monkeypatch.setenv('DATA_DIR', str(root))
    from app.server.main import create_app
    with TestClient(create_app()):
        assert not list((root / 'tmp').iterdir())
        assert not (root / 'matches/.old.tmp').exists()
        assert (root / 'matches/133162474/keep').exists()


@pytest.fixture
def remote_http(monkeypatch):
    import bz2
    import httpx
    from app.server import remote
    real_client = httpx.AsyncClient
    def install(mode='ok', payload=DEMO):
        def handler(request):
            if request.url.host == 'api.deadlock-api.com':
                if mode == 'down':
                    raise httpx.ConnectError('down')
                if mode == 'missing':
                    return httpx.Response(404)
                return httpx.Response(200, json={'cluster_id': 390, 'replay_salt': 1, 'metadata_salt': 2})
            return httpx.Response(200, content=bz2.compress(payload))
        monkeypatch.setattr(remote.httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    return install


def test_fetch_flow(client, remote_http):
    remote_http()
    response = client.post('/api/fetch', json={'matchId': '133162474'})
    assert response.status_code == 202
    result = done(client, response.json()['job'])
    assert result['state'] == 'done', result
    assert result['matchId'] == '133162474'
    assert client.post('/api/fetch', json={'matchId': '133162474'}).json()['duplicate']


@pytest.mark.parametrize('mode,message', [('missing', 'Запись матча ещё не готова у Valve (обычно появляется через 10–60 минут после игры) или уже удалена. Попробуйте позже.'), ('down', 'Сервис deadlock-api недоступен, попробуйте позже или загрузите файл')])
def test_fetch_remote_errors(client, remote_http, mode, message):
    remote_http(mode)
    result = done(client, client.post('/api/fetch', json={'matchId': '133162474'}).json()['job'])
    assert result['state'] == 'error'
    assert result['error'] == message
    assert not list((Path(os.environ['DATA_DIR']) / 'tmp').iterdir())


def test_fetch_decompression_limit(client, remote_http):
    remote_http(payload=DEMO + b'x' * 1048576)
    result = done(client, client.post('/api/fetch', json={'matchId': '133162474'}).json()['job'])
    assert result['state'] == 'error'
    assert not list((Path(os.environ['DATA_DIR']) / 'tmp').iterdir())


def test_inflight_limit_before_body(client):
    client.app.state.jobs.inflight = 2
    try:
        assert upload(client).status_code == 429
    finally:
        client.app.state.jobs.inflight = 0


def test_finished_records_expire(client):
    jobs = client.app.state.jobs
    jobs.jobs['old'] = {'state': 'done', '_finished': time.monotonic() - 3601}
    assert client.get('/api/jobs/old').status_code == 404


def test_raw_output_cap(client, monkeypatch):
    monkeypatch.setenv('MAX_RAW_MB', '1')
    monkeypatch.setenv('DLDUMP_BIN', shlex.join([sys.executable, '-c', 'import pathlib,sys,time; (pathlib.Path(sys.argv[2])/"large").write_bytes(b"x"*2000000); time.sleep(20)']))
    assert done(client, upload(client).json()['job'])['state'] == 'error'
    assert not list((Path(os.environ['DATA_DIR']) / 'tmp').iterdir())


@pytest.mark.skipif(os.name != 'posix', reason='RLIMIT_AS requires Linux; run in Docker')
def test_memory_limit(client, monkeypatch):
    monkeypatch.setenv('PARSE_MEM_MB', '64')
    monkeypatch.setenv('DLDUMP_BIN', shlex.join([sys.executable, '-c', 'x=bytearray(256*1024*1024)']))
    assert done(client, upload(client).json()['job'])['state'] == 'error'
    assert not list((Path(os.environ['DATA_DIR']) / 'tmp').iterdir())


def test_shutdown_cancels_active_and_queued(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'shutdown'))
    monkeypatch.setenv('MIN_FREE_GB', '0')
    monkeypatch.setenv('DLDUMP_BIN', shlex.join([sys.executable, '-c', 'import time; time.sleep(30)']))
    from app.server.main import create_app
    start = time.monotonic()
    with TestClient(create_app()) as c:
        assert upload(c).status_code == 202
        assert upload(c, DEMO + b'other').status_code == 202
        time.sleep(.2)
    assert time.monotonic() - start < 5
    assert not list((Path(os.environ['DATA_DIR']) / 'tmp').iterdir())


def test_zstd_download_limit(monkeypatch):
    import asyncio
    import io
    import httpx
    import zstandard
    from app.server import remote
    from app.pipeline.errors import DemoError
    real = httpx.AsyncClient
    payload = zstandard.ZstdCompressor().compress(DEMO + b'x' * 1048576)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=payload))
    monkeypatch.setattr(remote.httpx, 'AsyncClient', lambda **kw: real(transport=transport, **kw))
    with pytest.raises(DemoError, match='Файл слишком большой'):
        asyncio.run(remote.download('http://replay390.valve.net/test', io.BytesIO(), 300*1024**2, 1024))
