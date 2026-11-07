"""Bounded downloads from trusted API/Valve origins only."""
import asyncio
import bz2
import hashlib
import re
import time
from urllib.parse import urlparse

import httpx
from app.pipeline.errors import DemoError

NOT_FOUND = 'Матч не найден или запись уже удалена Valve'
API_DOWN = 'Сервис deadlock-api недоступен, попробуйте позже или загрузите файл'

async def salts(match_id):
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            response = await client.get(f'https://api.deadlock-api.com/v1/matches/{match_id}/salts')
            if response.status_code in (404, 410):
                raise DemoError(NOT_FOUND)
            response.raise_for_status()
            data = response.json()
            cluster = str(data.get('cluster_id', data.get('cluster', '')))
            if not re.fullmatch(r'[0-9]+', cluster):
                raise ValueError('cluster')
            for kind, extension in [('demo', 'dem'), ('metadata', 'meta')]:
                salt = str(data.get('replay_salt' if kind == 'demo' else 'metadata_salt', ''))
                if not re.fullmatch(r'[0-9]+', salt):
                    raise DemoError(NOT_FOUND)
                data[kind + '_url'] = f'http://replay{cluster}.valve.net/1422450/{match_id}_{salt}.{extension}.bz2'
            return data
    except DemoError:
        raise
    except Exception as exc:
        raise DemoError(API_DOWN) from exc

async def download(url, output, compressed_limit, limit, timeout=30):
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not re.fullmatch(r'replay[0-9]+\.valve\.net', parsed.hostname or ''):
        raise DemoError(NOT_FOUND)
    decoder = None
    zstd_writer = None
    compressed = size = 0
    digest = hashlib.sha256()
    signature = bytearray()
    deadline = time.monotonic() + timeout
    class Sink:
        def write(self, data):
            nonlocal size
            size += len(data)
            if size > limit:
                raise DemoError('Файл слишком большой')
            signature.extend(data[:max(0, 8-len(signature))])
            digest.update(data)
            output.write(data)
            return len(data)
        def flush(self):
            pass
    sink = Sink()
    prefix = bytearray()
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            async with client.stream('GET', url) as response:
                if response.status_code in (404, 410):
                    raise DemoError(NOT_FOUND)
                response.raise_for_status()
                async for chunk in response.aiter_bytes(65536):
                    if time.monotonic() > deadline:
                        raise TimeoutError()
                    compressed += len(chunk)
                    if compressed > compressed_limit:
                        raise DemoError('Скачанный файл слишком большой')
                    if decoder is None and zstd_writer is None:
                        prefix.extend(chunk)
                        if len(prefix) < 4:
                            continue
                        chunk = bytes(prefix)
                        prefix.clear()
                        if chunk[:4] == b'\x28\xb5\x2f\xfd':
                            # Valve also serves zstd frames under the .bz2 extension.
                            import zstandard
                            zstd_writer = zstandard.ZstdDecompressor(max_window_size=8*1024**2).stream_writer(sink, closefd=False)
                        else:
                            decoder = bz2.BZ2Decompressor()
                    if zstd_writer is not None:
                        zstd_writer.write(chunk)
                        await asyncio.sleep(0)
                    else:
                        while True:
                            await asyncio.sleep(0)
                            data = decoder.decompress(chunk, max_length=min(65536, limit - size + 1))
                            chunk = b''
                            sink.write(data)
                            if decoder.eof or decoder.needs_input:
                                break
                        if decoder.eof and decoder.unused_data:
                            raise ValueError('trailing data')
                if zstd_writer is not None:
                    zstd_writer.flush()
                elif decoder is None or not decoder.eof:
                    raise ValueError('truncated bz2')
                await asyncio.sleep(0)
        return digest.hexdigest(), bytes(signature)
    except DemoError:
        raise
    except Exception as exc:
        raise DemoError(NOT_FOUND) from exc
    finally:
        if zstd_writer is not None:
            zstd_writer.close()
