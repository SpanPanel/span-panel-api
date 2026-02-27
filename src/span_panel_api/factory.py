"""Factory for creating SPAN Panel API clients.

Auto-detects panel API version and returns an MQTT/Homie transport client.
Handles v2 registration when only a passphrase is provided.
"""

from __future__ import annotations

import logging

from .auth import register_v2
from .detection import detect_api_version
from .exceptions import SpanPanelAuthError
from .mqtt.client import SpanMqttClient
from .mqtt.models import MqttClientConfig

_LOGGER = logging.getLogger(__name__)

_V2_CLIENT_NAME = "span-panel-api"


async def create_span_client(
    host: str,
    passphrase: str | None = None,
    mqtt_config: MqttClientConfig | None = None,
    serial_number: str | None = None,
) -> SpanMqttClient:
    """Create a SPAN Panel MQTT client.

    Args:
        host: IP address or hostname of the SPAN Panel.
        passphrase: Panel passphrase for v2 registration.
        mqtt_config: Pre-built MQTT broker configuration.
        serial_number: Panel serial number (extracted from detection/registration if omitted).

    Returns:
        A connected-ready SpanMqttClient instance.

    Raises:
        SpanPanelAuthError: Neither mqtt_config nor passphrase provided,
            or serial_number could not be determined.
        SpanPanelConnectionError: Cannot reach panel during detection or registration.
        SpanPanelTimeoutError: Timeout during detection or registration.
    """
    if mqtt_config is None:
        if passphrase is None:
            raise SpanPanelAuthError("Neither mqtt_config nor passphrase provided")
        auth_response = await register_v2(host, _V2_CLIENT_NAME, passphrase)
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
        result = await detect_api_version(host)
        if result.status_info is not None:
            serial_number = result.status_info.serial_number

    if serial_number is None:
        raise SpanPanelAuthError("serial_number is required for MQTT transport but could not be determined")

    return SpanMqttClient(host, serial_number, mqtt_config)
