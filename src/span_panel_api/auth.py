"""SPAN Panel v2 REST API endpoints.

Standalone async functions for v2-specific operations: authentication,
certificate provisioning, schema retrieval, and status probing. These
use httpx directly — they are not routed through the generated OpenAPI
client (which only covers v1 endpoints).
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection
import hashlib
import json
import logging
import ssl
import uuid

import httpx

from ._http import V2_STATUS_PATH, _request, _warn_plaintext_transport
from .exceptions import SpanPanelAPIError, SpanPanelAuthError, SpanPanelServerError
from .models import HomieSchemaTypes, V2AuthResponse, V2HomieSchema, V2StatusInfo

_LOGGER = logging.getLogger(__name__)

#: Read, written and deleted by three calls, which is three chances to mistype it.
_FQDN_PATH = "/api/v2/dns/fqdn"


def _bearer(token: str) -> dict[str, str]:
    """The Authorization header every token-bearing call sends."""
    return {"Authorization": f"Bearer {token}"}


#: Keys whose value is a credential wherever it appears in a response body,
#: compared case-folded because the panel spells them lowerCamelCase and a
#: proxy or validation layer in between may not.
_CREDENTIAL_KEYS = frozenset(
    {
        "hoppassphrase",
        "passphrase",
        "ebusbrokerpassword",
        "password",
        "accesstoken",
        "token",
    }
)

_REDACTED = "***"

#: The key a pydantic-style validation error puts the field path under. Its value
#: is a list -- ``["body", "hopPassphrase"]`` -- and it is the only thing in a
#: field-level error object that says what was being validated.
_LOCATION_KEY = "loc"

#: The members of a validation error object that describe the failure rather than
#: repeat what was submitted. In an error object whose ``loc`` names a credential,
#: every *other* scalar is the rejected value under one name or another --
#: ``input`` is the name FastAPI uses, and a validator is free to add its own.
_ERROR_DESCRIPTION_KEYS = frozenset({"type", "loc", "msg", "url"})


def _names_a_credential(location: object) -> bool:
    """Whether a validation error's ``loc`` points at one of the credential fields."""
    if not isinstance(location, list):
        return False
    return any(isinstance(part, str) and part.lower() in _CREDENTIAL_KEYS for part in location)


def _scrub(text: str, secrets: Collection[str]) -> str:
    """Remove every occurrence of a credential the caller knows it sent.

    Empty secrets are skipped rather than matched: a door-bypass registration
    sends no passphrase, and ``"".replace("", ...)`` would rewrite every gap
    between characters in the body.
    """
    for secret in secrets:
        if secret:
            text = text.replace(secret, _REDACTED)
    return text


def _redact_member(key: object, item: object, rejected_here: bool, secrets: Collection[str]) -> object:
    """Decide one member of a decoded body, by its key and by what sits beside it."""
    name = str(key).lower()
    if name in _CREDENTIAL_KEYS:
        return _REDACTED
    if rejected_here and name not in _ERROR_DESCRIPTION_KEYS and not isinstance(item, dict | list):
        # A scalar in an error object whose `loc` names a credential. Its own key
        # says nothing -- `input` is not a secret-sounding word -- so the `loc`
        # beside it is the only evidence there is, and it is conclusive. A dict or
        # a list is walked instead, because there the keys inside can be judged
        # one by one and flattening it would throw away the diagnostic.
        return _REDACTED
    return _redact(item, secrets)


def _redact(value: object, secrets: Collection[str] = ()) -> object:
    """Strip credentials out of a JSON-decoded body, at any depth, three ways.

    **By key**, wherever a credential-named key appears. A top-level scan is not
    enough: a FastAPI-style 422 echoes the whole rejected request back under
    ``detail[].input``, so the passphrase reappears two levels down.

    **By the key beside it.** The same validation layer reports a *field-level*
    failure differently — ``loc`` points at the field and ``input`` carries the
    bare value — and there the credential is a scalar under an innocuous key. Only
    the sibling ``loc`` marks it, so that is what is read.

    **By value**, for the secrets the caller passes in. Structure runs out: a
    validator may fold the rejected value into its own prose, and nothing marks
    that. The one call that sends a credential knows exactly what it sent.

    Walks dicts and lists; any other value is returned as-is once scrubbed.
    """
    if isinstance(value, dict):
        rejected_here = _names_a_credential(value.get(_LOCATION_KEY))
        return {_scrub(str(key), secrets): _redact_member(key, item, rejected_here, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, secrets) for item in value]
    if isinstance(value, str):
        return _scrub(value, secrets)
    return value


def _log_auth_failure(endpoint: str, response: httpx.Response, secrets: Collection[str] = ()) -> None:
    """Record why an auth call failed, at DEBUG and with credentials removed.

    The body is worth keeping — a 422's validation detail is the only thing that
    says *which* field the panel objected to — but it is not worth putting in an
    exception message, which Home Assistant surfaces in the UI, writes to the
    config-flow log, and carries into a diagnostics download. DEBUG is opt-in and
    is where a user chasing a registration failure is already looking.

    ``secrets`` is what the caller sent on this request. Passing it closes the
    gap that key-based redaction cannot: a panel is free to quote the credential
    back in free prose, and no key or ``loc`` marks that.

    A body that is not JSON is described rather than shown. It could be an HTML
    error page from a proxy, and it could equally be an echo of the request; with
    no structure to walk there is no way to redact it, so only its shape is
    logged.
    """
    try:
        parsed = response.json()
    except ValueError:
        _LOGGER.debug(
            "%s failed with HTTP %d; body not JSON (%d bytes, content-type %r)",
            endpoint,
            response.status_code,
            len(response.content),
            response.headers.get("content-type", ""),
        )
        return
    _LOGGER.debug(
        "%s failed with HTTP %d; body (credentials redacted): %s",
        endpoint,
        response.status_code,
        json.dumps(_redact(parsed, secrets), sort_keys=True),
    )


def _str(val: object) -> str:
    """Extract a string from a JSON-decoded value."""
    return str(val) if val is not None else ""


def _int(val: object) -> int:
    """Extract an int from a JSON-decoded value."""
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        return int(val)
    return int(str(val))


HTTP_TOO_MANY_REQUESTS = 429

#: Default attempts and base backoff used when the panel rate-limits a request.
CA_CERT_MAX_ATTEMPTS = 5
CA_CERT_BACKOFF_S = 1.5


def _retry_delay(retry_after: str | None, attempt: int, backoff_s: float) -> float:
    """Return how long to wait before retrying a rate-limited request.

    Prefers the panel's ``Retry-After`` header (delta-seconds form) and falls
    back to exponential backoff. Malformed or negative values fall back too,
    so a bad header can never stall or skip the retry.
    """
    fallback = backoff_s * (2.0 ** (attempt - 1))
    if retry_after is None:
        return fallback
    try:
        parsed = float(retry_after)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed >= 0 else fallback


async def register_v2(
    host: str,
    name: str,
    passphrase: str | None = None,
    timeout: float = 10.0,
    port: int | None = None,
    httpx_client: httpx.AsyncClient | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> V2AuthResponse:
    """Register with the SPAN Panel v2 API and obtain access + MQTT credentials.

    A random suffix is appended to ``name`` to ensure uniqueness per panel.
    If ``passphrase`` is provided, it is sent as ``hopPassphrase``; omitting
    it enables door-bypass registration.

    .. note::
        Every call creates a new registered client entry on the panel. Callers
        should persist and reuse the returned ``V2AuthResponse`` rather than
        re-registering on every restart — otherwise stale entries will
        accumulate over the panel's lifetime.

    Args:
        host: IP address or hostname of the SPAN Panel
        name: Client display name base (e.g., "home-assistant"); a UUID suffix is appended
        passphrase: Panel passphrase (printed on label or set by owner). None for door bypass.
        timeout: Request timeout in seconds for the internally created client when
            ``httpx_client`` is None; ignored when a client is injected (caller configures timeouts).
        port: Port of the panel bootstrap API. ``None`` means "unspecified" and takes
            the scheme's default -- 80 without ``ssl_context``, 443 with one.
        httpx_client: Optional shared ``httpx.AsyncClient``; not closed by this function.
            Not used when ``ssl_context`` is supplied -- httpx fixes its trust store at
            construction, so a pinned CA needs a client built for it. See ``_get_client``.
        ssl_context: Trust anchor for the panel's HTTPS certificate. Supplying one moves
            this call to ``https://``; ``None`` is byte-identical to 3.0.1.

    Returns:
        V2AuthResponse with access token and MQTT broker credentials

    Raises:
        SpanPanelAuthError: Invalid passphrase or auth failure
        SpanPanelConnectionError: Cannot reach panel
        SpanPanelTimeoutError: Request timed out
        SpanPanelAPIError: Unexpected response
    """
    # This is the request whose plaintext exposure is a credential exposure: the
    # passphrase goes up in it and the broker password comes back in it.
    _warn_plaintext_transport(host, "SPAN Panel v2 registration", ssl_context)

    # The panel requires unique client names — append a random suffix.
    # The passphrase field must be "hopPassphrase" per the SPAN v2 API spec.
    suffix = uuid.uuid4().hex[:8]
    unique_name = f"{name}-{suffix}"
    payload: dict[str, str] = {"name": unique_name}
    if passphrase:
        payload["hopPassphrase"] = passphrase

    reply = await _request(
        "POST",
        host,
        port,
        "/api/v2/auth/register",
        timeout=timeout,
        httpx_client=httpx_client,
        ssl_context=ssl_context,
        json=payload,
    )

    if reply.status_code in (401, 403, 422):
        # Status only, matching the shape the branch below already uses. The body
        # is logged at DEBUG instead: a 422 from the panel's validation layer
        # echoes the submitted `hopPassphrase` straight back, and interpolating
        # `response.text` here put that secret into an exception message that
        # Home Assistant shows in the UI and captures in diagnostics. The
        # passphrase goes with it because this is the one place in the library
        # that knows what was sent, and the panel is under no obligation to
        # quote it back under a key that names it.
        _log_auth_failure(reply.endpoint, reply.response, () if passphrase is None else (passphrase,))
        raise SpanPanelAuthError(f"Authentication failed (HTTP {reply.status_code})")

    if reply.status_code != 200:
        raise SpanPanelAPIError(f"Unexpected response from /api/v2/auth/register: HTTP {reply.status_code}")

    data = reply.json_object(
        "accessToken",
        "tokenType",
        "iatMs",
        "ebusBrokerUsername",
        "ebusBrokerPassword",
        "ebusBrokerHost",
        "ebusBrokerMqttsPort",
        "ebusBrokerWsPort",
        "ebusBrokerWssPort",
        "hostname",
        "serialNumber",
        "hopPassphrase",
    )
    return V2AuthResponse(
        access_token=_str(data["accessToken"]),
        token_type=_str(data["tokenType"]),
        iat_ms=_int(data["iatMs"]),
        ebus_broker_username=_str(data["ebusBrokerUsername"]),
        ebus_broker_password=_str(data["ebusBrokerPassword"]),
        ebus_broker_host=_str(data["ebusBrokerHost"]),
        ebus_broker_mqtts_port=_int(data["ebusBrokerMqttsPort"]),
        ebus_broker_ws_port=_int(data["ebusBrokerWsPort"]),
        ebus_broker_wss_port=_int(data["ebusBrokerWssPort"]),
        hostname=_str(data["hostname"]),
        serial_number=_str(data["serialNumber"]),
        hop_passphrase=_str(data["hopPassphrase"]),
    )


async def download_ca_cert(
    host: str,
    timeout: float = 10.0,
    port: int | None = None,
    httpx_client: httpx.AsyncClient | None = None,
    max_attempts: int = CA_CERT_MAX_ATTEMPTS,
    backoff_s: float = CA_CERT_BACKOFF_S,
    ssl_context: ssl.SSLContext | None = None,
) -> str:
    """Download the PEM CA certificate from the SPAN Panel.

    **This call is unauthenticated and unverified by construction, and its
    result must be fingerprint-confirmed by the caller before it is trusted.**
    It is the bootstrap: it fetches the very anchor everything else is checked
    against, so there is nothing for it to check itself against. Anything on the
    path between here and the panel can answer it with a CA of its own, and the
    response carries no evidence that would distinguish that from the real one.
    Whoever calls this owes the trust decision -- comparing the fingerprint
    against one recorded out of band, or against one pinned on a previous
    install -- and until that is done the PEM is a candidate, not an anchor.

    ``ssl_context`` exists here for the caller that *already holds* the anchor
    and wants a second copy over a verified channel: refetching to compare
    fingerprints, which is how a suspected CA rotation is told apart from an
    ordinary TLS failure. It does not make the first fetch trustworthy, because
    the first fetch has no context to pass.

    The panel rate-limits this endpoint and replies with HTTP 429 once the
    limit is hit. A single reconnect storm — or another client polling the
    same panel — is enough to trigger it, and a one-shot request would turn
    that transient condition into a hard setup failure. Retry with
    exponential backoff, honouring ``Retry-After`` when the panel sends it.

    Args:
        host: IP address or hostname of the SPAN Panel
        timeout: Request timeout in seconds when ``httpx_client`` is None; ignored when injected.
        port: Port of the panel bootstrap API. ``None`` means "unspecified" and takes
            the scheme's default -- 80 without ``ssl_context``, 443 with one.
        httpx_client: Optional shared ``httpx.AsyncClient``; not closed by this function.
            Not used when ``ssl_context`` is supplied -- httpx fixes its trust store at
            construction, so a pinned CA needs a client built for it. See ``_get_client``.
        ssl_context: Trust anchor for a *re*-fetch by a caller that already holds one.
            ``None`` -- the bootstrap case -- is plaintext HTTP, and has to be.
        max_attempts: Total attempts made when the panel replies HTTP 429.
        backoff_s: Base delay for exponential backoff between 429 retries.

    Returns:
        PEM-encoded CA certificate as a string

    Raises:
        SpanPanelConnectionError: Cannot reach panel
        SpanPanelTimeoutError: Request timed out
        SpanPanelAPIError: Unexpected response or invalid PEM
    """
    last_status: int | None = None

    for attempt in range(1, max_attempts + 1):
        reply = await _request(
            "GET",
            host,
            port,
            "/api/v2/certificate/ca",
            timeout=timeout,
            httpx_client=httpx_client,
            ssl_context=ssl_context,
        )

        if reply.status_code == 200:
            pem = reply.text
            if not pem.startswith("-----BEGIN"):
                raise SpanPanelAPIError("Response is not a valid PEM certificate")
            return pem

        last_status = reply.status_code

        if reply.status_code == HTTP_TOO_MANY_REQUESTS and attempt < max_attempts:
            await asyncio.sleep(_retry_delay(reply.headers.get("retry-after"), attempt, backoff_s))
            continue

        break

    raise SpanPanelAPIError(f"Failed to download CA cert: HTTP {last_status}")


async def get_homie_schema(
    host: str,
    timeout: float = 10.0,
    port: int | None = None,
    httpx_client: httpx.AsyncClient | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> V2HomieSchema:
    """Fetch the Homie property schema from the SPAN Panel.

    This endpoint is unauthenticated.

    Args:
        host: IP address or hostname of the SPAN Panel
        timeout: Request timeout in seconds when ``httpx_client`` is None; ignored when injected.
        port: Port of the panel bootstrap API. ``None`` means "unspecified" and takes
            the scheme's default -- 80 without ``ssl_context``, 443 with one.
        httpx_client: Optional shared ``httpx.AsyncClient``; not closed by this function.
            Not used when ``ssl_context`` is supplied -- httpx fixes its trust store at
            construction, so a pinned CA needs a client built for it. See ``_get_client``.
        ssl_context: Trust anchor for the panel's HTTPS certificate. Supplying one moves
            this call to ``https://``; ``None`` is byte-identical to 3.0.1.

    Returns:
        V2HomieSchema with firmware version, schema hash, and type definitions

    Raises:
        SpanPanelConnectionError: Cannot reach panel
        SpanPanelTimeoutError: Request timed out
        SpanPanelAPIError: Unexpected response
    """
    reply = await _request(
        "GET",
        host,
        port,
        "/api/v2/homie/schema",
        timeout=timeout,
        httpx_client=httpx_client,
        ssl_context=ssl_context,
    )

    if reply.status_code >= 500:
        # A rebooting panel answers 502 from its front end while the application
        # behind it is still starting. That is "not ready yet", not "wrong" --
        # and it is the ordinary shape of a firmware upgrade, because a device
        # brings its network stack and proxy up before its application. Raised as
        # a distinct class so a caller can retry it and fail fast on a 4xx, which
        # will not fix itself.
        raise SpanPanelServerError(
            f"Panel not ready: HTTP {reply.status_code} fetching the Homie schema",
            status_code=reply.status_code,
        )
    if reply.status_code != 200:
        raise SpanPanelAPIError(
            f"Failed to fetch Homie schema: HTTP {reply.status_code}",
            status_code=reply.status_code,
        )

    # A panel part-way through starting can answer 200 with a truncated or empty
    # body. Retryable for the same reason a 502 is -- it is "not ready yet"
    # wearing a different status -- which is why this is the one endpoint that
    # asks for its malformed bodies as `SpanPanelServerError`. Untranslated it
    # had precisely the 502's old character: raised out of the caller's retry
    # loop on the first attempt and leaving the parser where it was.
    data = reply.json_object(on_malformed=SpanPanelServerError)

    # Extract types — each value is a dict of property definitions
    raw_types = data.get("types", {})
    types: HomieSchemaTypes = {}
    if isinstance(raw_types, dict):
        for type_name, props in raw_types.items():
            if isinstance(props, dict):
                types[str(type_name)] = {str(k): v for k, v in props.items()}

    # Compute schema hash from types key names for change detection
    # The panel provides this implicitly via the firmware version + types structure
    # We derive a hash for caching; the fixture README documents the expected value
    types_json = json.dumps(data.get("types", {}), sort_keys=True)
    schema_hash = "sha256:" + hashlib.sha256(types_json.encode()).hexdigest()[:16]

    # Read before anything else interprets the payload. A parent/child response
    # carries `deviceClasses` where this one reads `types`, so every field below
    # degrades to empty for such a panel — which is harmless only because this
    # value routes it to a different parser before those fields are used.
    # Absence is the flat signal and must stay distinct from an empty string.
    raw_data_model_version = data.get("dataModelVersion")
    data_model_version = None if raw_data_model_version is None else str(raw_data_model_version)

    return V2HomieSchema(
        firmware_version=str(data.get("firmwareVersion", "")),
        types_schema_hash=schema_hash,
        types=types,
        data_model_version=data_model_version,
    )


async def regenerate_passphrase(
    host: str,
    token: str,
    timeout: float = 10.0,
    port: int | None = None,
    httpx_client: httpx.AsyncClient | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> str:
    """Rotate the MQTT broker password on the SPAN Panel.

    After this call, the previous broker password is invalidated.
    The new broker password is returned. Note: the hop_passphrase
    (used for REST auth) is NOT changed by this operation.

    Args:
        host: IP address or hostname of the SPAN Panel
        token: Valid JWT access token
        timeout: Request timeout in seconds when ``httpx_client`` is None; ignored when injected.
        port: Port of the panel bootstrap API. ``None`` means "unspecified" and takes
            the scheme's default -- 80 without ``ssl_context``, 443 with one.
        httpx_client: Optional shared ``httpx.AsyncClient``; not closed by this function.
            Not used when ``ssl_context`` is supplied -- httpx fixes its trust store at
            construction, so a pinned CA needs a client built for it. See ``_get_client``.
        ssl_context: Trust anchor for the panel's HTTPS certificate. Supplying one moves
            this call to ``https://``; ``None`` is byte-identical to 3.0.1.

    Returns:
        New MQTT broker password

    Raises:
        SpanPanelAuthError: Token invalid or expired
        SpanPanelConnectionError: Cannot reach panel
        SpanPanelTimeoutError: Request timed out
        SpanPanelAPIError: Unexpected response
    """
    reply = await _request(
        "PUT",
        host,
        port,
        "/api/v2/auth/passphrase",
        timeout=timeout,
        httpx_client=httpx_client,
        ssl_context=ssl_context,
        headers=_bearer(token),
    )

    if reply.status_code in (401, 403, 412):
        raise SpanPanelAuthError(f"Authentication failed (HTTP {reply.status_code})")

    if reply.status_code != 200:
        raise SpanPanelAPIError(f"Failed to regenerate passphrase: HTTP {reply.status_code}")

    return _str(reply.json_object("ebusBrokerPassword")["ebusBrokerPassword"])


async def register_fqdn(
    host: str,
    token: str,
    fqdn: str,
    timeout: float = 10.0,
    port: int | None = None,
    httpx_client: httpx.AsyncClient | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> None:
    """Register an FQDN with the SPAN Panel for TLS certificate SAN inclusion.

    The panel regenerates its TLS server certificate to include the
    provided FQDN in the Subject Alternative Names, allowing MQTTS
    clients connecting via the FQDN to pass hostname verification.

    Args:
        host: IP address or hostname of the SPAN Panel
        token: Valid JWT access token from register_v2
        fqdn: Fully qualified domain name to register
        timeout: Request timeout in seconds when ``httpx_client`` is None; ignored when injected.
        port: Port of the panel bootstrap API. ``None`` means "unspecified" and takes
            the scheme's default -- 80 without ``ssl_context``, 443 with one.
        httpx_client: Optional shared ``httpx.AsyncClient``; not closed by this function.
            Not used when ``ssl_context`` is supplied -- httpx fixes its trust store at
            construction, so a pinned CA needs a client built for it. See ``_get_client``.
        ssl_context: Trust anchor for the panel's HTTPS certificate. Supplying one moves
            this call to ``https://``; ``None`` is byte-identical to 3.0.1.

    Raises:
        SpanPanelAuthError: Token invalid or expired
        SpanPanelConnectionError: Cannot reach panel
        SpanPanelTimeoutError: Request timed out
        SpanPanelAPIError: Unexpected response (including 404 if unsupported)
    """
    reply = await _request(
        "POST",
        host,
        port,
        _FQDN_PATH,
        timeout=timeout,
        httpx_client=httpx_client,
        ssl_context=ssl_context,
        json={"ebusTlsFqdn": fqdn},
        headers=_bearer(token),
    )

    if reply.status_code in (401, 403):
        raise SpanPanelAuthError(f"Authentication failed (HTTP {reply.status_code})")

    if reply.status_code not in (200, 201, 204):
        raise SpanPanelAPIError(f"Failed to register FQDN: HTTP {reply.status_code}")


async def get_fqdn(
    host: str,
    token: str,
    timeout: float = 10.0,
    port: int | None = None,
    httpx_client: httpx.AsyncClient | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> str | None:
    """Retrieve the currently registered FQDN from the SPAN Panel.

    Args:
        host: IP address or hostname of the SPAN Panel
        token: Valid JWT access token from register_v2
        timeout: Request timeout in seconds when ``httpx_client`` is None; ignored when injected.
        port: Port of the panel bootstrap API. ``None`` means "unspecified" and takes
            the scheme's default -- 80 without ``ssl_context``, 443 with one.
        httpx_client: Optional shared ``httpx.AsyncClient``; not closed by this function.
            Not used when ``ssl_context`` is supplied -- httpx fixes its trust store at
            construction, so a pinned CA needs a client built for it. See ``_get_client``.
        ssl_context: Trust anchor for the panel's HTTPS certificate. Supplying one moves
            this call to ``https://``; ``None`` is byte-identical to 3.0.1.

    Returns:
        The registered FQDN string, or ``None`` when no FQDN is configured
        (HTTP 404 or missing ``ebusTlsFqdn`` field). An empty string is only
        returned when the panel reports an explicit empty FQDN value.

    Raises:
        SpanPanelAuthError: Token invalid or expired
        SpanPanelConnectionError: Cannot reach panel
        SpanPanelTimeoutError: Request timed out
        SpanPanelAPIError: Unexpected response
    """
    reply = await _request(
        "GET",
        host,
        port,
        _FQDN_PATH,
        timeout=timeout,
        httpx_client=httpx_client,
        ssl_context=ssl_context,
        headers=_bearer(token),
    )

    if reply.status_code in (401, 403):
        raise SpanPanelAuthError(f"Authentication failed (HTTP {reply.status_code})")

    if reply.status_code == 404:
        return None

    if reply.status_code != 200:
        raise SpanPanelAPIError(f"Failed to get FQDN: HTTP {reply.status_code}")

    raw = reply.json_object().get("ebusTlsFqdn")
    if raw is None:
        return None
    return str(raw)


async def delete_fqdn(
    host: str,
    token: str,
    timeout: float = 10.0,
    port: int | None = None,
    httpx_client: httpx.AsyncClient | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> None:
    """Remove the registered FQDN from the SPAN Panel.

    The panel regenerates its TLS certificate without the FQDN in
    the SAN list.

    Args:
        host: IP address or hostname of the SPAN Panel
        token: Valid JWT access token from register_v2
        timeout: Request timeout in seconds when ``httpx_client`` is None; ignored when injected.
        port: Port of the panel bootstrap API. ``None`` means "unspecified" and takes
            the scheme's default -- 80 without ``ssl_context``, 443 with one.
        httpx_client: Optional shared ``httpx.AsyncClient``; not closed by this function.
            Not used when ``ssl_context`` is supplied -- httpx fixes its trust store at
            construction, so a pinned CA needs a client built for it. See ``_get_client``.
        ssl_context: Trust anchor for the panel's HTTPS certificate. Supplying one moves
            this call to ``https://``; ``None`` is byte-identical to 3.0.1.

    Raises:
        SpanPanelAuthError: Token invalid or expired
        SpanPanelConnectionError: Cannot reach panel
        SpanPanelTimeoutError: Request timed out
        SpanPanelAPIError: Unexpected response
    """
    reply = await _request(
        "DELETE",
        host,
        port,
        _FQDN_PATH,
        timeout=timeout,
        httpx_client=httpx_client,
        ssl_context=ssl_context,
        headers=_bearer(token),
    )

    if reply.status_code in (401, 403):
        raise SpanPanelAuthError(f"Authentication failed (HTTP {reply.status_code})")

    if reply.status_code not in (200, 204):
        raise SpanPanelAPIError(f"Failed to delete FQDN: HTTP {reply.status_code}")


async def get_v2_status(
    host: str,
    timeout: float = 5.0,
    port: int | None = None,
    httpx_client: httpx.AsyncClient | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> V2StatusInfo:
    """Lightweight v2 status probe (unauthenticated).

    Args:
        host: IP address or hostname of the SPAN Panel
        timeout: Request timeout in seconds when ``httpx_client`` is None; ignored when injected.
        port: Port of the panel bootstrap API. ``None`` means "unspecified" and takes
            the scheme's default -- 80 without ``ssl_context``, 443 with one.
        httpx_client: Optional shared ``httpx.AsyncClient``; not closed by this function.
            Not used when ``ssl_context`` is supplied -- httpx fixes its trust store at
            construction, so a pinned CA needs a client built for it. See ``_get_client``.
        ssl_context: Trust anchor for the panel's HTTPS certificate. Supplying one moves
            this call to ``https://``; ``None`` is byte-identical to 3.0.1.

    Returns:
        V2StatusInfo with serial number and firmware version

    Raises:
        SpanPanelConnectionError: Cannot reach panel
        SpanPanelTimeoutError: Request timed out
        SpanPanelAPIError: Unexpected response or non-v2 panel
    """
    reply = await _request(
        "GET",
        host,
        port,
        V2_STATUS_PATH,
        timeout=timeout,
        httpx_client=httpx_client,
        ssl_context=ssl_context,
    )

    if reply.status_code != 200:
        raise SpanPanelAPIError(f"Panel does not support v2 API: HTTP {reply.status_code}")

    return V2StatusInfo.from_status_payload(reply.json_object())
