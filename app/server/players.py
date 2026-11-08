import asyncio
import json
import re
import time
import xml.etree.ElementTree as ET

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.server.auth import STEAM_BASE, current_account, is_admin
from app.server.limits import HourlyLimit

router = APIRouter()
API = 'https://api.deadlock-api.com/v1'
TTL = 300
UPSTREAM_ERRORS = (httpx.HTTPError, TimeoutError, ValueError, TypeError, KeyError, AttributeError, ET.ParseError)


async def http_get(url):
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.get(url)
        return response.status_code, response.content


def account_id(value):
    if not re.fullmatch(r'[0-9]{1,17}', value):
        raise ValueError('Неверный Steam ID')
    number = int(value)
    if len(value) == 17:
        number -= STEAM_BASE
    if not 0 <= number <= 0xffffffff:
        raise ValueError('Неверный Steam ID')
    return number


def integer(row, key):
    value = row[key]
    if type(value) is not int or value < 0:
        raise ValueError('Неверный ответ API')
    return value


def parse_history(body):
    rows = json.loads(body)
    if not isinstance(rows, list):
        raise ValueError('Неверный ответ API')
    result = []
    mapping = {'matchId': 'match_id', 'heroId': 'hero_id', 'startTime': 'start_time',
               'kills': 'player_kills', 'deaths': 'player_deaths', 'assists': 'player_assists',
               'netWorth': 'net_worth', 'durationS': 'match_duration_s'}
    for row in rows:
        item = {out: integer(row, source) for out, source in mapping.items()}
        item['won'] = integer(row, 'match_result') == integer(row, 'player_team')
        result.append(item)
    return sorted(result, key=lambda item: (item['startTime'], item['matchId']), reverse=True)[:30]


def parse_steam(body):
    profile = ET.fromstring(body)
    return profile.findtext('steamID') or '', profile.findtext('avatarMedium') or ''


def parse_remote(body):
    info = json.loads(body)['match_info']
    players = info['players']
    if not isinstance(players, list):
        raise ValueError('Неверный ответ API')
    mapping = {'accountId': 'account_id', 'heroId': 'hero_id', 'team': 'team',
               'kills': 'kills', 'deaths': 'deaths', 'assists': 'assists', 'netWorth': 'net_worth'}
    return {'duration': integer(info, 'duration_s'), 'winningTeam': integer(info, 'winning_team'),
            'players': [{out: integer(player, source) for out, source in mapping.items()} for player in players]}


async def cached(request, url, parse):
    cache = request.app.state.players_cache
    now = time.monotonic()
    for key in list(cache):
        if now - cache[key][0] >= TTL:
            del cache[key]
    if url in cache:
        return cache[url][1]
    async with asyncio.timeout(15):
        status, body = await request.app.state.http_get(url)
    if status != 200:
        raise ValueError('Неверный ответ API')
    value = parse(body)
    cache[url] = (time.monotonic(), value)
    return value


def unavailable():
    return JSONResponse({'error': 'API временно недоступен'}, status_code=503)


@router.get('/api/players/{id}')
async def player(id: str, request: Request):
    try:
        account = account_id(id)
    except ValueError as exc:
        return JSONResponse({'error': str(exc)}, status_code=400)
    try:
        matches = await cached(request, f'{API}/players/{account}/match-history', parse_history)
    except UPSTREAM_ERRORS:
        return unavailable()
    steam = str(STEAM_BASE + account)
    try:
        name, avatar = await cached(request, f'https://steamcommunity.com/profiles/{steam}?xml=1', parse_steam)
    except UPSTREAM_ERRORS:
        name, avatar = '', ''
    storage, jobs = request.app.state.storage, request.app.state.jobs
    result = []
    for match in matches:
        match_id = str(match['matchId'])
        state = ('processed' if (storage.matches / match_id / 'match.json').is_file()
                 else 'queued' if 'fetch:' + match_id in jobs.active else 'available')
        result.append(match | {'state': state})
    return {'accountId': account, 'name': name or steam, 'avatar': avatar, 'matches': result}


@router.post('/api/players/{id}/refresh')
async def refresh(id: str, request: Request):
    """Ask deadlock-api to re-read history from Steam (force_refetch: 1 req/h per our IP)."""
    if current_account(request) is None:
        return JSONResponse({'error': 'Войдите через Steam, чтобы обновить историю'}, status_code=401)
    try:
        account = account_id(id)
    except ValueError as exc:
        return JSONResponse({'error': str(exc)}, status_code=400)
    if not hasattr(request.app.state, 'refresh_limit'):
        request.app.state.refresh_limit = HourlyLimit()
    retry = 0 if is_admin(current_account(request)) else request.app.state.refresh_limit.take('force_refetch', 1)
    if retry:
        return JSONResponse({'error': f'Обновить из Steam можно раз в час на весь сайт, попробуйте через {max(1, retry // 60)} мин'},
                            status_code=429, headers={'Retry-After': str(retry)})
    url = f'{API}/players/{account}/match-history'
    try:
        async with asyncio.timeout(90):
            status, body = await request.app.state.http_get(url + '?force_refetch=true')
        if status == 429:
            return JSONResponse({'error': 'deadlock-api временно ограничил обновления, попробуйте позже'}, status_code=429)
        if status != 200:
            raise ValueError('Неверный ответ API')
        matches = parse_history(body)
    except UPSTREAM_ERRORS:
        return unavailable()
    request.app.state.players_cache[url] = (time.monotonic(), matches)
    return {'ok': True, 'count': len(matches), 'latest': matches[0]['matchId'] if matches else None}


@router.get('/api/remote/{match_id}')
async def remote(match_id: str, request: Request):
    if not re.fullmatch(r'[0-9]{1,20}', match_id):
        return JSONResponse({'error': 'Неверный номер матча'}, status_code=400)
    try:
        result = await cached(request, f'{API}/matches/{int(match_id)}/metadata', parse_remote)
    except UPSTREAM_ERRORS:
        return unavailable()
    ids = ','.join(str(p['accountId']) for p in result['players'])
    try:
        names = await cached(request, f'{API}/players/steam?account_ids={ids}', parse_names) if ids else {}
    except UPSTREAM_ERRORS:
        names = {}
    return result | {'players': [p | {'name': names.get(p['accountId'], '')} for p in result['players']]}


def parse_names(body):
    rows = json.loads(body)
    if not isinstance(rows, list):
        raise ValueError('Неверный ответ API')
    return {row['account_id']: str(row.get('personaname') or '')[:64] for row in rows
            if isinstance(row, dict) and type(row.get('account_id')) is int}


@router.get('/api/search')
def search(q: str = ''):
    query = q.strip()
    if re.fullmatch(r'[0-9]{1,11}', query):
        return {'redirect': f'/m/{int(query)}'}
    if re.fullmatch(r'(?:https?://)?steamcommunity\.com/id/[^/?#]+/?', query):
        return JSONResponse({'error': 'Используйте ссылку вида /profiles/<число>'}, status_code=400)
    profile = re.fullmatch(r'(?:https?://)?steamcommunity\.com/profiles/([0-9]{17})/?', query)
    value = profile.group(1) if profile else query
    try:
        if not re.fullmatch(r'[0-9]{17}', value):
            raise ValueError()
        return {'redirect': f'/p/{account_id(value)}'}
    except ValueError:
        return JSONResponse({'error': 'Введите номер матча, Steam ID или ссылку на профиль Steam'}, status_code=400)
