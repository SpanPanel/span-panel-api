"""Factory for creating SPAN Panel API clients.

Auto-detects panel API version and returns an MQTT/Homie transport client.
Handles v2 registration when only a passphrase is provided.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .adapters import resolve_adapter
from .auth import get_homie_schema, register_v2
from .detection import detect_api_version
from .dispatch import select_adapter_key
from .exceptions import SpanPanelAuthError
from .mqtt.client import SpanMqttClient
from .mqtt.models import MqttClientConfig

if TYPE_CHECKING:
    import httpx

_LOGGER = logging.getLogger(__name__)

_V2_CLIENT_NAME = "span-panel-api"


async def create_span_client(
    host: str,
    passphrase: str | None = None,
    mqtt_config: MqttClientConfig | None = None,
    serial_number: str | None = None,
    port: int = 80,
    httpx_client: httpx.AsyncClient | None = None,
) -> SpanMqttClient:
    """Create a SPAN Panel MQTT client.

    Args:
        host: IP address or hostname of the SPAN Panel.
        passphrase: Panel passphrase for v2 registration.
        mqtt_config: Pre-built MQTT broker configuration.
        serial_number: Panel serial number (extracted from detection/registration if omitted).
        port: HTTP port of the panel bootstrap API used for registration and detection.
        httpx_client: Optional shared ``httpx.AsyncClient``, used for every request this
            makes and handed to the client it builds. Not closed here; its timeouts and
            limits are the caller's, which is why the per-call ``timeout`` defaults are
            ignored when one is given.

    Returns:
        A connected-ready SpanMqttClient instance.

    Raises:
        SpanPanelAuthError: Neither mqtt_config nor passphrase provided,
            or serial_number could not be determined.
        SpanPanelConnectionError: Cannot reach panel during detection or registration.
        SpanPanelTimeoutError: Timeout during detection or registration.
        SpanPanelSchemaVersionError: The panel reports a data-model-version whose
            schema major cannot be determined.
        SpanPanelAdapterMissingError: No installed package provides an adapter for
            the schema major this panel reports.
    """
    if mqtt_config is None:
        if passphrase is None:
            raise SpanPanelAuthError("Neither mqtt_config nor passphrase provided")
        auth_response = await register_v2(host, _V2_CLIENT_NAME, passphrase, port=port, httpx_client=httpx_client)
        mqtt_config = MqttClientConfig(
            broker_host=auth_response.ebus_broker_host,
            username=auth_response.ebus_broker_username,
            password=auth_response.ebus_broker_password,
            mqtts_port=auth_response.ebus_broker_mqtts_port,
            ws_port=auth_response.ebus_broker_ws_port,
            wss_port=auth_response.ebus_broker_wss_port,
        )
        if serial_number is None:
            serial_number = auth_response.serial_number

    if serial_number is None:
        # Try to detect from panel status
        result = await detect_api_version(host, port=port, httpx_client=httpx_client)
        if result.status_info is not None:
            serial_number = result.status_info.serial_number

    if serial_number is None:
        raise SpanPanelAuthError("serial_number is required for MQTT transport but could not be determined")

    # Dispatch reads the schema over REST before the broker is opened. SPAN
    # confirmed the absence of `dataModelVersion` on this endpoint is a reliable
    # flat-versus-parent/child signal, mirroring MQTT's `info/data-model-version`
    # — so the parser is chosen before a single message is consumed, rather than
    # a wrong parser being discovered by its output.
    schema = await get_homie_schema(host, port=port, httpx_client=httpx_client)
    adapter_key, dispatch_reason = select_adapter_key(schema.data_model_version)
    # In a thread: resolution reads distribution metadata and imports the adapter
    # package, and this is the first call in the process to do either. See
    # `adapters` — none of it is safe to run on an event loop.
    adapter_cls = await asyncio.to_thread(resolve_adapter, adapter_key, dispatch_reason)

    client = SpanMqttClient(
        host,
        serial_number,
        mqtt_config,
        panel_http_port=port,
        adapter_factory=adapter_cls,
        data_model_version=schema.data_model_version,
        schema_dispatch_reason=dispatch_reason,
        schema=schema,
        httpx_client=httpx_client,
    )
    await client.connect()
    return client
