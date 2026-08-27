"""Factory for creating SPAN Panel API clients.

Auto-detects panel API version and returns an MQTT/Homie transport client.
Handles v2 registration when only a passphrase is provided.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
from typing import TYPE_CHECKING

from ._http import _warn_plaintext_transport
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
    port: int | None = None,
    httpx_client: httpx.AsyncClient | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> SpanMqttClient:
    """Create a SPAN Panel MQTT client.

    Args:
        host: IP address or hostname of the SPAN Panel.
        passphrase: Panel passphrase for v2 registration.
        mqtt_config: Pre-built MQTT broker configuration.
        serial_number: Panel serial number (extracted from detection/registration if omitted).
        port: Port of the panel bootstrap API used for registration, detection and the
            schema fetch. ``None`` takes the scheme default -- 80 plaintext, 443 with a
            context.
        httpx_client: Optional shared ``httpx.AsyncClient``, used for every request this
            makes and handed to the client it builds. Not closed here; its timeouts and
            limits are the caller's, which is why the per-call ``timeout`` defaults are
            ignored when one is given. Superseded by ``ssl_context`` where one is given,
            because httpx cannot have a trust store applied after construction.
        ssl_context: Trust anchor for the panel's HTTPS certificate, applied to every
            bootstrap call this makes and carried into the client it returns for the
            schema refetches that client does on its own.

            ``register_v2`` is the reason this is not optional in practice: it carries
            the panel passphrase up and brings the broker password back, which makes it
            the most sensitive request this library issues. The schema fetches carry no
            credential, but a plaintext one still hands an observer the panel's
            topology, and one HTTP path left open is where the next one gets added.

            Separate from ``MqttClientConfig.ca_pem``, which anchors the *broker*
            connection. Both are the same panel CA; supply both, from one PEM.

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
        auth_response = await register_v2(
            host, _V2_CLIENT_NAME, passphrase, port=port, httpx_client=httpx_client, ssl_context=ssl_context
        )
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
    else:
        # `register_v2` warns for itself, so this branch is the only one that
        # bootstraps a client without ever reaching it. No passphrase travels
        # here, but detection and the schema fetch still go out in the clear and
        # still hand an observer the panel's topology -- and one HTTP path left
        # open is where the next one gets added.
        _warn_plaintext_transport(host, "Panel bootstrap traffic", ssl_context)

    if serial_number is None:
        # Try to detect from panel status
        result = await detect_api_version(host, port=port, httpx_client=httpx_client, ssl_context=ssl_context)
        if result.status_info is not None:
            serial_number = result.status_info.serial_number

    if serial_number is None:
        raise SpanPanelAuthError("serial_number is required for MQTT transport but could not be determined")

    # Dispatch reads the schema over REST before the broker is opened. SPAN
    # confirmed the absence of `dataModelVersion` on this endpoint is a reliable
    # flat-versus-parent/child signal, mirroring MQTT's `info/data-model-version`
    # — so the parser is chosen before a single message is consumed, rather than
    # a wrong parser being discovered by its output.
    schema = await get_homie_schema(host, port=port, httpx_client=httpx_client, ssl_context=ssl_context)
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
        ssl_context=ssl_context,
    )
    await client.connect()
    return client
