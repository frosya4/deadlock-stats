import asyncio
import json
import os
import shlex
import shutil
import signal
import sys
import tempfile
import time
import uuid
from pathlib import Path

from app.pipeline.errors import DemoError
from app.server.remote import download, salts

PARSER_ERROR = 'Не удалось разобрать демку (возможно, вышел патч игры)'

def directory_size(path):
    return sum(p.stat().st_size for p in path.rglob('*') if p.is_file())

def read_header(path):
    try:
        from app.proto.demo_pb2 import CDemoFileHeader
        with path.open('rb') as stream:
            if stream.read(8) != b'PBDEMS2\0' or len(stream.read(8)) != 8:
                return {}
            def varint():
                value = 0
                for shift in range(0, 70, 7):
                    byte = stream.read(1)
                    if not byte:
                        raise ValueError('truncated varint')
                    value |= (byte[0] & 127) << shift
                    if not byte[0] & 128:
                        return value
                raise ValueError('invalid varint')
            command, tick, size = varint(), varint(), varint()
            if command != 1 or size > 1024 * 1024:
                return {}
            payload = stream.read(size)
            if len(payload) != size:
                return {}
            header = CDemoFileHeader.FromString(payload)
            return {'server_name': header.server_name, 'game_directory': header.game_directory}
    except Exception:
        return {}


class Jobs:
    def __init__(self, storage):
        self.storage = storage
        self.jobs = {}
        self.active = {}
        self.queue = asyncio.Queue()
        self.inflight = 0

    def expire(self):
        for key, job in list(self.jobs.items()):
            if job.get('_finished', float('inf')) < time.monotonic() - 3600:
                del self.jobs[key]

    def submit(self, digest, path=None, match_id=None):
        self.expire()
        if digest in self.active:
            if path:
                path.unlink(missing_ok=True)
            return self.active[digest]
        job_id = uuid.uuid4().hex
        self.jobs[job_id] = {'state': 'queued', 'position': 0}
        self.active[digest] = job_id
        self.queue.put_nowait((job_id, digest, path, match_id))
        return job_id

    def status(self, job_id):
        self.expire()
        if job_id not in self.jobs:
            return None
        result = {k: v for k, v in self.jobs[job_id].items() if not k.startswith('_')}
        if result['state'] == 'queued':
            result['position'] = next((i for i, item in enumerate(self.queue._queue, 1) if item[0] == job_id), 0)
        return result

    async def run(self):
        while True:
            job_id, key, path, requested_id = await self.queue.get()
            job = self.jobs[job_id]
            job.update(state='processing', position=0)
            try:
                digest = key
                if requested_id:
                    async with asyncio.timeout(float(os.environ.get('FETCH_DEADLINE_S', '300'))):
                        data = await salts(requested_id)
                        path = self.storage.tmp / (uuid.uuid4().hex + '.dem')
                        with path.open('wb') as output:
                            digest, magic = await download(data['demo_url'], output, 300*1024**2,
                                int(os.environ.get('MAX_UPLOAD_MB', '600'))*1024**2,
                                timeout=float(os.environ.get('FETCH_DEADLINE_S', '300')))
                    if magic != b'PBDEMS2\0':
                        raise DemoError('Это не демка Deadlock')
                match_id = await self._process(path, digest, requested_id)
                job.update(state='done', matchId=match_id)
            except TimeoutError:
                job.update(state='error', error='Превышено время скачивания записи')
            except DemoError as exc:
                job.update(state='error', error=exc.user_message)
            except Exception:
                job.update(state='error', error=PARSER_ERROR)
            finally:
                if path:
                    path.unlink(missing_ok=True)
                job['_finished'] = time.monotonic()
                self.active.pop(key, None)
                self.queue.task_done()

    async def child(self, command, deadline, watched):
        child = await asyncio.create_subprocess_exec(sys.executable, '-m', 'app.server.sandbox', *command,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            start_new_session=(os.name == 'posix'), cwd=Path(__file__).resolve().parents[2])
        try:
            while child.returncode is None:
                if time.monotonic() > deadline or directory_size(watched) > int(os.environ.get('MAX_RAW_MB', '1024'))*1024**2:
                    raise DemoError(PARSER_ERROR)
                try:
                    await asyncio.wait_for(child.wait(), .1)
                except asyncio.TimeoutError:
                    pass
            if child.returncode or directory_size(watched) > int(os.environ.get('MAX_RAW_MB', '1024'))*1024**2:
                raise DemoError(PARSER_ERROR)
        finally:
            if os.name == 'posix':
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif child.returncode is None:
                killer = await asyncio.create_subprocess_exec('taskkill', '/PID', str(child.pid), '/T', '/F', stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                await killer.wait()
            await child.wait()

    async def _process(self, path, digest, requested_id=None):
        deadline = time.monotonic() + float(os.environ.get('PARSE_TIMEOUT_S', '240'))
        with tempfile.TemporaryDirectory(dir=self.storage.tmp, prefix='raw-') as raw:
            command = shlex.split(os.environ.get('DLDUMP_BIN', '/usr/local/bin/dldump'))
            await self.child(command + [str(path), raw], deadline, Path(raw))
            staging = self.storage.matches / ('.' + uuid.uuid4().hex + '.tmp')
            staging.mkdir()
            try:
                try:
                    await self.child([sys.executable, '-m', 'app.server.pipeline_child', raw, str(path), str(staging)], deadline, staging)
                except DemoError:
                    if (staging / 'error.json').exists():
                        raise DemoError(json.loads((staging / 'error.json').read_text(encoding='utf-8')))
                    raise
                summary = json.loads((staging / 'summary.json').read_text(encoding='utf-8'))
                claimed = str(summary['matchId'])
                if requested_id and claimed != requested_id:
                    raise DemoError(PARSER_ERROR)
                verified = bool(requested_id)
                match_id = requested_id if verified else 'u' + digest[:12]
                identity = dict(matchId=match_id, verified=verified)
                if not verified and claimed.isascii() and claimed.isdecimal():
                    identity['claimedMatchId'] = claimed
                summary.update(identity)
                report_path = staging / 'match.json'
                report = json.loads(report_path.read_text(encoding='utf-8'))
                report['match'].update(identity)
                report_path.write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
                (staging / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False), encoding='utf-8')
                target = self.storage.matches / match_id
                if not target.exists():
                    if self.storage.size() > float(os.environ.get('MAX_STORAGE_GB', '20'))*1024**3:
                        raise DemoError('Хранилище матчей заполнено, попробуйте позже')
                    staging.rename(target)
                if not verified:
                    self.storage.record(digest, match_id)
                return match_id
            finally:
                shutil.rmtree(staging, ignore_errors=True)
