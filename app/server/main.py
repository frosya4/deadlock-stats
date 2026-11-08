import asyncio
import hashlib
import json
import os
import tempfile
import re
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.server.jobs import Jobs
from app.server.limits import HourlyLimit, body_chunks, client_ip
from app.server.storage import Storage
from app.pipeline.enrich import enrich

WEB = Path(__file__).resolve().parents[2] / 'web'


class InvalidDemo(Exception):
    pass


class UploadTooLarge(Exception):
    pass


async def save_upload(upload, directory, limit):
    digest = hashlib.sha256()
    fd, name = tempfile.mkstemp(dir=directory, suffix='.dem')
    path = Path(name)
    size = 0
    signature = bytearray()
    try:
        with os.fdopen(fd, 'wb') as output:
            async for chunk in body_chunks(upload, float(os.environ.get('UPLOAD_DEADLINE_S', '900')),
                                           float(os.environ.get('MIN_UPLOAD_KBPS', '64'))):
                size += len(chunk)
                if size > limit:
                    raise UploadTooLarge()
                if len(signature) < 8:
                    signature.extend(chunk[:8 - len(signature)])
                if len(signature) == 8 and bytes(signature) != b'PBDEMS2\0':
                    raise InvalidDemo()
                digest.update(chunk)
                output.write(chunk)
            if bytes(signature) != b'PBDEMS2\0':
                raise InvalidDemo()
        return path, digest.hexdigest()
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def create_app():
    storage = Storage(Path(os.environ.get('DATA_DIR', '/data')))
    jobs = Jobs(storage)
    per_ip = HourlyLimit()
    upstream = HourlyLimit()

    @asynccontextmanager
    async def lifespan(app):
        storage.cleanup()
        worker = asyncio.create_task(jobs.run())
        try:
            yield
        finally:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
            storage.cleanup()

    app = FastAPI(lifespan=lifespan)

    app.state.jobs = jobs
    app.state.storage = storage
    app.state.per_ip = per_ip
    app.state.upstream = upstream

    def limited(message, retry=60):
        return JSONResponse({'error': message}, status_code=429, headers={'Retry-After': str(retry)})

    def admit(request):
        if jobs.queue.qsize() + jobs.inflight >= int(os.environ.get('MAX_QUEUE', '5')):
            return limited('Очередь заполнена, попробуйте позже')
        if jobs.inflight >= int(os.environ.get('MAX_INFLIGHT_UPLOADS', '2')):
            return limited('Слишком много загрузок, попробуйте позже')
        if shutil.disk_usage(storage.root).free < float(os.environ.get('MIN_FREE_GB', '10')) * 1024**3:
            return JSONResponse({'error': 'Недостаточно свободного места на сервере, попробуйте позже'}, status_code=507)
        if storage.size() >= float(os.environ.get('MAX_STORAGE_GB', '20')) * 1024**3:
            return JSONResponse({'error': 'Хранилище матчей заполнено, попробуйте позже'}, status_code=507)
        retry = per_ip.take(client_ip(request), int(os.environ.get('PER_IP_JOBS_PER_HOUR', '10')))
        if retry:
            return limited('Слишком много задач с вашего IP, попробуйте позже', retry)
        return None

    @app.put('/api/upload')
    @app.post('/api/upload')
    async def upload(request: Request):
        rejection = admit(request)
        if rejection:
            return rejection
        if request.headers.get('content-type', '').split(';')[0] != 'application/octet-stream':
            return JSONResponse({'error': 'Отправьте демку как application/octet-stream'}, status_code=415)
        jobs.inflight += 1
        path = None
        try:
            try:
                path, digest = await save_upload(request, storage.tmp, int(os.environ.get('MAX_UPLOAD_MB', '600')) * 1024**2)
            except TimeoutError:
                return JSONResponse({'error': 'Превышено время загрузки или скорость слишком мала'}, status_code=408)
            except InvalidDemo:
                return JSONResponse({'error': 'Это не демка Deadlock'}, status_code=400)
            except UploadTooLarge:
                return JSONResponse({'error': 'Файл слишком большой'}, status_code=413)
            match_id = storage.lookup(digest)
            if match_id:
                return JSONResponse({'matchId': match_id, 'duplicate': True})
            job = jobs.submit(digest, path)
            path = None  # Ownership passed to the worker.
            return JSONResponse({'job': job}, status_code=202)
        finally:
            jobs.inflight -= 1
            if path:
                path.unlink(missing_ok=True)

    @app.post('/api/fetch')
    async def fetch_match(request: Request):
        rejection = admit(request)
        if rejection:
            return rejection
        jobs.inflight += 1
        try:
            # Bound this small JSON body too; no unbounded request.json().
            body = bytearray()
            async for chunk in body_chunks(request, float(os.environ.get('UPLOAD_DEADLINE_S', '900')),
                                           float(os.environ.get('MIN_UPLOAD_KBPS', '64'))):
                body.extend(chunk)
                if len(body) > 1024:
                    return JSONResponse({'error': 'Неверный номер матча'}, status_code=400)
            try:
                match_id = str(json.loads(body)['matchId'])
                if not re.fullmatch(r'[0-9]{1,20}', match_id):
                    raise ValueError()
            except (ValueError, KeyError, TypeError):
                return JSONResponse({'error': 'Неверный номер матча'}, status_code=400)
            if (storage.matches / match_id / 'match.json').is_file():
                return {'matchId': match_id, 'duplicate': True}
            key = 'fetch:' + match_id
            if key not in jobs.active:
                # One salts call and one Valve download per new fetch.
                retry = upstream.take('fetch', int(os.environ.get('FETCH_PER_HOUR', '60')), count=2)
                if retry:
                    return limited('Лимит скачиваний у Valve исчерпан, попробуйте позже', retry)
            return JSONResponse({'job': jobs.submit(key, match_id=match_id)}, status_code=202)
        except TimeoutError:
            return JSONResponse({'error': 'Превышено время передачи запроса'}, status_code=408)
        finally:
            jobs.inflight -= 1

    @app.get('/api/jobs/{job_id}')
    def job_status(job_id: str):
        result = jobs.status(job_id)
        return result if result is not None else JSONResponse({'error': 'Задача не найдена'}, status_code=404)

    @app.get('/api/matches')
    def matches():
        return storage.summaries()

    def match_file(match_id, name):
        if not re.fullmatch(r'(?:[0-9]+|u[0-9a-f]{12})', match_id) or name not in {'match.json', 'postmatch_raw.json', 'players.csv', 'items.csv', 'deaths.csv', 'chat.csv', 'gold.csv'}:
            return JSONResponse({'error': 'Файл не найден'}, status_code=404)
        path = storage.matches / match_id / name
        if not path.is_file():
            return JSONResponse({'error': 'Файл не найден'}, status_code=404)
        return FileResponse(path)

    @app.get('/api/matches/{match_id}')
    def match(match_id: str):
        response = match_file(match_id, 'match.json')
        if not isinstance(response, FileResponse):
            return response
        path = storage.matches / match_id / 'match.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        required = {'fantasy', 'rank', 'grade', 'mvp', 'sourceTimeline', 'hsTimeline'}
        if any(not required.issubset(p) or not isinstance(p.get('lane'), dict)
               for p in data.get('players', [])):
            raw_path = path.with_name('postmatch_raw.json')
            try:
                postmatch = json.loads(raw_path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                postmatch = {}
            enrich(data, postmatch)
        return data

    @app.get('/api/matches/{match_id}/files/{name}')
    def files(match_id: str, name: str):
        return match_file(match_id, name)

    @app.get('/robots.txt', response_class=PlainTextResponse)
    def robots():
        return 'User-agent: *\nDisallow: /'

    @app.get('/m/{match_id}')
    def match_page(match_id: str):
        path = WEB / 'match.html'
        if not path.is_file():
            return JSONResponse({'error': 'Страница не найдена'}, status_code=404)
        if not re.fullmatch(r'u[0-9a-f]{12}', match_id):
            return FileResponse(path)
        report = storage.matches / match_id / 'match.json'
        try:
            claimed = str(json.loads(report.read_text(encoding='utf-8'))['match']['claimedMatchId'])
        except (OSError, ValueError, KeyError):
            return FileResponse(path)
        if not re.fullmatch(r'[0-9]{1,20}', claimed):
            return FileResponse(path)
        # Only validated numeric IDs enter this server-rendered fragment.
        fragment = '<aside><p>Загружено пользователем · не проверено</p>'
        if (storage.matches / claimed / 'match.json').is_file():
            fragment += f'<p><a href="/m/{claimed}">Официальная версия: /m/{claimed}</a></p>'
        fragment += f'''<button id="official-fetch">Скачать официальную запись у Valve</button>
<p id="official-status" role="status"></p></aside>
<script>
document.querySelector('#official-fetch').addEventListener('click', async function () {{
  this.disabled = true;
  const status = document.querySelector('#official-status');
  try {{
    const response = await fetch('/api/fetch', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{matchId: '{claimed}'}})}});
    let data = await response.json();
    if (!response.ok) throw new Error(data.error);
    while (data.job && data.state !== 'done') {{
      const job = data.job;
      status.textContent = 'Скачивание и обработка официальной записи…';
      await new Promise(resolve => setTimeout(resolve, 1000));
      const result = await fetch('/api/jobs/' + encodeURIComponent(job));
      data = {{...await result.json(), job}};
      if (!result.ok || data.state === 'error') throw new Error(data.error);
    }}
    if (/^[0-9]+$/.test(String(data.matchId))) location.assign('/m/' + data.matchId);
  }} catch (error) {{ status.textContent = error.message || 'Ошибка скачивания'; }}
  finally {{ this.disabled = false; }}
}});
</script>'''
        return HTMLResponse(path.read_text(encoding='utf-8').replace('<div id="report"', fragment + '<div id="report"', 1))

    if WEB.is_dir():
        app.mount('/', StaticFiles(directory=WEB, html=True), name='web')
    return app


app = create_app()
