"""SPAN Panel v2 REST API endpoints.

Standalone async functions for v2-specific operations: authentication,
certificate provisioning, schema retrieval, and status probing. These
use httpx directly — they are not routed through the generated OpenAPI
client (which only covers v1 endpoints).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid

import httpx

from ._http import _build_url, _get_client
from .exceptions import (
    SpanPanelAPIError,
    SpanPanelAuthError,
    SpanPanelConnectionError,
    SpanPanelServerError,
    SpanPanelTimeoutError,
)
from .models import HomieSchemaTypes, V2AuthResponse, V2HomieSchema, V2StatusInfo

_LOGGER = logging.getLogger(__name__)

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


def _redact(value: object) -> object:
    """Replace every credential-valued key in a JSON-decoded body, at any depth.

    A top-level key scan is not enough, and the case that matters is the exact
    one this exists for: a FastAPI-style 422 echoes the request that failed
    validation back under ``detail[].input``, so the passphrase that was just
    rejected reappears nested two levels down. Scanning only the outermost
    object would redact nothing in precisely the response most likely to carry a
    secret.

    Walks dicts and lists; anything else is returned as-is, because a scalar
    reached here is a value whose key has already been judged.
    """
    if isinstance(value, dict):
        return {key: _REDACTED if str(key).lower() in _CREDENTIAL_KEYS else _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _log_auth_failure(endpoint: str, response: httpx.Response) -> None:
    """Record why an auth call failed, at DEBUG and with credentials removed.

    The body is worth keeping — a 422's validation detail is the only thing that
    says *which* field the panel objected to — but it is not worth putting in an
    exception message, which Home Assistant surfaces in the UI, writes to the
    config-flow log, and carries into a diagnostics download. DEBUG is opt-in and
    is where a user chasing a registration failure is already looking.

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
        json.dumps(_redact(parsed), sort_keys=True),
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
    port: int = 80,
    httpx_client: httpx.AsyncClient | None = None,
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
        port: HTTP port of the panel bootstrap API
        httpx_client: Optional shared ``httpx.AsyncClient``; not closed by this function.

    Returns:
        V2AuthResponse with access token and MQTT broker credentials

    Raises:
        SpanPanelAuthError: Invalid passphrase or auth failure
        SpanPanelConnectionError: Cannot reach panel
        SpanPanelTimeoutError: Request timed out
        SpanPanelAPIError: Unexpected response
    """
    url = _build_url(host, port, "/api/v2/auth/register")
    # The panel requires unique client names — append a random suffix.
    # The passphrase field must be "hopPassphrase" per the SPAN v2 API spec.
    suffix = uuid.uuid4().hex[:8]
    unique_name = f"{name}-{suffix}"
    payload: dict[str, str] = {"name": unique_name}
    if passphrase:
        payload["hopPassphrase"] = passphrase

    try:
        async with _get_client(httpx_client, timeout) as client:
            response = await client.post(url, json=payload)
    except httpx.ConnectError as exc:
        raise SpanPanelConnectionError(f"Cannot reach panel at {host}") from exc
    except httpx.TimeoutException as exc:
        raise SpanPanelTimeoutError(f"Timed out connecting to {host}") from exc

    if response.status_code in (401, 403, 422):
        # Status only, matching the shape the branch below already uses. The body
        # is logged at DEBUG instead: a 422 from the panel's validation layer
        # echoes the submitted `hopPassphrase` straight back, and interpolating
        # `response.text` here put that secret into an exception message that
        # Home Assistant shows in the UI and captures in diagnostics.
        _log_auth_failure("/api/v2/auth/register", response)
        raise SpanPanelAuthError(f"Authentication failed (HTTP {response.status_code})")

    if response.status_code != 200:
        raise SpanPanelAPIError(f"Unexpected response from /api/v2/auth/register: HTTP {response.status_code}")

    data: dict[str, object] = response.json()
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
    port: int = 80,
    httpx_client: httpx.AsyncClient | None = None,
    max_attempts: int = CA_CERT_MAX_ATTEMPTS,
    backoff_s: float = CA_CERT_BACKOFF_S,
) -> str:
    """Download the PEM CA certificate from the SPAN Panel.

    The panel rate-limits this endpoint and replies with HTTP 429 once the
    limit is hit. A single reconnect storm — or another client polling the
    same panel — is enough to trigger it, and a one-shot request would turn
    that transient condition into a hard setup failure. Retry with
    exponential backoff, honouring ``Retry-After`` when the panel sends it.

    Args:
        host: IP address or hostname of the SPAN Panel
        timeout: Request timeout in seconds when ``httpx_client`` is None; ignored when injected.
        port: HTTP port of the panel bootstrap API
        httpx_client: Optional shared ``httpx.AsyncClient``; not closed by this function.
        max_attempts: Total attempts made when the panel replies HTTP 429.
        backoff_s: Base delay for exponential backoff between 429 retries.

    Returns:
        PEM-encoded CA certificate as a string

    Raises:
        SpanPanelConnectionError: Cannot reach panel
        SpanPanelTimeoutError: Request timed out
        SpanPanelAPIError: Unexpected response or invalid PEM
    """
    url = _build_url(host, port, "/api/v2/certificate/ca")
    last_status: int | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            async with _get_client(httpx_client, timeout) as client:
                response = await client.get(url)
        except httpx.ConnectError as exc:
            raise SpanPanelConnectionError(f"Cannot reach panel at {host}") from exc
        except httpx.TimeoutException as exc:
            raise SpanPanelTimeoutError(f"Timed out connecting to {host}") from exc

        if response.status_code == 200:
            pem = response.text
            if not pem.startswith("-----BEGIN"):
                raise SpanPanelAPIError("Response is not a valid PEM certificate")
            return pem

        last_status = response.status_code

        if response.status_code == HTTP_TOO_MANY_REQUESTS and attempt < max_attempts:
            await asyncio.sleep(_retry_delay(response.headers.get("retry-after"), attempt, backoff_s))
            continue

        break

    raise SpanPanelAPIError(f"Failed to download CA cert: HTTP {last_status}")


async def get_homie_schema(
    host: str,
    timeout: float = 10.0,
    port: int = 80,
    httpx_client: httpx.AsyncClient | None = None,
) -> V2HomieSchema:
    """Fetch the Homie property schema from the SPAN Panel.

    This endpoint is unauthenticated.

    Args:
        host: IP address or hostname of the SPAN Panel
        timeout: Request timeout in seconds when ``httpx_client`` is None; ignored when injected.
        port: HTTP port of the panel bootstrap API
        httpx_client: Optional shared ``httpx.AsyncClient``; not closed by this function.

    Returns:
        V2HomieSchema with firmware version, schema hash, and type definitions

    Raises:
        SpanPanelConnectionError: Cannot reach panel
        SpanPanelTimeoutError: Request timed out
        SpanPanelAPIError: Unexpected response
    """
    url = _build_url(host, port, "/api/v2/homie/schema")

    try:
        async with _get_client(httpx_client, timeout) as client:
            response = await client.get(url)
    except httpx.TimeoutException as exc:
        raise SpanPanelTimeoutError(f"Timed out connecting to {host}") from exc
    except httpx.TransportError as exc:
        # Every way the connection itself can fail, not just a refused connect:
        # `ReadError` and `WriteError` when a rebooting panel resets mid-request,
        # and `RemoteProtocolError` when its proxy closes without answering --
        # which is exactly what a proxy restarting under load produces. Catching
        # only `ConnectError` meant those escaped this function untranslated,
        # skipped the caller's retry clause entirely, and stranded the parser the
        # same way a 502 used to. `TimeoutException` is itself a `TransportError`,
        # so it has to be caught first.
        raise SpanPanelConnectionError(f"Cannot reach panel at {host}: {exc}") from exc

    if response.status_code >= 500:
        # A rebooting panel answers 502 from its front end while the application
        # behind it is still starting. That is "not ready yet", not "wrong" --
        # and it is the ordinary shape of a firmware upgrade, because a device
        # brings its network stack and proxy up before its application. Raised as
        # a distinct class so a caller can retry it and fail fast on a 4xx, which
        # will not fix itself.
        raise SpanPanelServerError(
            f"Panel not ready: HTTP {response.status_code} fetching the Homie schema",
            status_code=response.status_code,
        )
    if response.status_code != 200:
        raise SpanPanelAPIError(
            f"Failed to fetch Homie schema: HTTP {response.status_code}",
            status_code=response.status_code,
        )

    try:
        parsed = response.json()
    except ValueError as exc:
        # A panel part-way through starting can answer 200 with a truncated or
        # empty body. Retryable for the same reason a 502 is -- it is "not ready
        # yet" wearing a different status -- and untranslated this had precisely
        # the 502's old character: raised out of the caller's retry loop on the
        # first attempt and left the parser where it was.
        raise SpanPanelServerError(
            f"Panel not ready: {host} answered 200 with a body that is not JSON",
            status_code=response.status_code,
        ) from exc
    if not isinstance(parsed, dict):
        raise SpanPanelServerError(
            f"Panel not ready: {host} answered 200 with {type(parsed).__name__}, not an object",
            status_code=response.status_code,
        )
    data: dict[str, object] = parsed

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
    port: int = 80,
    httpx_client: httpx.AsyncClient | None = None,
) -> str:
    """Rotate the MQTT broker password on the SPAN Panel.

    After this call, the previous broker password is invalidated.
    The new broker password is returned. Note: the hop_passphrase
    (used for REST auth) is NOT changed by this operation.

    Args:
        host: IP address or hostname of the SPAN Panel
        token: Valid JWT access token
        timeout: Request timeout in seconds when ``httpx_client`` is None; ignored when injected.
        port: HTTP port of the panel bootstrap API
        httpx_client: Optional shared ``httpx.AsyncClient``; not closed by this function.

    Returns:
        New MQTT broker password

    Raises:
        SpanPanelAuthError: Token invalid or expired
        SpanPanelConnectionError: Cannot reach panel
        SpanPanelTimeoutError: Request timed out
        SpanPanelAPIError: Unexpected response
    """
    url = _build_url(host, port, "/api/v2/auth/passphrase")
    headers = {"Authorization": f"Bearer {token}"}

    try:
        async with _get_client(httpx_client, timeout) as client:
            response = await client.put(url, headers=headers)
    except httpx.ConnectError as exc:
        raise SpanPanelConnectionError(f"Cannot reach panel at {host}") from exc
    except httpx.TimeoutException as exc:
        raise SpanPanelTimeoutError(f"Timed out connecting to {host}") from exc

    if response.status_code in (401, 403, 412):
        raise SpanPanelAuthError(f"Authentication failed (HTTP {response.status_code})")

    if response.status_code != 200:
        raise SpanPanelAPIError(f"Failed to regenerate passphrase: HTTP {response.status_code}")

    data: dict[str, object] = response.json()
    return _str(data["ebusBrokerPassword"])


async def register_fqdn(
    host: str,
    token: str,
    fqdn: str,
    timeout: float = 10.0,
    port: int = 80,
    httpx_client: httpx.AsyncClient | None = None,
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
        port: HTTP port of the panel bootstrap API
        httpx_client: Optional shared ``httpx.AsyncClient``; not closed by this function.

    Raises:
        SpanPanelAuthError: Token invalid or expired
        SpanPanelConnectionError: Cannot reach panel
        SpanPanelTimeoutError: Request timed out
        SpanPanelAPIError: Unexpected response (including 404 if unsupported)
    """
    url = _build_url(host, port, "/api/v2/dns/fqdn")
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"ebusTlsFqdn": fqdn}

    try:
        async with _get_client(httpx_client, timeout) as client:
            response = await client.post(url, json=payload, headers=headers)
    except httpx.ConnectError as exc:
        raise SpanPanelConnectionError(f"Cannot reach panel at {host}") from exc
    except httpx.TimeoutException as exc:
        raise SpanPanelTimeoutError(f"Timed out connecting to {host}") from exc

    if response.status_code in (401, 403):
        raise SpanPanelAuthError(f"Authentication failed (HTTP {response.status_code})")

    if response.status_code not in (200, 201, 204):
        raise SpanPanelAPIError(f"Failed to register FQDN: HTTP {response.status_code}")


async def get_fqdn(
    host: str,
    token: str,
    timeout: float = 10.0,
    port: int = 80,
    httpx_client: httpx.AsyncClient | None = None,
) -> str | None:
    """Retrieve the currently registered FQDN from the SPAN Panel.

    Args:
        host: IP address or hostname of the SPAN Panel
        token: Valid JWT access token from register_v2
        timeout: Request timeout in seconds when ``httpx_client`` is None; ignored when injected.
        port: HTTP port of the panel bootstrap API
        httpx_client: Optional shared ``httpx.AsyncClient``; not closed by this function.

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
    url = _build_url(host, port, "/api/v2/dns/fqdn")
    headers = {"Authorization": f"Bearer {token}"}

    try:
        async with _get_client(httpx_client, timeout) as client:
            response = await client.get(url, headers=headers)
    except httpx.ConnectError as exc:
        raise SpanPanelConnectionError(f"Cannot reach panel at {host}") from exc
    except httpx.TimeoutException as exc:
        raise SpanPanelTimeoutError(f"Timed out connecting to {host}") from exc

    if response.status_code in (401, 403):
        raise SpanPanelAuthError(f"Authentication failed (HTTP {response.status_code})")

    if response.status_code == 404:
        return None

    if response.status_code != 200:
        raise SpanPanelAPIError(f"Failed to get FQDN: HTTP {response.status_code}")

    data: dict[str, object] = response.json()
    raw = data.get("ebusTlsFqdn")
    if raw is None:
        return None
    return str(raw)


async def delete_fqdn(
    host: str,
    token: str,
    timeout: float = 10.0,
    port: int = 80,
    httpx_client: httpx.AsyncClient | None = None,
) -> None:
    """Remove the registered FQDN from the SPAN Panel.

    The panel regenerates its TLS certificate without the FQDN in
    the SAN list.

    Args:
        host: IP address or hostname of the SPAN Panel
        token: Valid JWT access token from register_v2
        timeout: Request timeout in seconds when ``httpx_client`` is None; ignored when injected.
        port: HTTP port of the panel bootstrap API
        httpx_client: Optional shared ``httpx.AsyncClient``; not closed by this function.

    Raises:
        SpanPanelAuthError: Token invalid or expired
        SpanPanelConnectionError: Cannot reach panel
        SpanPanelTimeoutError: Request timed out
        SpanPanelAPIError: Unexpected response
    """
    url = _build_url(host, port, "/api/v2/dns/fqdn")
    headers = {"Authorization": f"Bearer {token}"}

    try:
        async with _get_client(httpx_client, timeout) as client:
            response = await client.delete(url, headers=headers)
    except httpx.ConnectError as exc:
        raise SpanPanelConnectionError(f"Cannot reach panel at {host}") from exc
    except httpx.TimeoutException as exc:
        raise SpanPanelTimeoutError(f"Timed out connecting to {host}") from exc

    if response.status_code in (401, 403):
        raise SpanPanelAuthError(f"Authentication failed (HTTP {response.status_code})")

    if response.status_code not in (200, 204):
        raise SpanPanelAPIError(f"Failed to delete FQDN: HTTP {response.status_code}")


async def get_v2_status(
    host: str,
    timeout: float = 5.0,
    port: int = 80,
    httpx_client: httpx.AsyncClient | None = None,
) -> V2StatusInfo:
    """Lightweight v2 status probe (unauthenticated).

    Args:
        host: IP address or hostname of the SPAN Panel
        timeout: Request timeout in seconds when ``httpx_client`` is None; ignored when injected.
        port: HTTP port of the panel bootstrap API
        httpx_client: Optional shared ``httpx.AsyncClient``; not closed by this function.

    Returns:
        V2StatusInfo with serial number and firmware version

    Raises:
        SpanPanelConnectionError: Cannot reach panel
        SpanPanelTimeoutError: Request timed out
        SpanPanelAPIError: Unexpected response or non-v2 panel
    """
    url = _build_url(host, port, "/api/v2/status")

    try:
        async with _get_client(httpx_client, timeout) as client:
            response = await client.get(url)
    except httpx.ConnectError as exc:
        raise SpanPanelConnectionError(f"Cannot reach panel at {host}") from exc
    except httpx.TimeoutException as exc:
        raise SpanPanelTimeoutError(f"Timed out connecting to {host}") from exc

    if response.status_code != 200:
        raise SpanPanelAPIError(f"Panel does not support v2 API: HTTP {response.status_code}")

    data: dict[str, object] = response.json()
    return V2StatusInfo(
        serial_number=str(data.get("serialNumber", "")),
        firmware_version=str(data.get("firmwareVersion", "")),
    )
