"""Shared HTTP helpers for SPAN Panel bootstrap REST calls."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import ssl

import httpx

from .exceptions import SpanPanelValidationError

#: What a bootstrap URL resolves to when the caller names no port. HTTP without a
#: context, HTTPS with one -- so a caller that pins the panel CA and leaves the
#: port alone reaches the right place rather than the plaintext one.
DEFAULT_HTTP_PORT = 80
DEFAULT_HTTPS_PORT = 443


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


def _build_url(host: str, port: int | None, path: str, ssl_context: ssl.SSLContext | None = None) -> str:
    """Build a bootstrap URL, choosing the scheme from whether a CA was supplied.

    ``port`` is ``None`` for "the caller did not say", which is the whole reason
    it is nullable: with a plain ``int = 80`` there is no way to tell an omitted
    port from one the caller deliberately set to 80, and the two need opposite
    answers here.

    An explicit ``port=80`` alongside an ``ssl_context`` is refused rather than
    reinterpreted. It is not a hypothetical combination -- a consumer that
    persisted a port before it pinned a CA produces exactly this on its first
    HTTPS call -- and both readings are defensible: the caller may mean "HTTPS on
    the unusual port 80" or may simply not have migrated the stored value.
    Guessing either way is a security control that silently does something other
    than what it was asked to.

    Raises:
        SpanPanelValidationError: ``ssl_context`` supplied with an explicit port 80.
    """
    if ssl_context is None:
        resolved = DEFAULT_HTTP_PORT if port is None else port
        return f"http://{host}{path}" if resolved == DEFAULT_HTTP_PORT else f"http://{host}:{resolved}{path}"

    if port == DEFAULT_HTTP_PORT:
        raise SpanPanelValidationError(
            f"port={DEFAULT_HTTP_PORT} was passed together with an ssl_context for {host}. "
            f"Port {DEFAULT_HTTP_PORT} is the plaintext default and {DEFAULT_HTTPS_PORT} is the TLS one; "
            "pass the panel's HTTPS port explicitly, or omit port to take the default."
        )
    resolved = DEFAULT_HTTPS_PORT if port is None else port
    return f"https://{host}{path}" if resolved == DEFAULT_HTTPS_PORT else f"https://{host}:{resolved}{path}"


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
    ssl_context: ssl.SSLContext | None = None,
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield the client this call should use, honouring both arguments truthfully.

    **A supplied ``ssl_context`` wins over an injected client, deliberately.**
    httpx fixes ``verify=`` at construction, so a context cannot be applied to a
    client somebody else built. This function used to yield an injected client
    untouched, which meant a caller passing both would have got a plaintext-or-
    system-trust connection while believing it had pinned the panel CA -- a
    security control that appears to be on and is off. The only two honest
    options are to refuse the combination or to build a dedicated client, and
    building one keeps the pin working for the consumer that motivated it: Home
    Assistant injects its shared client at every bootstrap call site.

    The cost is named rather than hidden: these calls lose the shared connection
    pool and the injected client's timeout and header policy, and take this
    function's ``timeout`` instead. Acceptable because every caller here is
    bootstrap -- registration, detection, schema, FQDN, status -- made a handful
    of times per config entry, not a hot path. The injected client is never
    closed by this function on any path; the dedicated one always is.
    """
    if ssl_context is not None:
        async with httpx.AsyncClient(timeout=timeout, verify=ssl_context) as client:
            yield client
        return
    if httpx_client is not None:
        yield httpx_client
        return
    ctx = await _create_ssl_context()
    async with httpx.AsyncClient(timeout=timeout, verify=ctx) as client:
        yield client
