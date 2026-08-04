"""Factory for creating SPAN Panel API clients.

Auto-detects panel API version and returns an MQTT/Homie transport client.
Handles v2 registration when only a passphrase is provided.
"""

from __future__ import annotations

import logging
import re

from .adapters import discover_adapters
from .auth import register_v2
from .detection import detect_api_version
from .exceptions import SpanPanelAdapterMissingError, SpanPanelAuthError
from .mqtt.client import SpanMqttClient
from .mqtt.models import MqttClientConfig
from .protocol import SchemaAdapter

_LOGGER = logging.getLogger(__name__)

_V2_CLIENT_NAME = "span-panel-api"

_DMV_PATTERN = re.compile(r"^(\d+)\.\d+(?:\.\d+)?$")


def _select_adapter_key(data_model_version: str | None) -> tuple[str, str]:
    """Tier 1 dispatch: the panel's data-model-version selects the adapter major.

    Absence is the flat-schema signal — the property was introduced by the same
    firmware that introduced the parent/child model, so a panel that does not
    publish it is speaking the flat schema.
    """
    if data_model_version is None:
        return "schema_0", "data-model-version absent (flat schema)"

    match = _DMV_PATTERN.match(data_model_version)
    if match is None:
        return "schema_0", f"unrecognised data-model-version={data_model_version!r}, assuming flat"

    return (
        f"schema_{int(match.group(1))}",
        f"data-model-version={data_model_version!r}",
    )


def _resolve_adapter_cls(key: str, reason: str) -> type[SchemaAdapter]:
    """Look up the discovered adapter class for `key`, or raise with the installed list."""
    registry = discover_adapters()
    adapter_cls = registry.get(key)
    if adapter_cls is None:
        raise SpanPanelAdapterMissingError(needed=key, reason=reason, available=sorted(registry))
    return adapter_cls


async def create_span_client(
    host: str,
    passphrase: str | None = None,
    mqtt_config: MqttClientConfig | None = None,
    serial_number: str | None = None,
    port: int = 80,
) -> SpanMqttClient:
    """Create a SPAN Panel MQTT client.

    Args:
        host: IP address or hostname of the SPAN Panel.
        passphrase: Panel passphrase for v2 registration.
        mqtt_config: Pre-built MQTT broker configuration.
        serial_number: Panel serial number (extracted from detection/registration if omitted).
        port: HTTP port of the panel bootstrap API used for registration and detection.

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
        auth_response = await register_v2(host, _V2_CLIENT_NAME, passphrase, port=port)
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
        result = await detect_api_version(host, port=port)
        if result.status_info is not None:
            serial_number = result.status_info.serial_number

    if serial_number is None:
        raise SpanPanelAuthError("serial_number is required for MQTT transport but could not be determined")

    # Phase 0: the factory does not fetch the Homie schema, so no panel can
    # report a data-model-version yet. `None` is the correct observation for
    # every panel currently in the field — Phase 1 adds the fetch.
    data_model_version: str | None = None
    adapter_key, dispatch_reason = _select_adapter_key(data_model_version)
    adapter_cls = _resolve_adapter_cls(adapter_key, dispatch_reason)

    client = SpanMqttClient(host, serial_number, mqtt_config, panel_http_port=port, adapter_factory=adapter_cls)
    client._data_model_version = data_model_version  # pylint: disable=protected-access
    client._schema_dispatch_reason = dispatch_reason  # pylint: disable=protected-access
    await client.connect()
    return client
