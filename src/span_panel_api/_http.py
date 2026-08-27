"""Shared HTTP helpers for SPAN Panel bootstrap REST calls."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import logging
import ssl
from typing import Literal

import httpx

from .exceptions import SpanPanelAPIError, SpanPanelConnectionError, SpanPanelTimeoutError, SpanPanelValidationError

_LOGGER = logging.getLogger(__name__)

#: What a bootstrap URL resolves to when the caller names no port. HTTP without a
#: context, HTTPS with one -- so a caller that pins the panel CA and leaves the
#: port alone reaches the right place rather than the plaintext one.
DEFAULT_HTTP_PORT = 80
DEFAULT_HTTPS_PORT = 443

#: The one bootstrap path two modules request: the detector probes it to decide
#: whether the panel speaks v2 at all, and `get_v2_status` reads the same answer
#: for a caller that already knows it does. Named here rather than spelled out in
#: each, so the two cannot drift apart the way their parsers had.
V2_STATUS_PATH = "/api/v2/status"

#: The verbs the bootstrap API uses. Spelled as a `Literal` rather than passed
#: through to `client.request()` so the dispatch below stays exhaustive and each
#: call still reaches the named httpx method.
type _Method = Literal["GET", "POST", "PUT", "DELETE"]


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


@dataclass(frozen=True, slots=True)
class _Reply:
    """One panel answer, with the decoding every caller of it needs.

    Status classification stays with the caller, because it is genuinely
    per-endpoint: 412 means "no passphrase is set" on the rotation path and
    nothing anywhere else, and 404 means "no FQDN configured" on one call and
    "not a v2 panel" on another. The two steps *around* that classification are
    the same everywhere and had been written out once per endpoint -- translating
    a failed connection, and turning a body into an object with the fields the
    caller is about to read. Both live here.
    """

    host: str
    endpoint: str
    response: httpx.Response

    @property
    def status_code(self) -> int:
        """The status the panel answered with."""
        return self.response.status_code

    @property
    def text(self) -> str:
        """The body as text, for the one endpoint that answers with a PEM."""
        return self.response.text

    @property
    def headers(self) -> httpx.Headers:
        """The response headers, for ``Retry-After`` and content-type."""
        return self.response.headers

    def json_object(self, *required: str, on_malformed: type[SpanPanelAPIError] = SpanPanelAPIError) -> dict[str, object]:
        """Decode the body as a JSON object and confirm the fields about to be read.

        A 200 is not a promise of a body. A panel part-way through starting
        answers one with nothing in it; a proxy in front of one answers with an
        HTML error page under a 200; and firmware is free to omit a field this
        library treats as mandatory. Untranslated those surfaced as
        ``JSONDecodeError`` and ``KeyError`` -- neither of them a
        ``SpanPanelError``, so neither caught by a caller holding this library's
        contract, and both escaping the retry clauses built on it.

        ``on_malformed`` exists for the one endpoint where an unreadable body is
        "not ready yet" rather than "wrong": the schema fetch, whose caller
        retries a booting panel. Every other endpoint here is asked once.
        """
        try:
            parsed = self.response.json()
        except ValueError as exc:
            raise on_malformed(
                f"{self.host} answered HTTP {self.status_code} for {self.endpoint} with a body that is not JSON",
                status_code=self.status_code,
            ) from exc
        if not isinstance(parsed, dict):
            raise on_malformed(
                f"{self.host} answered HTTP {self.status_code} for {self.endpoint} "
                f"with {type(parsed).__name__}, not a JSON object",
                status_code=self.status_code,
            )
        body: dict[str, object] = parsed
        missing = sorted(key for key in required if key not in body)
        if missing:
            raise on_malformed(
                f"{self.host} answered HTTP {self.status_code} for {self.endpoint} "
                f"without the required field(s) {', '.join(missing)}",
                status_code=self.status_code,
            )
        return body


async def _request(
    method: _Method,
    host: str,
    port: int | None,
    path: str,
    *,
    timeout: float,
    httpx_client: httpx.AsyncClient | None = None,
    ssl_context: ssl.SSLContext | None = None,
    json: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> _Reply:
    """Make one bootstrap request and translate everything that is not an answer.

    The whole ``httpx.TransportError`` family, not just a refused connect:
    ``ReadError`` and ``WriteError`` when a rebooting panel resets mid-request,
    and ``RemoteProtocolError`` when its proxy closes without answering, which is
    what a proxy restarting under load produces. ``TimeoutException`` is itself a
    ``TransportError``, so it has to be caught first to keep its own class.

    The verb is dispatched to the named httpx method rather than handed to
    ``client.request()``: the URL stays the first positional argument of a
    recognisable call, which is what makes an injected client inspectable by the
    caller that supplied it.
    """
    url = _build_url(host, port, path, ssl_context)
    try:
        async with _get_client(httpx_client, timeout, ssl_context) as client:
            match method:
                case "GET":
                    response = await client.get(url, headers=headers)
                case "POST":
                    response = await client.post(url, json=json, headers=headers)
                case "PUT":
                    response = await client.put(url, json=json, headers=headers)
                case "DELETE":
                    response = await client.delete(url, headers=headers)
    except httpx.TimeoutException as exc:
        raise SpanPanelTimeoutError(f"Timed out connecting to {host}") from exc
    except httpx.TransportError as exc:
        raise SpanPanelConnectionError(f"Cannot reach panel at {host}: {exc}") from exc
    return _Reply(host=host, endpoint=path, response=response)
