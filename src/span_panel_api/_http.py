"""Shared HTTP helpers for SPAN Panel bootstrap REST calls."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import ssl

import httpx


@dataclass
class _SSLCache:
    """Mutable container for the cached SSLContext and its async lock."""

    context: ssl.SSLContext | None = None
    lock: asyncio.Lock | None = field(default=None, repr=False)

    def get_lock(self) -> asyncio.Lock:
        """Return the async lock, creating it lazily."""
        if self.lock is None:
            self.lock = asyncio.Lock()
        return self.lock


_ssl_cache = _SSLCache()


def _build_url(host: str, port: int, path: str) -> str:
    """Build an HTTP URL, omitting the port when it is the default (80)."""
    if port == 80:
        return f"http://{host}{path}"
    return f"http://{host}:{port}{path}"


async def _create_ssl_context() -> ssl.SSLContext:
    """Return a cached default SSL context, creating it in an executor on first call.

    ``ssl.create_default_context()`` calls ``load_verify_locations`` which
    performs blocking file I/O on the system CA bundle.  The resulting context
    is thread-safe and reusable, so we cache it for the lifetime of the process.
    """
    if _ssl_cache.context is not None:
        return _ssl_cache.context
    async with _ssl_cache.get_lock():
        # Double-check after acquiring the lock.
        if _ssl_cache.context is not None:
            return _ssl_cache.context
        loop = asyncio.get_running_loop()
        _ssl_cache.context = await loop.run_in_executor(None, ssl.create_default_context)
        return _ssl_cache.context


@asynccontextmanager
async def _get_client(
    httpx_client: httpx.AsyncClient | None,
    timeout: float,
) -> AsyncIterator[httpx.AsyncClient]:
    if httpx_client is not None:
        yield httpx_client
        return
    ctx = await _create_ssl_context()
    async with httpx.AsyncClient(timeout=timeout, verify=ctx) as client:
        yield client
