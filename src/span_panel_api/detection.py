"""SPAN Panel API version detection.

Probes the panel to determine whether it supports v2 (eBus/Homie)
or only v1 (REST). The detection call is unauthenticated — no token
or passphrase is required.
"""

from __future__ import annotations

from dataclasses import dataclass
import ssl

import httpx

from ._http import V2_STATUS_PATH, _request
from .exceptions import SpanPanelConnectionError, SpanPanelTimeoutError
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
    port: int | None = None,
    httpx_client: httpx.AsyncClient | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> DetectionResult:
    """Detect SPAN Panel API version.

    Probes GET /api/v2/status (unauthenticated).
    Returns DetectionResult with api_version="v2" and status_info
    populated on success; api_version="v1" and status_info=None otherwise.

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
        DetectionResult indicating which API version is available. On transport
        failures, ``api_version`` is ``"v1"`` and ``probe_failed`` is True.
    """
    try:
        reply = await _request(
            "GET",
            host,
            port,
            V2_STATUS_PATH,
            timeout=timeout,
            httpx_client=httpx_client,
            ssl_context=ssl_context,
        )
    except (SpanPanelConnectionError, SpanPanelTimeoutError):
        # Every way the connection can fail, which is the point of catching this
        # library's own two classes rather than a hand-listed tuple of httpx
        # ones: the tuple named `ConnectError`, `TimeoutException` and
        # `RemoteProtocolError` and therefore let a `ReadError` -- a panel
        # resetting its listener mid-probe -- out of a function documented to
        # return a result rather than raise.
        return DetectionResult(api_version="v1", probe_failed=True)

    if reply.status_code != 200:
        return DetectionResult(api_version="v1")

    return DetectionResult(api_version="v2", status_info=V2StatusInfo.from_status_payload(reply.json_object()))
