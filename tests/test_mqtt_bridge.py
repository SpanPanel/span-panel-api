"""Tests for the refactored AsyncMqttBridge (event-loop-driven)."""

from __future__ import annotations

import pytest

from span_panel_api.mqtt.connection import AsyncMqttBridge

SERIAL = "test-serial-0001"


def _make_bridge() -> AsyncMqttBridge:
    return AsyncMqttBridge(
        host="broker.local",
        port=8883,
        username="user",
        password="pass",
        panel_host="192.168.1.1",
        serial_number=SERIAL,
    )


class TestBridgeConstruction:
    """Verify the refactored bridge initializes correctly."""

    def test_defaults(self) -> None:
        bridge = _make_bridge()
        assert bridge.is_connected() is False
        assert bridge._client is None
        assert bridge._misc_timer is None
        assert bridge._reconnect_task is None
        assert bridge._should_reconnect is False
        assert bridge._initial_connect_done is False

    def test_set_message_callback(self) -> None:
        bridge = _make_bridge()

        def cb(topic: str, payload: str) -> None:
            pass

        bridge.set_message_callback(cb)
        assert bridge._message_callback is cb

    def test_set_connection_callback(self) -> None:
        bridge = _make_bridge()

        def cb(connected: bool) -> None:
            pass

        bridge.set_connection_callback(cb)
        assert bridge._connection_callback is cb


class TestBridgeSubscribePublish:
    """Verify subscribe/publish with no client are safe no-ops."""

    def test_subscribe_no_client(self) -> None:
        bridge = _make_bridge()
        bridge.subscribe("test/topic")  # Should not raise

    def test_publish_no_client(self) -> None:
        bridge = _make_bridge()
        bridge.publish("test/topic", "payload")  # Should not raise


class TestDisconnectCleanup:
    """Verify disconnect cleans up all resources."""

    @pytest.mark.asyncio
    async def test_disconnect_resets_state(self) -> None:
        bridge = _make_bridge()
        bridge._should_reconnect = True
        bridge._initial_connect_done = True
        await bridge.disconnect()

        assert bridge._should_reconnect is False
        assert bridge._initial_connect_done is False
        assert bridge._connected is False
        assert bridge._client is None
        assert bridge._reconnect_task is None
        assert bridge._misc_timer is None
