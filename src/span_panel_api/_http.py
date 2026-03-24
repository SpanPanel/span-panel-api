"""Shared HTTP helpers for SPAN Panel bootstrap REST calls."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx


def _build_url(host: str, port: int, path: str) -> str:
    """Build an HTTP URL, omitting the port when it is the default (80)."""
    if port == 80:
        return f"http://{host}{path}"
    return f"http://{host}:{port}{path}"


@asynccontextmanager
async def _get_client(
    httpx_client: httpx.AsyncClient | None,
    timeout: float,
) -> AsyncIterator[httpx.AsyncClient]:
    if httpx_client is not None:
        yield httpx_client
        return
    async with httpx.AsyncClient(timeout=timeout, verify=False) as client:  # nosec B501
        yield client
