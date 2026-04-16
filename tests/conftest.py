"""Shared pytest configuration and fixtures for SPAN Panel API tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from unittest.mock import MagicMock, patch

import paho.mqtt.client as paho
import pytest
from paho.mqtt.client import ConnectFlags
from paho.mqtt.reasoncodes import ReasonCode

import span_panel_api._http as _http_mod
from span_panel_api.models import V2HomieSchema
from span_panel_api.mqtt.const import TOPIC_PREFIX, TYPE_CORE


@pytest.fixture(autouse=True)
def _reset_ssl_cache() -> None:
    """Ensure the module-level SSL context cache doesn't leak between tests."""
    _http_mod._ssl_cache.context = None
    _http_mod._ssl_cache.lock = None


# ---------------------------------------------------------------------------
# Constants shared across MQTT tests
# ---------------------------------------------------------------------------

SERIAL = "nj-2316-XXXX"
TOPIC_PREFIX_SERIAL = f"{TOPIC_PREFIX}/{SERIAL}"

# Minimal Homie description that makes the device "ready"
MINIMAL_DESCRIPTION = json.dumps({"nodes": {"core": {"type": TYPE_CORE}}})

# Mock schema for SpanMqttClient.connect() — panel_size=32
_MOCK_SCHEMA = V2HomieSchema(
    firmware_version="test",
    types_schema_hash="sha256:test",
    types={
        "energy.ebus.device.circuit": {
            "space": {"datatype": "integer", "format": "1:32:1"},
        },
    },
)


# ---------------------------------------------------------------------------
# Mock MQTT client fixture
# ---------------------------------------------------------------------------


def _make_fake_sock() -> MagicMock:
    """Create a fake socket with fileno=-1 (skips add_reader/add_writer)."""
    sock = MagicMock()
    sock.fileno.return_value = -1
    return sock


@pytest.fixture
async def mqtt_client_mock() -> AsyncGenerator[MagicMock, None]:
    """Patch AsyncMQTTClient to return a MagicMock that simulates paho.

    The mock wires up ``connect()`` to trigger the bridge's ``on_connect``
    and ``on_socket_open`` callbacks, exactly matching HA core's mock pattern.

    Yields the mock client instance (``cls.return_value``).
    """
    loop = asyncio.get_running_loop()
    fake_sock = _make_fake_sock()

    def _connect(
        host: str = "",
        port: int = 0,
        keepalive: int = 60,
        **_kwargs: object,
    ) -> int:
        """Simulate paho connect — fire socket + CONNACK callbacks."""
        # Socket open goes through the sync bridge → call_soon_threadsafe
        mock_client.on_socket_open(mock_client, None, fake_sock)
        mock_client.on_socket_register_write(mock_client, None, fake_sock)
        # Schedule on_connect on event loop (we're in executor thread)
        loop.call_soon_threadsafe(
            mock_client.on_connect,
            mock_client,
            None,
            ConnectFlags(session_present=0),
            ReasonCode(packetType=2, aName="Success"),
            None,
        )
        return 0

    def _reconnect() -> int:
        """Simulate paho reconnect."""
        mock_client.on_socket_open(mock_client, None, fake_sock)
        mock_client.on_socket_register_write(mock_client, None, fake_sock)
        loop.call_soon_threadsafe(
            mock_client.on_connect,
            mock_client,
            None,
            ConnectFlags(session_present=0),
            ReasonCode(packetType=2, aName="Success"),
            None,
        )
        return 0

    with (
        patch("span_panel_api.mqtt.connection.AsyncMQTTClient") as cls,
        patch("span_panel_api.mqtt.connection.download_ca_cert", return_value="FAKE-PEM"),
        patch("span_panel_api.mqtt.connection._build_ssl_context", return_value=MagicMock()),
        patch("span_panel_api.mqtt.client.get_homie_schema", return_value=_MOCK_SCHEMA),
    ):
        mock_client = cls.return_value
        mock_client.connect.side_effect = _connect
        mock_client.reconnect.side_effect = _reconnect
        mock_client.subscribe.return_value = (0, 1)
        mock_client.publish.return_value = MagicMock(rc=0, mid=1)
        mock_client.disconnect.return_value = 0
        mock_client.loop_read.return_value = 0
        mock_client.loop_write.return_value = 0
        mock_client.loop_misc.return_value = paho.MQTT_ERR_SUCCESS

        yield mock_client
