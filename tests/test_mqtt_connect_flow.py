"""Tests for MQTT connection lifecycle using the mock client.

Exercises AsyncMqttBridge connect/disconnect/reconnect and
SpanMqttClient full connect-to-snapshot flow.
"""

from __future__ import annotations

import asyncio
import ssl
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from paho.mqtt.client import ConnectFlags, DisconnectFlags, MQTTMessage
from paho.mqtt.reasoncodes import ReasonCode

from span_panel_api.exceptions import SpanPanelAPIError, SpanPanelConnectionError
from span_panel_api.mqtt.client import SpanMqttClient
from span_panel_api.mqtt.connection import AsyncMqttBridge
from span_panel_api.mqtt.const import MQTT_FULL_REBUILD_AFTER_FAILURES, MQTT_RECONNECT_MIN_DELAY_S
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

        mqtt_client_mock.tls_set_context.assert_called_once()
        mqtt_client_mock.tls_set.assert_not_called()

    @pytest.mark.asyncio
    async def test_connect_does_not_set_lwt(self, mqtt_client_mock: MagicMock) -> None:
        """Consumer must not set LWT on the device's $state topic."""
        bridge = _make_bridge()
        await bridge.connect()

        mqtt_client_mock.will_set.assert_not_called()

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
        mqtt_client_mock.tls_set_context.assert_not_called()

    @pytest.mark.asyncio
    async def test_malformed_ca_pem_raises_connection_error(self, mqtt_client_mock: MagicMock) -> None:
        """Malformed CA PEM must surface as SpanPanelConnectionError, not ssl.SSLError."""
        bridge = _make_bridge()
        with patch(
            "span_panel_api.mqtt.connection.build_panel_ssl_context",
            side_effect=ssl.SSLError("malformed PEM"),
        ):
            with pytest.raises(SpanPanelConnectionError, match="Failed to build SSL context"):
                await bridge.connect()

    @pytest.mark.asyncio
    async def test_non_oserror_connect_failure_wrapped(self, mqtt_client_mock: MagicMock) -> None:
        """Non-OSError from paho.connect() (e.g. WebsocketConnectionError) wraps cleanly."""
        bridge = _make_bridge()

        class _FakeWebsocketError(Exception):
            pass

        mqtt_client_mock.connect.side_effect = _FakeWebsocketError("ws handshake failed")
        with pytest.raises(SpanPanelConnectionError, match="Cannot connect to MQTT broker"):
            await bridge.connect()

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


def _make_span_client(snapshot_interval: float = 1.0) -> SpanMqttClient:
    config = MqttClientConfig(
        broker_host="broker.local",
        username="user",
        password="pass",
    )
    return SpanMqttClient(
        host="192.168.1.1",
        serial_number=SERIAL,
        broker_config=config,
        snapshot_interval=snapshot_interval,
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
    async def test_no_package_metadata_is_read_on_the_event_loop(self, mqtt_client_mock: MagicMock) -> None:
        """connect() reads packaging metadata three ways, and all of it is file I/O.

        Entry-point enumeration and `version()` both open dist-info off disk;
        resolution imports the adapter package, which for `schema_1` means the
        eBus SDK and jsonschema. Home Assistant reported the lot — `listdir`,
        `read_text`, `open`, `scandir` — as blocking calls in the event loop and
        asked for a bug report, with the entry-point scan alone stalling setup
        for two seconds on a cold import cache.

        `version()` is watched because it was missed. Moving discovery off the
        loop left it behind in the same log statement, and Home Assistant kept
        reporting three blocking calls for a defect that read as fixed. A test
        naming only the operations already known about would have agreed.

        Asserted on the operations rather than on `resolve_adapter` running
        off-thread, because it is deliberately called twice: once in a thread to
        warm the cache, then again by `_build_adapter` on the loop, where a cache
        hit costs nothing. Watching the call would fail a correct implementation;
        watching the I/O is the actual property.
        """
        import threading

        from span_panel_api.adapters import _reset_adapter_cache
        from span_panel_api_schema_0 import SchemaZeroAdapter

        loop_thread = threading.get_ident()
        ran_on: dict[str, int] = {}

        class _RecordingEntryPoint:
            name = "schema_0"

            def load(self) -> object:
                ran_on["load"] = threading.get_ident()
                return SchemaZeroAdapter

        def _enumerate(group: str) -> list[_RecordingEntryPoint]:
            ran_on["enumerate"] = threading.get_ident()
            return [_RecordingEntryPoint()]

        def _version(name: str) -> str:
            ran_on["version"] = threading.get_ident()
            return "0.0.0-test"

        client = _make_span_client()
        _reset_adapter_cache()
        try:
            with (
                patch("span_panel_api.adapters.entry_points", side_effect=_enumerate),
                patch("span_panel_api.mqtt.client.version", side_effect=_version),
            ):
                connect_task = asyncio.create_task(client.connect())
                await asyncio.sleep(0.05)
                client._on_message(f"{TOPIC_PREFIX_SERIAL}/$description", MINIMAL_DESCRIPTION)
                client._on_message(f"{TOPIC_PREFIX_SERIAL}/$state", "ready")
                await asyncio.wait_for(connect_task, timeout=5.0)
        finally:
            # The fake registry is process-wide; leaving it cached would hand
            # every later test a single-entry-point environment.
            _reset_adapter_cache()

        assert set(ran_on) == {"enumerate", "load", "version"}, f"not all of it ran: {ran_on}"
        assert loop_thread not in ran_on.values(), f"metadata read on the event loop: {ran_on}"

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

        # Trigger a property message while streaming — timer scheduled
        client._on_message(f"{TOPIC_PREFIX_SERIAL}/core/some-prop", "42")
        assert client._snapshot_timer is not None

        # Fire the debounce directly (default 1.0s interval would slow the test)
        client._fire_snapshot()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

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


# ---------------------------------------------------------------------------
# AsyncMqttBridge — rebuild path (CA refresh / stale-state recovery)
# ---------------------------------------------------------------------------


async def _trigger_reconnect_loop(bridge: AsyncMqttBridge, mqtt_client_mock: MagicMock) -> None:
    """Drive the bridge into its reconnect loop via an _on_disconnect edge."""
    bridge._on_disconnect(
        mqtt_client_mock,
        None,
        DisconnectFlags(is_disconnect_packet_from_server=True),
        ReasonCode(packetType=2, aName="Success"),
        None,
    )
    assert bridge._reconnect_task is not None


class TestBridgeReconnectRebuild:
    """Verify the reconnect loop's CA-refresh / client-rebuild path."""

    @pytest.mark.asyncio
    async def test_ssl_error_triggers_immediate_rebuild(
        self, mqtt_client_mock: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A single ssl.SSLError on reconnect should fire a rebuild without
        waiting for the OSError threshold."""
        monkeypatch.setattr("span_panel_api.mqtt.connection.MQTT_RECONNECT_MIN_DELAY_S", 0.01)
        bridge = _make_bridge()
        await bridge.connect()

        # CA fetch count from initial connect — verify subsequent rebuild
        # increments it.
        from span_panel_api.mqtt import connection as conn_mod

        download_calls_before = conn_mod.download_ca_cert.call_count  # type: ignore[attr-defined]

        mqtt_client_mock.reconnect.side_effect = [ssl.SSLError("verify failed"), 0]

        await _trigger_reconnect_loop(bridge, mqtt_client_mock)
        await asyncio.sleep(0.2)

        # Rebuild fetched a fresh CA exactly once.
        assert conn_mod.download_ca_cert.call_count == download_calls_before + 1  # type: ignore[attr-defined]
        # Old client got disconnected during rebuild.
        mqtt_client_mock.disconnect.assert_called()

        await bridge.disconnect()

    @pytest.mark.asyncio
    async def test_oserror_threshold_triggers_rebuild(
        self, mqtt_client_mock: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Repeated OSError reconnect failures fire rebuild only after the
        configured threshold, not on the first or second failure."""
        monkeypatch.setattr("span_panel_api.mqtt.connection.MQTT_RECONNECT_MIN_DELAY_S", 0.01)
        bridge = _make_bridge()
        await bridge.connect()

        from span_panel_api.mqtt import connection as conn_mod

        download_calls_before = conn_mod.download_ca_cert.call_count  # type: ignore[attr-defined]

        # Three OSErrors, then success on the fourth call.
        mqtt_client_mock.reconnect.side_effect = [OSError("EOF"), OSError("EOF"), OSError("EOF"), 0]

        await _trigger_reconnect_loop(bridge, mqtt_client_mock)
        # Allow several loop iterations.
        await asyncio.sleep(0.6)

        # Rebuild fired exactly once across the threshold-many failures.
        assert conn_mod.download_ca_cert.call_count == download_calls_before + 1  # type: ignore[attr-defined]

        await bridge.disconnect()

    @pytest.mark.asyncio
    async def test_repeated_ssl_errors_each_trigger_rebuild(
        self, mqtt_client_mock: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SSL errors are not throttled by a once-per-outage flag — each
        independent SSL failure triggers its own rebuild attempt."""
        monkeypatch.setattr("span_panel_api.mqtt.connection.MQTT_RECONNECT_MIN_DELAY_S", 0.01)
        bridge = _make_bridge()
        await bridge.connect()

        from span_panel_api.mqtt import connection as conn_mod

        download_calls_before = conn_mod.download_ca_cert.call_count  # type: ignore[attr-defined]

        # Sustained outage: every reconnect raises SSL, AND the rebuild's
        # initial connect also fails (panel unreachable at connect time).
        # This keeps the loop running so multiple SSL errors can accumulate.
        mqtt_client_mock.reconnect.side_effect = ssl.SSLError("verify failed")
        mqtt_client_mock.connect.side_effect = OSError("connection refused")

        await _trigger_reconnect_loop(bridge, mqtt_client_mock)
        await asyncio.sleep(0.3)

        # Each SSL error fires its own rebuild attempt (counted via CA fetch).
        # Lower bound 2 because with min_delay=0.01 and 0.3s window we get
        # plenty of iterations; assert > 1 to prove the no-throttle behavior.
        rebuilds = conn_mod.download_ca_cert.call_count - download_calls_before  # type: ignore[attr-defined]
        assert rebuilds >= 3, f"expected each SSL to trigger rebuild, got {rebuilds}"

        await bridge.disconnect()

    @pytest.mark.asyncio
    async def test_extended_outage_cadence(self, mqtt_client_mock: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
        """During an extended OSError outage, rebuilds keep firing every
        MQTT_FULL_REBUILD_AFTER_FAILURES failures — not just once per outage."""
        monkeypatch.setattr("span_panel_api.mqtt.connection.MQTT_RECONNECT_MIN_DELAY_S", 0.005)
        bridge = _make_bridge()
        await bridge.connect()

        from span_panel_api.mqtt import connection as conn_mod

        download_calls_before = conn_mod.download_ca_cert.call_count  # type: ignore[attr-defined]

        # Sustained outage: every reconnect raises OSError and the rebuild's
        # initial connect also fails (panel unreachable throughout).
        mqtt_client_mock.reconnect.side_effect = OSError("EOF")
        mqtt_client_mock.connect.side_effect = OSError("connection refused")

        await _trigger_reconnect_loop(bridge, mqtt_client_mock)
        await asyncio.sleep(0.5)

        # We expect at least 2 rebuilds (across 3*2 = 6+ failures).
        rebuilds = conn_mod.download_ca_cert.call_count - download_calls_before  # type: ignore[attr-defined]
        assert rebuilds >= 2, f"expected >=2 rebuilds during extended outage, got {rebuilds}"

        await bridge.disconnect()

    @pytest.mark.asyncio
    async def test_failed_rebuild_preserves_old_client(
        self, mqtt_client_mock: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If download_ca_cert raises SpanPanelAPIError (e.g. HTTP 502),
        the rebuild bails out and the previous paho client is preserved."""
        monkeypatch.setattr("span_panel_api.mqtt.connection.MQTT_RECONNECT_MIN_DELAY_S", 0.01)
        bridge = _make_bridge()
        await bridge.connect()
        original_client = bridge._client
        assert original_client is not None

        from span_panel_api.mqtt import connection as conn_mod

        # CA endpoint returns 502 — rebuild must not crash the loop.
        monkeypatch.setattr(conn_mod, "download_ca_cert", AsyncMock(side_effect=SpanPanelAPIError("HTTP 502")))

        mqtt_client_mock.reconnect.side_effect = ssl.SSLError("verify failed")

        await _trigger_reconnect_loop(bridge, mqtt_client_mock)
        await asyncio.sleep(0.1)

        # The old client reference is preserved — rebuild failed before tearing it down.
        assert bridge._client is original_client
        # Bridge teardown intent stays consistent — reconnect loop did not die.
        assert bridge._should_reconnect is True

        await bridge.disconnect()

    @pytest.mark.asyncio
    async def test_failed_rebuild_resets_counter(self, mqtt_client_mock: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
        """After a failed rebuild attempt, the counter resets so the next
        rebuild fires only after another threshold-many failures, not on
        the immediate next iteration."""
        monkeypatch.setattr("span_panel_api.mqtt.connection.MQTT_RECONNECT_MIN_DELAY_S", 0.01)
        bridge = _make_bridge()
        await bridge.connect()

        from span_panel_api.mqtt import connection as conn_mod

        # First two CA fetches raise 502, then succeed.
        ca_mock = AsyncMock(
            side_effect=[
                SpanPanelAPIError("HTTP 502"),
                SpanPanelAPIError("HTTP 502"),
                "FAKE-PEM",
            ]
        )
        monkeypatch.setattr(conn_mod, "download_ca_cert", ca_mock)

        # Drive a stream of OSErrors. The threshold should fire rebuild every
        # MQTT_FULL_REBUILD_AFTER_FAILURES failures, and each attempt — even
        # if it fails at CA fetch — must reset the counter so we don't try
        # again on the very next iteration. Rebuild's connect must also fail
        # so the third (successful) CA fetch doesn't end the outage.
        mqtt_client_mock.reconnect.side_effect = OSError("EOF")
        mqtt_client_mock.connect.side_effect = OSError("connection refused")

        await _trigger_reconnect_loop(bridge, mqtt_client_mock)
        await asyncio.sleep(0.5)

        # We should see at most one rebuild attempt per
        # MQTT_FULL_REBUILD_AFTER_FAILURES iterations — not one per iteration.
        # With ~50 iterations available in 0.5s and threshold=3, we expect
        # roughly 16 rebuild attempts maximum, definitely not 50.
        attempts = ca_mock.call_count
        max_iterations = int(0.5 / 0.01)
        assert (
            attempts <= max_iterations // MQTT_FULL_REBUILD_AFTER_FAILURES + 2
        ), f"expected throttling — got {attempts} rebuild attempts in {max_iterations} iterations"

        await bridge.disconnect()

    @pytest.mark.asyncio
    async def test_no_ca_fetch_when_tls_disabled(self, mqtt_client_mock: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-TLS bridges skip the CA fetch entirely on rebuild but still
        rebuild the paho client (covering the stale-paho-state case)."""
        monkeypatch.setattr("span_panel_api.mqtt.connection.MQTT_RECONNECT_MIN_DELAY_S", 0.01)
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

        from span_panel_api.mqtt import connection as conn_mod

        # SSL error wouldn't happen on a plain-TCP bridge, but the threshold
        # path still fires on persistent OSError. Rebuild's connect also
        # fails to extend the outage.
        mqtt_client_mock.reconnect.side_effect = OSError("EOF")
        mqtt_client_mock.connect.side_effect = OSError("connection refused")

        download_calls_before = conn_mod.download_ca_cert.call_count  # type: ignore[attr-defined]

        await _trigger_reconnect_loop(bridge, mqtt_client_mock)
        await asyncio.sleep(0.2)

        # CA never fetched on a non-TLS bridge, even though the rebuild path ran.
        assert conn_mod.download_ca_cert.call_count == download_calls_before  # type: ignore[attr-defined]

        await bridge.disconnect()

    @pytest.mark.asyncio
    async def test_pre_rebuild_callback_fires_before_old_client_torn_down(
        self, mqtt_client_mock: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The pre-rebuild callback must fire before the bridge calls
        disconnect() on the old paho client, so SpanMqttClient can reset
        its accumulator while the original client is still wired."""
        monkeypatch.setattr("span_panel_api.mqtt.connection.MQTT_RECONNECT_MIN_DELAY_S", 0.01)
        bridge = _make_bridge()
        await bridge.connect()

        observed_order: list[str] = []

        def pre_rebuild_hook() -> None:
            # mqtt_client_mock.disconnect is the old-client teardown call.
            observed_order.append(
                "pre_rebuild_then_disconnect"
                if mqtt_client_mock.disconnect.call_count == 0
                else "pre_rebuild_after_disconnect"
            )

        bridge.set_pre_rebuild_callback(pre_rebuild_hook)

        mqtt_client_mock.reconnect.side_effect = ssl.SSLError("verify failed")

        await _trigger_reconnect_loop(bridge, mqtt_client_mock)
        await asyncio.sleep(0.1)

        assert observed_order == ["pre_rebuild_then_disconnect"]

        await bridge.disconnect()

    @pytest.mark.asyncio
    async def test_pre_rebuild_callback_exception_does_not_break_rebuild(
        self, mqtt_client_mock: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A misbehaving pre-rebuild callback must not abort the rebuild."""
        monkeypatch.setattr("span_panel_api.mqtt.connection.MQTT_RECONNECT_MIN_DELAY_S", 0.01)
        bridge = _make_bridge()
        await bridge.connect()

        bridge.set_pre_rebuild_callback(lambda: (_ for _ in ()).throw(RuntimeError("boom")))

        from span_panel_api.mqtt import connection as conn_mod

        download_calls_before = conn_mod.download_ca_cert.call_count  # type: ignore[attr-defined]

        mqtt_client_mock.reconnect.side_effect = ssl.SSLError("verify failed")

        await _trigger_reconnect_loop(bridge, mqtt_client_mock)
        await asyncio.sleep(0.1)

        # Rebuild proceeded despite callback raising.
        assert conn_mod.download_ca_cert.call_count == download_calls_before + 1  # type: ignore[attr-defined]

        await bridge.disconnect()


# ---------------------------------------------------------------------------
# SpanMqttClient — accumulator reset on bridge rebuild
# ---------------------------------------------------------------------------


class TestSpanMqttClientAccumulatorReset:
    """Verify the pre-rebuild hook resets Homie state while preserving schema."""

    @pytest.mark.asyncio
    async def test_pre_rebuild_resets_accumulator(self, mqtt_client_mock: MagicMock) -> None:
        """`_on_pre_rebuild` replaces the adapter (and its internal accumulator/
        consumer) with a fresh instance."""
        client = _make_span_client()

        connect_task = asyncio.create_task(client.connect())
        await asyncio.sleep(0.05)
        client._on_message(f"{TOPIC_PREFIX_SERIAL}/$description", MINIMAL_DESCRIPTION)
        client._on_message(f"{TOPIC_PREFIX_SERIAL}/$state", "ready")
        await asyncio.wait_for(connect_task, timeout=5.0)

        original_adapter = client._adapter
        assert original_adapter is not None
        # Adapter is in a ready-ish state from the simulated Homie messages.
        assert original_adapter.is_ready() is True

        # Trigger the pre-rebuild hook directly — same call the bridge makes.
        client._on_pre_rebuild()

        # New adapter instance, fresh state.
        assert client._adapter is not original_adapter
        assert client._adapter is not None
        assert client._adapter.is_ready() is False

        await client.close()

    @pytest.mark.asyncio
    async def test_pre_rebuild_preserves_schema_state(self, mqtt_client_mock: MagicMock) -> None:
        """Schema-derived state must survive across pre-rebuild — schema cannot change in-session."""
        client = _make_span_client()

        connect_task = asyncio.create_task(client.connect())
        await asyncio.sleep(0.05)
        client._on_message(f"{TOPIC_PREFIX_SERIAL}/$description", MINIMAL_DESCRIPTION)
        client._on_message(f"{TOPIC_PREFIX_SERIAL}/$state", "ready")
        await asyncio.wait_for(connect_task, timeout=5.0)

        schema_hash_before = client._schema_hash
        schema_types_before = client._previous_schema_types
        schema_before = client._schema
        field_metadata_before = client.field_metadata
        assert field_metadata_before is not None

        client._on_pre_rebuild()

        assert client._schema_hash == schema_hash_before
        assert client._previous_schema_types == schema_types_before
        assert client._schema == schema_before

        # `field_metadata` reads the live adapter rather than a cache, so the
        # fresh accumulator legitimately reads None until the new subscription's
        # retained messages repopulate the tree. What survives the rebuild is the
        # schema-derived *input*, observable as the same mapping once ready again.
        assert client.field_metadata is None
        client._on_message(f"{TOPIC_PREFIX_SERIAL}/$description", MINIMAL_DESCRIPTION)
        client._on_message(f"{TOPIC_PREFIX_SERIAL}/$state", "ready")
        assert client.field_metadata == field_metadata_before

        await client.close()

    @pytest.mark.asyncio
    async def test_pre_rebuild_before_connect_is_noop(self) -> None:
        """If pre-rebuild somehow fires before connect() completes, the
        handler must not raise — there is no accumulator state to reset."""
        client = _make_span_client()
        # _schema is None because connect() never ran.
        client._on_pre_rebuild()
        # No exception, no state changes.
        assert client._adapter is None


# ---------------------------------------------------------------------------
# AsyncMqttBridge — rebuild path: hardening / edge cases
# ---------------------------------------------------------------------------


class TestBridgeRebuildHardening:
    """Edge-case coverage that guards against the reconnect task dying."""

    @pytest.mark.asyncio
    async def test_rebuild_returns_false_when_loop_is_none(self, mqtt_client_mock: MagicMock) -> None:
        """_rebuild_client must short-circuit if the bridge has no loop yet
        (e.g., called against a freshly-constructed but never-connected bridge)."""
        bridge = _make_bridge()
        # Skip connect() — bridge._loop is None.
        result = await bridge._rebuild_client()
        assert result is False
        # No side effects: no client, no CA fetch, no warnings.
        assert bridge._client is None

    @pytest.mark.asyncio
    async def test_make_paho_client_raising_does_not_kill_loop(
        self, mqtt_client_mock: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If _make_paho_client raises during rebuild, the reconnect loop
        must survive — the broad exception catch returns False so the
        outer loop keeps spinning across multiple rebuild attempts."""
        monkeypatch.setattr("span_panel_api.mqtt.connection.MQTT_RECONNECT_MIN_DELAY_S", 0.01)
        bridge = _make_bridge()
        await bridge.connect()

        from span_panel_api.mqtt import connection as conn_mod

        # _make_paho_client always raises during this outage — simulates an
        # unexpected paho construction failure that would otherwise leak.
        def always_boom(ssl_context: ssl.SSLContext | None) -> object:
            raise RuntimeError("simulated paho construction failure")

        monkeypatch.setattr(bridge, "_make_paho_client", always_boom)

        mqtt_client_mock.reconnect.side_effect = ssl.SSLError("verify failed")

        download_calls_before = conn_mod.download_ca_cert.call_count  # type: ignore[attr-defined]

        await _trigger_reconnect_loop(bridge, mqtt_client_mock)
        await asyncio.sleep(0.15)

        # The loop survived multiple iterations of (SSL error → rebuild attempt →
        # _make_paho_client raises). If the broad catch were missing, the very
        # first failure would have killed the task and download_ca_cert would
        # have been called exactly once. We expect at least 2 attempts.
        rebuild_attempts = conn_mod.download_ca_cert.call_count - download_calls_before  # type: ignore[attr-defined]
        assert rebuild_attempts >= 2, f"reconnect loop died after _make_paho_client error: only {rebuild_attempts} attempts"
        # Task is still alive and bridge teardown semantics intact.
        assert bridge._reconnect_task is not None
        assert not bridge._reconnect_task.done(), "reconnect loop died on _make_paho_client error"
        assert bridge._should_reconnect is True

        await bridge.disconnect()

    @pytest.mark.asyncio
    async def test_unknown_exception_does_not_trigger_rebuild(
        self, mqtt_client_mock: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The design explicitly says unknown exceptions in the reconnect path
        must NOT trigger a rebuild — recovery actions should not be applied
        to error classes whose effect we cannot predict."""
        monkeypatch.setattr("span_panel_api.mqtt.connection.MQTT_RECONNECT_MIN_DELAY_S", 0.01)
        bridge = _make_bridge()
        await bridge.connect()

        from span_panel_api.mqtt import connection as conn_mod

        download_calls_before = conn_mod.download_ca_cert.call_count  # type: ignore[attr-defined]

        # A non-OSError, non-SSL exception falls through to the unknown branch.
        class WeirdProtocolError(Exception):
            pass

        mqtt_client_mock.reconnect.side_effect = WeirdProtocolError("???")

        await _trigger_reconnect_loop(bridge, mqtt_client_mock)
        await asyncio.sleep(0.2)

        # Many failures should have accumulated but no rebuild fires —
        # download_ca_cert call count is unchanged.
        assert conn_mod.download_ca_cert.call_count == download_calls_before  # type: ignore[attr-defined]

        await bridge.disconnect()

    @pytest.mark.asyncio
    async def test_pre_rebuild_callback_not_fired_when_ca_fetch_fails(
        self, mqtt_client_mock: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the CA fetch fails, the rebuild returns False *before* firing
        the pre-rebuild callback. The accumulator should not be reset for a
        rebuild that never happened."""
        monkeypatch.setattr("span_panel_api.mqtt.connection.MQTT_RECONNECT_MIN_DELAY_S", 0.01)
        bridge = _make_bridge()
        await bridge.connect()

        from span_panel_api.mqtt import connection as conn_mod

        callback_fired = {"n": 0}

        def pre_rebuild_hook() -> None:
            callback_fired["n"] += 1

        bridge.set_pre_rebuild_callback(pre_rebuild_hook)

        # CA fetch fails for all attempts during this outage.
        monkeypatch.setattr(conn_mod, "download_ca_cert", AsyncMock(side_effect=SpanPanelAPIError("HTTP 502")))

        mqtt_client_mock.reconnect.side_effect = ssl.SSLError("verify failed")

        await _trigger_reconnect_loop(bridge, mqtt_client_mock)
        await asyncio.sleep(0.15)

        # Pre-rebuild callback must not fire — the rebuild bailed at CA fetch.
        assert callback_fired["n"] == 0

        await bridge.disconnect()

    @pytest.mark.asyncio
    async def test_client_assigned_before_executor_await(
        self, mqtt_client_mock: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The bridge's `_client` reference must point at the new client
        before the executor await — so a CONNACK arriving during the await
        sees the right client when callbacks dispatch to bridge.subscribe."""
        monkeypatch.setattr("span_panel_api.mqtt.connection.MQTT_RECONNECT_MIN_DELAY_S", 0.01)
        bridge = _make_bridge()
        await bridge.connect()
        original_client = bridge._client

        # Capture the state of self._client at the moment _blocking_connect runs.
        # Since mqtt_client_mock is the same MagicMock instance for old and new
        # client, we cannot distinguish identity — but we can confirm the
        # assignment happens before the executor by checking that bridge._client
        # is set when the mock's connect side_effect fires.
        observed_client_at_connect: list[object | None] = []

        original_connect = mqtt_client_mock.connect.side_effect

        def capturing_connect(*args: object, **kwargs: object) -> int:
            observed_client_at_connect.append(bridge._client)
            assert callable(original_connect)
            return original_connect(*args, **kwargs)  # type: ignore[no-any-return]

        mqtt_client_mock.connect.side_effect = capturing_connect

        mqtt_client_mock.reconnect.side_effect = ssl.SSLError("verify failed")

        await _trigger_reconnect_loop(bridge, mqtt_client_mock)
        await asyncio.sleep(0.15)

        # The mock connect fired at least once with bridge._client already
        # set (not None and not pointing somewhere else).
        assert observed_client_at_connect, "rebuild path never invoked connect"
        for observed in observed_client_at_connect:
            assert observed is not None, "bridge._client was None at connect time"

        # After the rebuild, the original client reference is still the same
        # mock (singleton behavior of MagicMock.return_value).
        assert bridge._client is original_client  # same MagicMock instance

        await bridge.disconnect()
