"""Request body and admission limits; clocks can be replaced by tests."""
import asyncio
import ipaddress
import math
import time
from collections import deque


def client_ip(request):
    peer = request.client.host if request.client else 'unknown'
    try:
        address = ipaddress.ip_address(peer)
        trusted = any(address in network for network in PRIVATE if address.version == network.version)
        if trusted:
            forwarded = request.headers.get('x-forwarded-for', '').split(',')
            # Walk from the trusted proxy towards the first untrusted hop.
            for value in reversed(forwarded):
                address = ipaddress.ip_address(value.strip())
                peer = str(address)
                if not any(address in network for network in PRIVATE if address.version == network.version):
                    break
    except ValueError:
        pass
    return peer


PRIVATE = tuple(map(ipaddress.ip_network, ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8', '::1/128', 'fc00::/7')))


class HourlyLimit:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.entries = {}

    def take(self, key, limit, count=1):
        now = self.clock()
        for old in list(self.entries):
            queue = self.entries[old]
            while queue and queue[0] <= now - 3600:
                queue.popleft()
            if not queue:
                del self.entries[old]
        queue = self.entries.get(key, deque())
        if len(queue) + count > limit:
            return max(1, math.ceil(queue[0] + 3600 - now)) if queue else 3600
        queue.extend([now] * count)
        self.entries[key] = queue
        return 0


async def body_chunks(request, deadline, kbps, grace=30, clock=time.monotonic):
    start = clock()
    size = 0
    iterator = request.stream().__aiter__()
    while True:
        elapsed = clock() - start
        # Wake even if the client sends no further bytes.
        rate_deadline = max(grace, size / (kbps * 1024)) if kbps > 0 else deadline
        remaining = min(deadline, rate_deadline) - elapsed
        if remaining <= 0:
            raise TimeoutError('request body deadline or minimum rate exceeded')
        try:
            async with asyncio.timeout(remaining):
                chunk = await anext(iterator)
        except StopAsyncIteration:
            return
        elapsed = clock() - start
        if elapsed >= deadline or (elapsed >= grace and size < elapsed * kbps * 1024):
            raise TimeoutError('request body deadline or minimum rate exceeded')
        size += len(chunk)
        yield chunk
