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

from .exceptions import (
    SpanPanelAPIError,
    SpanPanelConnectionError,
    SpanPanelTimeoutError,
    SpanPanelTLSVerificationError,
    SpanPanelValidationError,
)

_LOGGER = logging.getLogger(__name__)

#: What a bootstrap URL resolves to when the caller names no port. HTTP without a
#: context, HTTPS with one -- so a caller that pins the panel CA and leaves the
#: port alone reaches the right place rather than the plaintext one.
DEFAULT_HTTP_PORT = 80
DEFAULT_HTTPS_PORT = 443

#: The one bootstrap path two modules request: the detector probes it to decide
#: whether the panel speaks v2 at all, and `get_v2_status` reads the same answer
#: for a caller that already knows it does. Named here rather than spelled out in
#: each, so the two cannot drift apart the way their parsers had. Load-bearing
#: for `_warn_plaintext_transport`'s exemption: a request to this path is one of
#: the three the warning stays silent for, so a change here changes what warns.
V2_STATUS_PATH = "/api/v2/status"

#: Exempt from the plaintext warning alongside `V2_STATUS_PATH`, named here
#: because the transport is what grants the exemption. See
#: `_warn_plaintext_transport`.
CA_CERT_PATH = "/api/v2/certificate/ca"

#: Exempt from the plaintext warning alongside the two above, and named here for
#: the same reason. See `_warn_plaintext_transport` for why, and for what the
#: exemption leaves a consumer to do.
HOMIE_SCHEMA_PATH = "/api/v2/homie/schema"

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
    cached = _ssl_cache.context
    if cached is not None:
        return cached
    async with _ssl_cache.get_lock():
        # Double-check after acquiring the lock.
        cached = _ssl_cache.context
        if cached is not None:
            return cached
        # Read back through a local rather than returning the field again. The
        # field is `SSLContext | None` and another task may clear or replace it
        # between the assignment and the return, so returning it a second time
        # is a read this function cannot promise is non-None -- which is what a
        # strict checker objects to, correctly. The value that was just built is
        # the value to hand back.
        loop = asyncio.get_running_loop()
        context = await loop.run_in_executor(None, ssl.create_default_context)
        _ssl_cache.context = context
        return context


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


#: Panels already warned about over plaintext, so the warning is said once each.
#: Process-wide, once per panel host: wider than the MQTT bridge's unpinned-CA
#: warning, which is per bridge instance and so repeats when a config entry is
#: reloaded. See `_warn_plaintext_transport` for why the client object is the
#: wrong key.
_warned_plaintext_hosts: set[str] = set()


def _reset_plaintext_warnings() -> None:
    """Test hook. Not public API."""
    _warned_plaintext_hosts.clear()


def _warn_plaintext_transport(
    host: str,
    path: str,
    ssl_context: ssl.SSLContext | None,
    *,
    json: dict[str, str] | None,
    headers: dict[str, str] | None,
) -> None:
    """Say out loud, once per panel, that its credential-bearing bootstrap traffic is not encrypted.

    In the same voice as the MQTT bridge's unpinned-CA warning, and for the same
    reason: a security property that is off by default is only a decision if the
    operator can tell it is off. ``ssl_context=None`` puts the request on
    plaintext ``http://``, and every call that still warns carries a credential
    that anything on the path reads: registration sends the panel passphrase,
    when it is given one, and brings an access token and the broker password
    back; passphrase rotation sends a bearer token and brings the new broker
    password back; and the three FQDN calls each send a bearer token.

    **Three endpoints are exempt, by the shape of the request, and none of them
    claims the once-per-host slot.** A request is exempt only when its path is
    one of the three *and* it sends no header and no body. None of the three
    does today, so none carries a credential in either direction, and warning
    on them named credentials they never carry. The exemption reads the
    request rather than the path alone so that a credential added to one of
    these calls later -- a bearer header on the schema read, say -- warns by
    construction, instead of depending on somebody remembering to narrow the
    exemption. What it can read is the request: it rests on none of the three
    answering with a credential, which none of them, being unauthenticated,
    has any reason to. Each path is exempt on its own ground.

    The CA download has no "on" to warn about. It fetches the very anchor
    verification would need: an unverified-TLS wrapping is readable and
    forgeable by the same active on-path attacker, the payload is a public
    certificate, and its authenticity control is the leaf check callers run
    *after* the fetch. Its warning is the line issue span#264 reported.

    The status endpoint is the detection probe, most prominently the request
    discovery makes against a device nobody has configured. No pin can exist
    there, because trust-on-first-use has not happened, and the only action
    available is configuring the panel, whose flow pins before any credential
    moves. That pin is trust on first use, not a confirmation: at first
    contact there is nothing to compare the fingerprint against. Its warning
    is what a merely *advertising* unconfigured panel produced at every boot.

    The schema read does have an "on", and is exempt because it carries no
    credential and the decision it drives is checked elsewhere, not because it
    cannot be protected. A forged answer can steer which parser reads the
    panel, and two controls stand against that. The consumer this exemption
    is for, Home Assistant, reads it unpinned in its config flow to refuse
    firmware it cannot drive before a credential moves, and reads it again at
    setup over the entry's pinned transport, where it refuses a flat panel.
    And a session cross-checks the REST schema generation against the one the
    panel publishes over MQTT once its tree is complete, refusing a
    disagreement (`SpanMqttClient._assert_transports_agree_on_schema_generation`).
    Its warning named the passphrase and the broker password the first time
    each panel was discovered or added after a start, and spent the slot that
    the registration behind it needed. A session's own schema reads keep a
    signal of their own: `SpanMqttClient` warns once when its broker
    connection uses TLS pinned by `MqttClientConfig.ca_pem` and its schema
    reads have no `ssl_context`. Every other posture in which a session reads
    its schema unverified already has a line from the bridge, which warns
    about a broker connection without TLS and about a TLS one without a
    pinned CA.

    The limits of these exemptions, stated rather than implied. The status body
    informs decisions -- `proximityProven`, the serial an identity check reads
    -- and a plaintext answer is one anything on the path can write; the
    controls for that are the panel's own registration gate and the pin the
    consumer's flow acquires before a credential moves, not a log line. A
    forged schema also reaches past the parser choice, where neither control
    above follows it: the schema-0 adapter takes the panel size and its field
    metadata from the schema's `types`. Home Assistant refuses flat firmware,
    so the exposure is another consumer of that adapter that reads the schema
    without the entry's own transport. And a consumer *can* probe a
    configured, pinned panel's status, or read its schema, without its
    context; the exemption means no warning will point that out, so a
    consumer owes every status probe and every schema read of a configured
    host the entry's own transport. Not marking the host matters as
    much as not warning -- the probe runs first in every flow, the schema read
    runs before registration in a config flow, and a diagnostic re-read runs
    on pinned entries, and none of them may spend the slot a
    credential-bearing call needs later.

    **Called from the transport, not from the calls that bootstrap a client.**
    Warning at the call sites meant each new call site had to remember to, and
    passphrase rotation did not: the one call a consumer reaches for when
    reauthenticating went out in the clear and said nothing. There is one
    mechanism here so there is nothing to remember.

    **Scoped to the panel, not to the request or the client object.** Per
    request is a line somebody filters out. Per client object looks tighter and
    is worse, because a call made without an injected client builds a fresh
    one each time -- so that key would warn on every such call, which is per
    request under another name. The panel is the thing the warning is actually
    about.

    The credential itself is never named. This is a warning *about* a secret,
    not a place to put one.
    """
    if ssl_context is not None:
        return
    if path in (CA_CERT_PATH, V2_STATUS_PATH, HOMIE_SCHEMA_PATH) and json is None and headers is None:
        return
    if host in _warned_plaintext_hosts:
        return
    _warned_plaintext_hosts.add(host)
    _LOGGER.warning(
        "Bootstrap traffic for %s is being sent over plaintext HTTP: no ssl_context was supplied, so "
        "these requests and their responses -- including any credential they carry, such as the panel "
        "passphrase and the broker password -- are readable by anything on the path between here and "
        "the panel. Pin the panel's CA certificate and pass it as ssl_context.",
        host,
    )


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
    _warn_plaintext_transport(host, path, ssl_context, json=json, headers=headers)
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
        if _is_certificate_verification_failure(exc):
            raise SpanPanelTLSVerificationError(
                f"{host} answered {path} with a certificate the supplied trust anchor rejects: {exc}"
            ) from exc
        raise SpanPanelConnectionError(f"Cannot reach panel at {host}: {exc}") from exc
    return _Reply(host=host, endpoint=path, response=response)


def _is_certificate_verification_failure(exc: BaseException) -> bool:
    """Whether this transport failure is demonstrably about certificate verification.

    httpx wraps the underlying ``ssl.SSLCertVerificationError`` rather than
    exposing it, so the evidence lives in the cause chain. Only that exact class
    counts: a handshake that dies any other way -- a reset, a protocol mismatch,
    an alert from a peer that is not TLS at all -- is indistinguishable from a
    panel mid-reboot, and calling ambiguous evidence "verification failed" would
    let a transient outage masquerade as the one failure consumers treat as
    terminal. The walk is capped because ``__context__`` chains are
    caller-assembled and nothing here should trust one to be finite.
    """
    seen = 0
    current: BaseException | None = exc
    while current is not None and seen < 10:
        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        current = current.__cause__ if current.__cause__ is not None else current.__context__
        seen += 1
    return False
