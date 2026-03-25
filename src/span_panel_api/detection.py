"""SPAN Panel API version detection.

Probes the panel to determine whether it supports v2 (eBus/Homie)
or only v1 (REST). The detection call is unauthenticated — no token
or passphrase is required.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from ._http import _build_url, _get_client
from .models import V2StatusInfo


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """Result of probing a SPAN Panel for API version support.

    ``probe_failed`` is True when the HTTP request did not complete (for example
    connection refused, timeout, or protocol error). It is False when any HTTP
    response was received, including non-200 statuses that imply a v1-only panel.
    """

    api_version: str  # "v1" | "v2"
    status_info: V2StatusInfo | None = None  # populated when v2 detected
    probe_failed: bool = False


async def detect_api_version(
    host: str,
    timeout: float = 5.0,
    port: int = 80,
    httpx_client: httpx.AsyncClient | None = None,
) -> DetectionResult:
    """Detect SPAN Panel API version.

    Probes GET /api/v2/status (unauthenticated).
    Returns DetectionResult with api_version="v2" and status_info
    populated on success; api_version="v1" and status_info=None otherwise.

    Args:
        host: IP address or hostname of the SPAN Panel
        timeout: Request timeout in seconds when ``httpx_client`` is None; ignored when injected.
        port: HTTP port of the panel bootstrap API
        httpx_client: Optional shared ``httpx.AsyncClient``; not closed by this function.

    Returns:
        DetectionResult indicating which API version is available. On transport
        failures, ``api_version`` is ``"v1"`` and ``probe_failed`` is True.
    """
    url = _build_url(host, port, "/api/v2/status")
    try:
        async with _get_client(httpx_client, timeout) as client:
            response = await client.get(url)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError):
        return DetectionResult(api_version="v1", probe_failed=True)

    if response.status_code != 200:
        return DetectionResult(api_version="v1")

    data: dict[str, object] = response.json()
    serial = str(data.get("serialNumber", ""))
    firmware = str(data.get("firmwareVersion", ""))
    raw_proximity = data.get("proximityProven")
    proximity_proven: bool | None = None
    if isinstance(raw_proximity, bool):
        proximity_proven = raw_proximity
    return DetectionResult(
        api_version="v2",
        status_info=V2StatusInfo(
            serial_number=serial,
            firmware_version=firmware,
            proximity_proven=proximity_proven,
        ),
    )
