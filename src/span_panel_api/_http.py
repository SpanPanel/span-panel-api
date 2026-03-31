"""Shared HTTP helpers for SPAN Panel bootstrap REST calls."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import ssl

import httpx


def _build_url(host: str, port: int, path: str) -> str:
    """Build an HTTP URL, omitting the port when it is the default (80)."""
    if port == 80:
        return f"http://{host}{path}"
    return f"http://{host}:{port}{path}"


async def _create_ssl_context() -> ssl.SSLContext:
    """Create a default SSL context in an executor to avoid blocking the event loop.

    ``ssl.create_default_context()`` calls ``load_verify_locations`` which
    performs blocking file I/O on the system CA bundle.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, ssl.create_default_context)


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
