import copy
import json

import pytest
from fastapi.testclient import TestClient

from tests.test_pipeline import raw_dir, processed


def test_fixture_enrichment(processed):
    _, data, out = processed
    mvp = next(p for p in data['players'] if p['slot'] == 8)
    assert (mvp['hero'], mvp['rank'], mvp['grade'], mvp['mvp']) == ('Виктор', 1, 'S', True)
    assert data['match']['mvpSlot'] == 8
    assert sorted(p['rank'] for p in data['players']) == list(range(1, 13))
    for p in data['players']:
        assert p['lane']['result'] in {'won', 'even', 'lost'}
        assert sum(p['fantasy']['parts'].values()) == pytest.approx(p['fantasy']['total'], abs=.01)
        assert sum(v for k, v in p['sourceTimeline'][-1].items() if k != 't') == pytest.approx(p['netWorth'], abs=1)
        assert p['hsTimeline'][-1]['hsRate'] == pytest.approx(100 * p['heroHeadshots'] / p['heroHits'])
    for lane in {p['lane']['id'] for p in data['players']}:
        groups = {}
        for p in data['players']:
            if p['lane']['id'] == lane:
                groups.setdefault(p['team'], set()).add(p['lane']['result'])
        assert all(len(results) == 1 for results in groups.values())
        assert sorted(next(iter(results)) for results in groups.values()) in [['lost', 'won'], ['even', 'even']]
    rates = [p['hsTimeline'][-1]['hsRate'] for p in data['players']]
    assert data['match']['hsAverage'] == pytest.approx(sum(rates) / len(rates))


def test_idempotent(processed):
    from app.pipeline.enrich import enrich
    _, original, out = processed
    data = copy.deepcopy(original)
    postmatch = json.loads((out / 'postmatch_raw.json').read_text(encoding='utf-8'))
    before = copy.deepcopy(postmatch)
    assert enrich(data, postmatch) is data
    assert data == original
    assert enrich(data, postmatch) == original
    assert postmatch == before


@pytest.mark.parametrize('raw_available', [True, False])
def test_old_match_api(processed, tmp_path, monkeypatch, raw_available):
    from app.server.main import create_app
    _, data, out = processed
    old = copy.deepcopy(data)
    for p in old['players']:
        if isinstance(p['lane'], dict):
            p['lane'] = p['lane']['id']
        for key in ('fantasy', 'rank', 'grade', 'mvp', 'sourceTimeline', 'hsTimeline'):
            p.pop(key, None)
    for key in ('mvpSlot', 'hsAverage'):
        old['match'].pop(key, None)
    root = tmp_path / 'data'
    folder = root / 'matches' / '133162474'
    folder.mkdir(parents=True)
    (folder / 'match.json').write_text(json.dumps(old), encoding='utf-8')
    if raw_available:
        (folder / 'postmatch_raw.json').write_bytes((out / 'postmatch_raw.json').read_bytes())
    monkeypatch.setenv('DATA_DIR', str(root))
    with TestClient(create_app()) as client:
        response = client.get('/api/matches/133162474')
        assert response.status_code == 200
        assert response.json()['players'][0]['fantasy']
        if raw_available:
            assert response.json() == data
        else:
            assert response.json()["players"][0]["sourceTimeline"] == []
        assert client.get('/api/matches/999').status_code == 404


def test_sparse_and_lane_boundary():
    from app.pipeline.enrich import enrich
    players = [dict(slot=i, teamKey=str(i), lane=1, kills=0, healing=1000, selfHealing=200) for i in range(2)]
    data = {'match': {'midBoss': [{'claimedBy': '0'}]}, 'players': players,
            'playerTimeline': {'0': [{'t': 601, 'netWorth': 900}, {'t': 599, 'netWorth': 116}],
                               '1': [{'t': 599, 'netWorth': 100}]}}
    enrich(data, {})
    assert [p['lane']['result'] for p in players] == ['won', 'even']
    assert players[0]['lane']['at'] == 599
    assert players[0]['fantasy']['parts']['healing'] == .04
    assert players[0]['fantasy']['parts']['midboss'] == 1
    assert data['match']['hsAverage'] is None
    assert all(p['sourceTimeline'] == [] for p in players)


def test_no_future_lane_snapshot():
    from app.pipeline.enrich import enrich
    data = {"players": [{"slot": 0, "team": "amber", "lane": 1}],
            "playerTimeline": {"0": [{"t": 601, "netWorth": 1000}]}}
    enrich(data, {})
    assert data["players"][0]["lane"]["teamNW"] == 0
    assert data["players"][0]["lane"]["result"] == "even"


@pytest.mark.parametrize("own,result", [(115, "even"), (116, "won"), (85, "even"), (84, "lost")])
def test_exact_lane_threshold(own, result):
    from app.pipeline.enrich import enrich
    data = {"players": [{"slot": i, "team": i, "lane": 1} for i in range(2)],
            "playerTimeline": {"0": [{"t": 600, "netWorth": own}],
                               "1": [{"t": 600, "netWorth": 100}]}}
    enrich(data, {})
    assert data["players"][0]["lane"]["result"] == result
