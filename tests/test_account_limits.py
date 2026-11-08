import concurrent.futures
import json
import pytest

from fastapi.testclient import TestClient

from auth_helpers import login
from test_server import client, done, remote_http


@pytest.fixture(autouse=True)
def stub_remote(remote_http):
    remote_http()


def fetch(client, match_id):
    return client.post('/api/fetch', json={'matchId': str(match_id)})


def test_fetch_requires_login(client):
    client.cookies.clear()
    response = fetch(client, 1)
    assert response.status_code == 401
    assert response.json() == {'error': 'Войдите через Steam, чтобы обработать матч'}


def test_account_limit_two_per_hour(client):
    login(client)
    assert fetch(client, 1).status_code == 202
    assert fetch(client, 2).status_code == 202
    response = fetch(client, 3)
    assert response.status_code == 429
    assert response.json()['error'].startswith('Лимит: 2 обработки в час, попробуйте через ')
    assert int(response.headers['retry-after']) > 0
    login(client, 123)
    assert fetch(client, 3).status_code == 202


def test_duplicate_does_not_consume_limit(client, monkeypatch):
    login(client)
    path = client.app.state.storage.matches / '1'
    path.mkdir()
    (path / 'match.json').write_text('{}', encoding='utf-8')
    monkeypatch.setenv('MAX_QUEUE', '0')
    assert fetch(client, 1).json() == {'matchId': '1', 'duplicate': True}
    monkeypatch.setenv('MAX_QUEUE', '5')
    assert fetch(client, 2).status_code == 202
    assert fetch(client, 3).status_code == 202
    assert fetch(client, 4).status_code == 429


def test_active_does_not_consume_limit(client, monkeypatch):
    login(client)
    job = client.app.state.jobs.submit('fetch:1', match_id='1')
    monkeypatch.setenv('MAX_QUEUE', '0')
    assert fetch(client, 1).json() == {'job': job}
    with client.app.state.storage.connect() as db:
        assert db.execute('SELECT count(*) FROM account_jobs').fetchone()[0] == 0


def test_limit_survives_restart(client):
    login(client)
    assert fetch(client, 1).status_code == 202
    assert fetch(client, 2).status_code == 202
    from app.server.main import create_app
    with TestClient(create_app()) as restarted:
        login(restarted)
        assert fetch(restarted, 3).status_code == 429


def test_parallel_account_limit(client):
    login(client)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(pool.map(lambda match_id: fetch(client, match_id), [1, 2, 3]))
    assert sorted(r.status_code for r in responses) == [202, 202, 429]


def test_storage_limit_atomic_and_rolling(tmp_path, monkeypatch):
    from app.server.storage import Storage
    now = [10000.0]
    monkeypatch.setattr('app.server.storage.time.time', lambda: now[0])
    stores = [Storage(tmp_path) for _ in range(3)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda storage: storage.take_account(1, 2), stores))
    assert sorted(results) == [0, 0, 3600]
    now[0] += 3600
    assert stores[0].take_account(1, 2) == 0


def test_requested_by_recorded(client, remote_http):
    login(client)
    remote_http()
    result = done(client, fetch(client, 133162474).json()['job'])
    assert result['state'] == 'done'
    meta = client.app.state.storage.matches / '133162474' / 'meta.json'
    assert json.loads(meta.read_text(encoding='utf-8'))['requestedBy'] == 39734273


def test_upload_stays_anonymous(client):
    from test_server import upload, done
    client.cookies.clear()
    assert done(client, upload(client).json()['job'])['state'] == 'done'
