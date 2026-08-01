from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from span_panel_api._impl.schema_0 import SchemaZeroAdapter
from span_panel_api.adapters import _reset_adapter_cache
from span_panel_api.exceptions import SpanPanelAdapterMissingError
from span_panel_api.factory import _select_adapter_key
from span_panel_api.mqtt.client import SpanMqttClient
from span_panel_api.mqtt.models import MqttClientConfig

from conftest import MINIMAL_DESCRIPTION, SERIAL, TOPIC_PREFIX_SERIAL


def test_absent_data_model_version_selects_schema_zero() -> None:
    key, reason = _select_adapter_key(None)
    assert key == "schema_0"
    assert "absent" in reason


@pytest.mark.parametrize("dmv", ["1.0", "1.4", "2.0"])
def test_present_data_model_version_requests_a_numbered_adapter(dmv: str) -> None:
    key, reason = _select_adapter_key(dmv)
    assert key == f"schema_{dmv.split('.')[0]}"
    assert dmv in reason


def test_missing_adapter_raises_with_the_installed_list() -> None:
    from span_panel_api.factory import _resolve_adapter_cls

    _reset_adapter_cache()
    with pytest.raises(SpanPanelAdapterMissingError) as exc:
        _resolve_adapter_cls("schema_1", "data-model-version='1.0'")

    assert exc.value.needed == "schema_1"
    assert "schema_0" in exc.value.available


# ---------------------------------------------------------------------------
# create_span_client — wiring the selected adapter class into SpanMqttClient
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_span_client_wires_schema_zero_adapter_and_diagnostics() -> None:
    """The factory must pass the resolved adapter *class* as adapter_factory,
    and assign the dispatch diagnostics onto the constructed client before
    connect() runs."""
    from span_panel_api.factory import create_span_client

    _reset_adapter_cache()
    config = MqttClientConfig(broker_host="broker.local", username="user", password="pass")

    with patch("span_panel_api.factory.SpanMqttClient") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.connect = AsyncMock()

        result = await create_span_client(
            "192.168.1.1",
            mqtt_config=config,
            serial_number="test-serial",
        )

    assert result is mock_client
    _, kwargs = mock_cls.call_args
    assert kwargs["adapter_factory"] is SchemaZeroAdapter
    mock_client.connect.assert_awaited_once()
    # Diagnostics were assigned directly on the instance ahead of connect().
    assert mock_client._data_model_version is None  # pylint: disable=protected-access
    assert "absent" in mock_client._schema_dispatch_reason  # pylint: disable=protected-access


# ---------------------------------------------------------------------------
# SpanMqttClient diagnostics properties — before and after connect()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_diagnostics_properties_before_and_after_connect(mqtt_client_mock: MagicMock) -> None:
    _reset_adapter_cache()
    config = MqttClientConfig(broker_host="broker.local", username="user", password="pass")
    client = SpanMqttClient(host="192.168.1.1", serial_number=SERIAL, broker_config=config)

    # Before connect(): no adapter yet. Defaults describe a client built
    # directly, bypassing create_span_client.
    assert client.adapter is None
    assert client.schema_major is None
    assert client.data_model_version is None
    assert client.schema_dispatch_reason == "not dispatched"
    assert "schema_0" in client.available_adapters

    # Simulate what create_span_client does after adapter selection, ahead of connect().
    client._data_model_version = None  # pylint: disable=protected-access
    client._schema_dispatch_reason = "data-model-version absent (flat schema)"  # pylint: disable=protected-access

    connect_task = asyncio.create_task(client.connect())
    await asyncio.sleep(0.05)
    client._on_message(f"{TOPIC_PREFIX_SERIAL}/$description", MINIMAL_DESCRIPTION)
    client._on_message(f"{TOPIC_PREFIX_SERIAL}/$state", "ready")
    await asyncio.wait_for(connect_task, timeout=5.0)

    assert isinstance(client.adapter, SchemaZeroAdapter)
    assert client.schema_major == "schema_0"
    assert client.data_model_version is None
    assert client.schema_dispatch_reason == "data-model-version absent (flat schema)"

    await client.close()
