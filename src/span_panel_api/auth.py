"""SPAN Panel v2 REST API endpoints.

Standalone async functions for v2-specific operations: authentication,
certificate provisioning, schema retrieval, and status probing. These
use httpx directly — they are not routed through the generated OpenAPI
client (which only covers v1 endpoints).
"""

from __future__ import annotations

import hashlib
import json
import uuid

import httpx

from ._http import _build_url, _get_client
from .exceptions import SpanPanelAPIError, SpanPanelAuthError, SpanPanelConnectionError, SpanPanelTimeoutError
from .models import HomieSchemaTypes, V2AuthResponse, V2HomieSchema, V2StatusInfo


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
        raise SpanPanelAuthError(f"Authentication failed (HTTP {response.status_code}): {response.text}")

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
) -> str:
    """Download the PEM CA certificate from the SPAN Panel.

    Args:
        host: IP address or hostname of the SPAN Panel
        timeout: Request timeout in seconds when ``httpx_client`` is None; ignored when injected.
        port: HTTP port of the panel bootstrap API
        httpx_client: Optional shared ``httpx.AsyncClient``; not closed by this function.

    Returns:
        PEM-encoded CA certificate as a string

    Raises:
        SpanPanelConnectionError: Cannot reach panel
        SpanPanelTimeoutError: Request timed out
        SpanPanelAPIError: Unexpected response or invalid PEM
    """
    url = _build_url(host, port, "/api/v2/certificate/ca")

    try:
        async with _get_client(httpx_client, timeout) as client:
            response = await client.get(url)
    except httpx.ConnectError as exc:
        raise SpanPanelConnectionError(f"Cannot reach panel at {host}") from exc
    except httpx.TimeoutException as exc:
        raise SpanPanelTimeoutError(f"Timed out connecting to {host}") from exc

    if response.status_code != 200:
        raise SpanPanelAPIError(f"Failed to download CA cert: HTTP {response.status_code}")

    pem = response.text
    if not pem.startswith("-----BEGIN"):
        raise SpanPanelAPIError("Response is not a valid PEM certificate")

    return pem


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
    except httpx.ConnectError as exc:
        raise SpanPanelConnectionError(f"Cannot reach panel at {host}") from exc
    except httpx.TimeoutException as exc:
        raise SpanPanelTimeoutError(f"Timed out connecting to {host}") from exc

    if response.status_code != 200:
        raise SpanPanelAPIError(f"Failed to fetch Homie schema: HTTP {response.status_code}")

    data: dict[str, object] = response.json()

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

    return V2HomieSchema(
        firmware_version=str(data.get("firmwareVersion", "")),
        types_schema_hash=schema_hash,
        types=types,
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
