"""Factory for creating SPAN panel transport clients.

Use :func:`create_span_client` as the single entry point when building
integrations that should work with both Gen2 (OpenAPI/HTTP) and Gen3 (gRPC)
panels.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .client import SpanPanelClient
from .exceptions import SpanPanelConnectionError
from .models import PanelGeneration
from .protocol import SpanPanelClientProtocol

if TYPE_CHECKING:
    from .grpc.client import SpanGrpcClient as SpanGrpcClientType

_LOGGER = logging.getLogger(__name__)


async def create_span_client(
    host: str,
    panel_generation: PanelGeneration | None = None,
    *,
    port: int | None = None,
    use_ssl: bool = False,
    access_token: str | None = None,
    timeout: float = 30.0,
    retries: int = 0,
    retry_timeout: float = 0.5,
    retry_backoff_multiplier: float = 2.0,
    simulation_mode: bool = False,
    simulation_config_path: str | None = None,
    simulation_start_time: str | None = None,
) -> SpanPanelClientProtocol:
    """Create the appropriate SPAN panel transport client.

    When *panel_generation* is ``None`` the function auto-detects which
    generation the panel is by probing in order: Gen2 (OpenAPI/HTTP on port
    80/443) then Gen3 (gRPC on port 50065).

    Args:
        host: IP address or hostname of the SPAN panel.
        panel_generation: Force a specific generation, or ``None`` to
            auto-detect.
        port: Override the default port.  Defaults to 80 for Gen2 and 50065
            for Gen3.
        use_ssl: Use HTTPS for Gen2 connections (default: ``False``).
        access_token: JWT access token for Gen2 authenticated requests.
        timeout: Request timeout in seconds (Gen2 only).
        retries: Number of retry attempts on transient failures (Gen2 only).
        retry_timeout: Delay between retries in seconds (Gen2 only).
        retry_backoff_multiplier: Exponential backoff multiplier (Gen2 only).
        simulation_mode: Enable simulation mode (Gen2 only).
        simulation_config_path: Path to YAML simulation config (Gen2 only).
        simulation_start_time: Override simulation start time ISO string
            (Gen2 only).

    Returns:
        A client satisfying :class:`~span_panel_api.protocol.SpanPanelClientProtocol`.

    Raises:
        SpanPanelConnectionError: If auto-detection fails to reach the panel
            via either transport.
        ImportError: If Gen3 is requested but ``grpcio`` is not installed.
    """
    if panel_generation == PanelGeneration.GEN2:
        return _make_gen2_client(
            host=host,
            port=port or 80,
            use_ssl=use_ssl,
            access_token=access_token,
            timeout=timeout,
            retries=retries,
            retry_timeout=retry_timeout,
            retry_backoff_multiplier=retry_backoff_multiplier,
            simulation_mode=simulation_mode,
            simulation_config_path=simulation_config_path,
            simulation_start_time=simulation_start_time,
        )

    if panel_generation == PanelGeneration.GEN3:
        return _make_gen3_client(host=host, port=port)

    # Auto-detect
    return await _auto_detect(
        host=host,
        port=port,
        use_ssl=use_ssl,
        access_token=access_token,
        timeout=timeout,
        retries=retries,
        retry_timeout=retry_timeout,
        retry_backoff_multiplier=retry_backoff_multiplier,
        simulation_mode=simulation_mode,
        simulation_config_path=simulation_config_path,
        simulation_start_time=simulation_start_time,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _make_gen2_client(
    host: str,
    port: int,
    use_ssl: bool,
    access_token: str | None,
    timeout: float,
    retries: int,
    retry_timeout: float,
    retry_backoff_multiplier: float,
    simulation_mode: bool,
    simulation_config_path: str | None,
    simulation_start_time: str | None,
) -> SpanPanelClient:
    client = SpanPanelClient(
        host=host,
        port=port,
        use_ssl=use_ssl,
        timeout=timeout,
        retries=retries,
        retry_timeout=retry_timeout,
        retry_backoff_multiplier=retry_backoff_multiplier,
        simulation_mode=simulation_mode,
        simulation_config_path=simulation_config_path,
        simulation_start_time=simulation_start_time,
    )
    if access_token:
        client.set_access_token(access_token)
    return client


def _make_gen3_client(host: str, port: int | None) -> SpanGrpcClientType:
    try:
        from .grpc.client import SpanGrpcClient  # pylint: disable=import-outside-toplevel
    except ImportError as exc:
        raise ImportError(
            "grpcio is required for Gen3 gRPC support. Install with: pip install span-panel-api[grpc]"
        ) from exc

    from .grpc.const import DEFAULT_GRPC_PORT  # pylint: disable=import-outside-toplevel

    return SpanGrpcClient(host=host, port=port or DEFAULT_GRPC_PORT)


async def _auto_detect(
    host: str,
    port: int | None,
    use_ssl: bool,
    access_token: str | None,
    timeout: float,
    retries: int,
    retry_timeout: float,
    retry_backoff_multiplier: float,
    simulation_mode: bool,
    simulation_config_path: str | None,
    simulation_start_time: str | None,
) -> SpanPanelClientProtocol:
    """Try Gen2 first, then Gen3, raise if neither responds."""
    # Probe Gen2 (OpenAPI/HTTP)
    gen2_client = _make_gen2_client(
        host=host,
        port=port or 80,
        use_ssl=use_ssl,
        access_token=access_token,
        timeout=timeout,
        retries=retries,
        retry_timeout=retry_timeout,
        retry_backoff_multiplier=retry_backoff_multiplier,
        simulation_mode=simulation_mode,
        simulation_config_path=simulation_config_path,
        simulation_start_time=simulation_start_time,
    )
    if await gen2_client.ping():
        _LOGGER.info("Auto-detected Gen2 panel at %s", host)
        return gen2_client

    # Probe Gen3 (gRPC)
    try:
        gen3_client = _make_gen3_client(host=host, port=port)
        if await gen3_client.ping():
            _LOGGER.info("Auto-detected Gen3 panel at %s", host)
            return gen3_client
    except ImportError:
        _LOGGER.debug("grpcio not installed, skipping Gen3 probe for %s", host)

    raise SpanPanelConnectionError(
        f"Could not reach panel at {host} via Gen2 (HTTP) or Gen3 (gRPC). "
        "Verify the host address and ensure the panel is online."
    )
