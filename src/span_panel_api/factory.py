"""Factory for creating SPAN Panel API clients.

Auto-detects panel API version and returns an MQTT/Homie transport client.
Handles v2 registration when only a passphrase is provided.
"""

from __future__ import annotations

import logging
import re

from .adapters import resolve_adapter
from .auth import register_v2
from .detection import detect_api_version
from .exceptions import SpanPanelAuthError, SpanPanelSchemaVersionError
from .mqtt.client import SpanMqttClient
from .mqtt.models import MqttClientConfig

_LOGGER = logging.getLogger(__name__)

_V2_CLIENT_NAME = "span-panel-api"

# The canonical form the published spec defines: MAJOR.MINOR[.PATCH].
_DMV_CANONICAL = re.compile(r"^(\d+)\.\d+(?:\.\d+)?$")
# Tolerant form: a leading integer major, optionally followed by a separator and
# anything at all. Accepts '1', '1.0.3-rc2', '1_0'; rejects 'v1.0', '', 'x'.
_DMV_MAJOR = re.compile(r"^(\d+)(?:[._-].*)?$")


def _select_adapter_key(data_model_version: str | None) -> tuple[str, str]:
    """Tier 1 dispatch: the panel's data-model-version selects the adapter major.

    Absence is the flat-schema signal — the property was introduced by the same
    firmware that introduced the parent/child model, so a panel that does not
    publish it is speaking the flat schema.

    Presence is never read as flat. Falling back to schema_0 for a value we do
    not recognise would hand a parent/child panel to the flat parser, which does
    not fail — it produces plausible but wrong power and energy figures. A wrong
    number in Home Assistant is worse than an error, so anything present and
    unreadable raises instead.

    Between those two poles sits a value whose major is unambiguous even though
    its full form is not canonical ('1', '1.0-beta'). That is not a guess: the
    major is what selects the adapter, and it was read, not assumed. Those
    dispatch normally and log the deviation, so a firmware that starts emitting
    a new format is visible before it is an outage.

    Raises:
        SpanPanelSchemaVersionError: A version is present but no major can be
            extracted from it.
    """
    if data_model_version is None:
        return "schema_0", "data-model-version absent (flat schema)"

    if (match := _DMV_CANONICAL.match(data_model_version)) is not None:
        return f"schema_{int(match.group(1))}", f"data-model-version={data_model_version!r}"

    if (match := _DMV_MAJOR.match(data_model_version)) is not None:
        _LOGGER.warning(
            "data-model-version=%r is not the canonical MAJOR.MINOR[.PATCH] form; "
            "dispatching on major %s. Please report this value.",
            data_model_version,
            match.group(1),
        )
        return (
            f"schema_{int(match.group(1))}",
            f"data-model-version={data_model_version!r} (non-canonical; major only)",
        )

    raise SpanPanelSchemaVersionError(data_model_version)


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
        SpanPanelSchemaVersionError: The panel reports a data-model-version whose
            schema major cannot be determined.
        SpanPanelAdapterMissingError: No installed package provides an adapter for
            the schema major this panel reports.
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
    adapter_cls = resolve_adapter(adapter_key, dispatch_reason)

    client = SpanMqttClient(
        host,
        serial_number,
        mqtt_config,
        panel_http_port=port,
        adapter_factory=adapter_cls,
        data_model_version=data_model_version,
        schema_dispatch_reason=dispatch_reason,
    )
    await client.connect()
    return client
