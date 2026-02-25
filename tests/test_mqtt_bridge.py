"""Tests for the refactored AsyncMqttBridge (event-loop-driven)."""

from __future__ import annotations

import inspect

import pytest

from span_panel_api.mqtt.async_client import AsyncMQTTClient
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

    def test_callbacks_default_none(self) -> None:
        bridge = _make_bridge()
        assert bridge._message_callback is None
        assert bridge._connection_callback is None

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


class TestNoThreadingInModule:
    """Verify no threading usage in the refactored connection module."""

    def test_no_threading_lock_import(self) -> None:
        source = inspect.getsource(AsyncMqttBridge)
        assert "threading.Lock" not in source
        assert "threading" not in source

    def test_no_loop_start(self) -> None:
        source = inspect.getsource(AsyncMqttBridge)
        assert "loop_start" not in source

    def test_no_loop_stop(self) -> None:
        source = inspect.getsource(AsyncMqttBridge)
        assert "loop_stop" not in source

    def test_uses_async_mqtt_client(self) -> None:
        source = inspect.getsource(AsyncMqttBridge)
        assert "AsyncMQTTClient" in source


class TestCallbacksDirect:
    """Verify MQTT callbacks run directly (no call_soon_threadsafe)."""

    def test_on_connect_no_threadsafe_dispatch(self) -> None:
        source = inspect.getsource(AsyncMqttBridge._on_connect)
        assert "call_soon_threadsafe" not in source

    def test_on_disconnect_no_threadsafe_dispatch(self) -> None:
        source = inspect.getsource(AsyncMqttBridge._on_disconnect)
        assert "call_soon_threadsafe" not in source

    def test_on_message_no_threadsafe_dispatch(self) -> None:
        source = inspect.getsource(AsyncMqttBridge._on_message)
        assert "call_soon_threadsafe" not in source


class TestSocketCallbackBridges:
    """Verify sync socket callbacks use call_soon_threadsafe (for executor)."""

    def test_sync_open_bridges_to_event_loop(self) -> None:
        source = inspect.getsource(AsyncMqttBridge._on_socket_open_sync)
        assert "call_soon_threadsafe" in source

    def test_sync_register_write_bridges_to_event_loop(self) -> None:
        source = inspect.getsource(AsyncMqttBridge._on_socket_register_write_sync)
        assert "call_soon_threadsafe" in source


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


class TestMqttPackageExports:
    """Verify AsyncMQTTClient is exported from the mqtt package."""

    def test_async_mqtt_client_exported(self) -> None:
        from span_panel_api.mqtt import AsyncMQTTClient as Exported

        assert Exported is AsyncMQTTClient
