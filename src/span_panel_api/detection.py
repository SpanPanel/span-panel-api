"""SPAN Panel API version detection.

Probes the panel to determine whether it supports v2 (eBus/Homie)
or only v1 (REST). The detection call is unauthenticated — no token
or passphrase is required.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .models import V2StatusInfo


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """Result of probing a SPAN Panel for API version support."""

    api_version: str  # "v1" | "v2"
    status_info: V2StatusInfo | None = None  # populated when v2 detected


async def detect_api_version(host: str, timeout: float = 5.0) -> DetectionResult:
    """Detect SPAN Panel API version.

    Probes GET /api/v2/status (unauthenticated).
    Returns DetectionResult with api_version="v2" and status_info
    populated on success; api_version="v1" and status_info=None otherwise.

    Args:
        host: IP address or hostname of the SPAN Panel
        timeout: Request timeout in seconds

    Returns:
        DetectionResult indicating which API version is available
    """
    url = f"http://{host}/api/v2/status"
    try:
        async with httpx.AsyncClient(timeout=timeout, verify=False) as client:  # nosec B501
            response = await client.get(url)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError):
        return DetectionResult(api_version="v1")

    if response.status_code != 200:
        return DetectionResult(api_version="v1")

    data: dict[str, object] = response.json()
    serial = str(data.get("serialNumber", ""))
    firmware = str(data.get("firmwareVersion", ""))
    return DetectionResult(
        api_version="v2",
        status_info=V2StatusInfo(serial_number=serial, firmware_version=firmware),
    )
