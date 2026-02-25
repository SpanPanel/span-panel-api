"""Tests for MQTT connection lifecycle using the mock client.

Exercises AsyncMqttBridge connect/disconnect/reconnect and
SpanMqttClient full connect-to-snapshot flow.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from paho.mqtt.client import ConnectFlags, DisconnectFlags, MQTTMessage
from paho.mqtt.reasoncodes import ReasonCode

from span_panel_api.mqtt.client import SpanMqttClient
from span_panel_api.mqtt.connection import AsyncMqttBridge
from span_panel_api.mqtt.const import MQTT_RECONNECT_MIN_DELAY_S
from span_panel_api.mqtt.models import MqttClientConfig

from conftest import MINIMAL_DESCRIPTION, SERIAL, TOPIC_PREFIX_SERIAL


def _make_bridge() -> AsyncMqttBridge:
    return AsyncMqttBridge(
        host="broker.local",
        port=8883,
        username="user",
        password="pass",
        panel_host="192.168.1.1",
        serial_number=SERIAL,
        use_tls=True,
    )


def _make_mqtt_message(topic: str, payload: str) -> MQTTMessage:
    """Create a paho MQTTMessage with given topic and payload."""
    msg = MQTTMessage(topic=topic.encode("utf-8"))
    msg.payload = payload.encode("utf-8")
    return msg


# ---------------------------------------------------------------------------
# AsyncMqttBridge — connect / disconnect
# ---------------------------------------------------------------------------


class TestBridgeConnect:
    @pytest.mark.asyncio
    async def test_connect_success(self, mqtt_client_mock: MagicMock) -> None:
        bridge = _make_bridge()
        await bridge.connect()

        assert bridge.is_connected() is True
        assert bridge._initial_connect_done is True
        mqtt_client_mock.connect.assert_called_once()
        mqtt_client_mock.setup.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_sets_credentials(self, mqtt_client_mock: MagicMock) -> None:
        bridge = _make_bridge()
        await bridge.connect()

        mqtt_client_mock.username_pw_set.assert_called_once_with("user", "pass")

    @pytest.mark.asyncio
    async def test_connect_configures_tls(self, mqtt_client_mock: MagicMock) -> None:
        bridge = _make_bridge()
        await bridge.connect()

        mqtt_client_mock.tls_set.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_sets_lwt(self, mqtt_client_mock: MagicMock) -> None:
        bridge = _make_bridge()
        await bridge.connect()

        mqtt_client_mock.will_set.assert_called_once()
        lwt_call = mqtt_client_mock.will_set.call_args
        assert SERIAL in lwt_call.args[0]

    @pytest.mark.asyncio
    async def test_connect_no_tls(self, mqtt_client_mock: MagicMock) -> None:
        bridge = AsyncMqttBridge(
            host="broker.local",
            port=1883,
            username="user",
            password="pass",
            panel_host="192.168.1.1",
            serial_number=SERIAL,
            use_tls=False,
        )
        await bridge.connect()

        assert bridge.is_connected() is True
        mqtt_client_mock.tls_set.assert_not_called()

    @pytest.mark.asyncio
    async def test_disconnect_after_connect(self, mqtt_client_mock: MagicMock) -> None:
        bridge = _make_bridge()
        await bridge.connect()
        assert bridge.is_connected() is True

        await bridge.disconnect()
        assert bridge.is_connected() is False
        assert bridge._client is None
        assert bridge._should_reconnect is False
        mqtt_client_mock.disconnect.assert_called_once()


# ---------------------------------------------------------------------------
# AsyncMqttBridge — subscribe / publish
# ---------------------------------------------------------------------------


class TestBridgeSubscribePublish:
    @pytest.mark.asyncio
    async def test_subscribe_after_connect(self, mqtt_client_mock: MagicMock) -> None:
        bridge = _make_bridge()
        await bridge.connect()

        bridge.subscribe("test/topic", qos=1)
        mqtt_client_mock.subscribe.assert_called_once_with("test/topic", qos=1)

    @pytest.mark.asyncio
    async def test_publish_after_connect(self, mqtt_client_mock: MagicMock) -> None:
        bridge = _make_bridge()
        await bridge.connect()

        bridge.publish("test/topic", "hello", qos=1)
        mqtt_client_mock.publish.assert_called_once_with("test/topic", payload="hello", qos=1)


# ---------------------------------------------------------------------------
# AsyncMqttBridge — message callback
# ---------------------------------------------------------------------------


class TestBridgeMessageCallback:
    @pytest.mark.asyncio
    async def test_on_message_dispatches_to_callback(self, mqtt_client_mock: MagicMock) -> None:
        bridge = _make_bridge()
        received: list[tuple[str, str]] = []

        def on_msg(topic: str, payload: str) -> None:
            received.append((topic, payload))

        bridge.set_message_callback(on_msg)
        await bridge.connect()

        # Simulate an incoming message by calling bridge's _on_message
        msg = _make_mqtt_message("ebus/5/test/topic", "value")
        bridge._on_message(mqtt_client_mock, None, msg)

        assert received == [("ebus/5/test/topic", "value")]

    @pytest.mark.asyncio
    async def test_on_message_no_callback(self, mqtt_client_mock: MagicMock) -> None:
        bridge = _make_bridge()
        await bridge.connect()

        # Should not raise when no callback is set
        msg = _make_mqtt_message("ebus/5/test/topic", "value")
        bridge._on_message(mqtt_client_mock, None, msg)


# ---------------------------------------------------------------------------
# AsyncMqttBridge — connection callback
# ---------------------------------------------------------------------------


class TestBridgeConnectionCallback:
    @pytest.mark.asyncio
    async def test_on_connect_notifies_callback(self, mqtt_client_mock: MagicMock) -> None:
        bridge = _make_bridge()
        states: list[bool] = []
        bridge.set_connection_callback(states.append)
        await bridge.connect()

        # The connect flow triggers _on_connect → callback(True)
        assert True in states

    @pytest.mark.asyncio
    async def test_on_disconnect_notifies_callback(self, mqtt_client_mock: MagicMock) -> None:
        bridge = _make_bridge()
        states: list[bool] = []
        bridge.set_connection_callback(states.append)
        await bridge.connect()

        # Simulate disconnect
        bridge._on_disconnect(
            mqtt_client_mock,
            None,
            DisconnectFlags(is_disconnect_packet_from_server=True),
            ReasonCode(packetType=2, aName="Success"),
            None,
        )

        assert False in states
        assert bridge.is_connected() is False


# ---------------------------------------------------------------------------
# AsyncMqttBridge — reconnect loop
# ---------------------------------------------------------------------------


class TestBridgeReconnect:
    @pytest.mark.asyncio
    async def test_disconnect_triggers_reconnect(self, mqtt_client_mock: MagicMock) -> None:
        bridge = _make_bridge()
        await bridge.connect()
        assert bridge._initial_connect_done is True

        # Simulate unexpected disconnect
        bridge._on_disconnect(
            mqtt_client_mock,
            None,
            DisconnectFlags(is_disconnect_packet_from_server=True),
            ReasonCode(packetType=2, aName="Success"),
            None,
        )

        assert bridge._reconnect_task is not None
        # Let the reconnect loop run one iteration
        await asyncio.sleep(MQTT_RECONNECT_MIN_DELAY_S + 0.1)
        # reconnect should have been called and succeeded
        mqtt_client_mock.reconnect.assert_called()

        # Clean up
        await bridge.disconnect()
        assert bridge._reconnect_task is None

    @pytest.mark.asyncio
    async def test_no_reconnect_before_initial_connect(self, mqtt_client_mock: MagicMock) -> None:
        bridge = _make_bridge()
        await bridge.connect()

        # Reset initial_connect_done to simulate pre-initial state
        bridge._initial_connect_done = False

        bridge._on_disconnect(
            mqtt_client_mock,
            None,
            DisconnectFlags(is_disconnect_packet_from_server=True),
            ReasonCode(packetType=2, aName="Success"),
            None,
        )

        # No reconnect task should be created
        assert bridge._reconnect_task is None

        await bridge.disconnect()


# ---------------------------------------------------------------------------
# SpanMqttClient — full connect-to-snapshot flow
# ---------------------------------------------------------------------------


def _make_span_client() -> SpanMqttClient:
    config = MqttClientConfig(
        broker_host="broker.local",
        username="user",
        password="pass",
    )
    return SpanMqttClient(
        host="192.168.1.1",
        serial_number=SERIAL,
        broker_config=config,
    )


class TestSpanMqttClientConnect:
    @pytest.mark.asyncio
    async def test_connect_and_ready(self, mqtt_client_mock: MagicMock) -> None:
        """Full connect flow: broker connect → subscribe → Homie ready."""
        client = _make_span_client()

        # Start connect in background — it will wait for Homie ready
        connect_task = asyncio.create_task(client.connect())

        # Let the bridge connect complete
        await asyncio.sleep(0.05)

        # Feed Homie messages via _on_message to trigger ready detection.
        # Description first (not yet ready), then state (transitions to ready).
        client._on_message(f"{TOPIC_PREFIX_SERIAL}/$description", MINIMAL_DESCRIPTION)
        client._on_message(f"{TOPIC_PREFIX_SERIAL}/$state", "ready")

        await asyncio.wait_for(connect_task, timeout=5.0)

        assert await client.ping() is True
        mqtt_client_mock.subscribe.assert_called()

    @pytest.mark.asyncio
    async def test_close(self, mqtt_client_mock: MagicMock) -> None:
        client = _make_span_client()

        connect_task = asyncio.create_task(client.connect())
        await asyncio.sleep(0.05)

        client._on_message(f"{TOPIC_PREFIX_SERIAL}/$description", MINIMAL_DESCRIPTION)
        client._on_message(f"{TOPIC_PREFIX_SERIAL}/$state", "ready")
        await asyncio.wait_for(connect_task, timeout=5.0)

        await client.close()
        assert await client.ping() is False

    @pytest.mark.asyncio
    async def test_set_circuit_relay(self, mqtt_client_mock: MagicMock) -> None:
        client = _make_span_client()

        connect_task = asyncio.create_task(client.connect())
        await asyncio.sleep(0.05)

        client._on_message(f"{TOPIC_PREFIX_SERIAL}/$description", MINIMAL_DESCRIPTION)
        client._on_message(f"{TOPIC_PREFIX_SERIAL}/$state", "ready")
        await asyncio.wait_for(connect_task, timeout=5.0)

        # Publish relay command
        circuit_id = "aabbccdd11223344556677889900aabb"
        await client.set_circuit_relay(circuit_id, "OPEN")
        mqtt_client_mock.publish.assert_called()

    @pytest.mark.asyncio
    async def test_set_circuit_priority(self, mqtt_client_mock: MagicMock) -> None:
        client = _make_span_client()

        connect_task = asyncio.create_task(client.connect())
        await asyncio.sleep(0.05)

        client._on_message(f"{TOPIC_PREFIX_SERIAL}/$description", MINIMAL_DESCRIPTION)
        client._on_message(f"{TOPIC_PREFIX_SERIAL}/$state", "ready")
        await asyncio.wait_for(connect_task, timeout=5.0)

        circuit_id = "aabbccdd11223344556677889900aabb"
        await client.set_circuit_priority(circuit_id, "NEVER")
        mqtt_client_mock.publish.assert_called()

    @pytest.mark.asyncio
    async def test_streaming_dispatches_snapshot(self, mqtt_client_mock: MagicMock) -> None:
        client = _make_span_client()

        connect_task = asyncio.create_task(client.connect())
        await asyncio.sleep(0.05)

        client._on_message(f"{TOPIC_PREFIX_SERIAL}/$description", MINIMAL_DESCRIPTION)
        client._on_message(f"{TOPIC_PREFIX_SERIAL}/$state", "ready")
        await asyncio.wait_for(connect_task, timeout=5.0)

        # Register snapshot callback and start streaming
        snapshots: list[object] = []
        callback = AsyncMock(side_effect=lambda s: snapshots.append(s))
        unregister = client.register_snapshot_callback(callback)
        await client.start_streaming()

        # Trigger a property message while streaming
        client._on_message(f"{TOPIC_PREFIX_SERIAL}/core/some-prop", "42")
        await asyncio.sleep(0.05)

        assert len(snapshots) > 0
        callback.assert_called()

        # Unregister and stop
        unregister()
        await client.stop_streaming()
        await client.close()

    @pytest.mark.asyncio
    async def test_reconnect_resubscribes(self, mqtt_client_mock: MagicMock) -> None:
        client = _make_span_client()

        connect_task = asyncio.create_task(client.connect())
        await asyncio.sleep(0.05)

        client._on_message(f"{TOPIC_PREFIX_SERIAL}/$description", MINIMAL_DESCRIPTION)
        client._on_message(f"{TOPIC_PREFIX_SERIAL}/$state", "ready")
        await asyncio.wait_for(connect_task, timeout=5.0)

        mqtt_client_mock.subscribe.reset_mock()

        # Simulate reconnection
        client._on_connection_change(True)
        mqtt_client_mock.subscribe.assert_called_once()

        await client.close()
