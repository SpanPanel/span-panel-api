"""Tests for MQTT snapshot debounce timer in SpanMqttClient.

Exercises the snapshot_interval parameter and debounce logic:
- Multiple rapid messages → single dispatch
- Snapshot fires after configured interval
- close() cancels pending timer
- interval <= 0 dispatches immediately (real-time mode)
- set_snapshot_interval() runtime changes
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from span_panel_api.mqtt.client import SpanMqttClient
from span_panel_api.mqtt.models import MqttClientConfig

from conftest import MINIMAL_DESCRIPTION, SERIAL, TOPIC_PREFIX_SERIAL


def _make_client(snapshot_interval: float = 1.0) -> SpanMqttClient:
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


async def _connect_client(client: SpanMqttClient, mqtt_client_mock: MagicMock) -> None:
    """Connect the client and bring Homie to ready state."""
    connect_task = asyncio.create_task(client.connect())
    await asyncio.sleep(0.05)
    client._on_message(f"{TOPIC_PREFIX_SERIAL}/$description", MINIMAL_DESCRIPTION)
    client._on_message(f"{TOPIC_PREFIX_SERIAL}/$state", "ready")
    await asyncio.wait_for(connect_task, timeout=5.0)


class TestSnapshotDebounce:
    """Test debounce timer behavior with snapshot_interval >= 1.0s.

    These tests drive the timer callback directly rather than waiting for
    real wall-clock timers to fire — enforcing the 1.0s minimum would
    otherwise make every test slow.
    """

    @pytest.mark.asyncio
    async def test_multiple_messages_single_dispatch(self, mqtt_client_mock: MagicMock) -> None:
        """Multiple rapid MQTT messages should schedule only one timer."""
        client = _make_client(snapshot_interval=1.0)
        await _connect_client(client, mqtt_client_mock)

        snapshots: list[object] = []
        callback = AsyncMock(side_effect=lambda s: snapshots.append(s))
        client.register_snapshot_callback(callback)
        await client.start_streaming()

        # Fire 10 rapid messages — only one timer should be scheduled
        for i in range(10):
            client._on_message(f"{TOPIC_PREFIX_SERIAL}/core/power", str(i * 100))

        # Before timer fires: no snapshots yet, but a single timer exists
        assert len(snapshots) == 0
        assert client._snapshot_timer is not None

        # Fire the debounce directly
        client._fire_snapshot()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        # Exactly one snapshot dispatched
        assert len(snapshots) == 1
        callback.assert_called_once()

        await client.stop_streaming()
        await client.close()

    @pytest.mark.asyncio
    async def test_snapshot_does_not_fire_before_interval(self, mqtt_client_mock: MagicMock) -> None:
        """Snapshot is only scheduled, not dispatched, until the timer fires."""
        client = _make_client(snapshot_interval=1.0)
        await _connect_client(client, mqtt_client_mock)

        snapshots: list[object] = []
        callback = AsyncMock(side_effect=lambda s: snapshots.append(s))
        client.register_snapshot_callback(callback)
        await client.start_streaming()

        client._on_message(f"{TOPIC_PREFIX_SERIAL}/core/power", "1000")

        # Timer scheduled but not yet fired
        assert client._snapshot_timer is not None
        assert len(snapshots) == 0

        # Drive the timer
        client._fire_snapshot()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert len(snapshots) == 1

        await client.stop_streaming()
        await client.close()

    @pytest.mark.asyncio
    async def test_close_cancels_pending_timer(self, mqtt_client_mock: MagicMock) -> None:
        """close() should cancel any pending debounce timer."""
        client = _make_client(snapshot_interval=1.0)
        await _connect_client(client, mqtt_client_mock)

        snapshots: list[object] = []
        callback = AsyncMock(side_effect=lambda s: snapshots.append(s))
        client.register_snapshot_callback(callback)
        await client.start_streaming()

        # Trigger a message to start the timer
        client._on_message(f"{TOPIC_PREFIX_SERIAL}/core/power", "500")
        assert client._snapshot_timer is not None

        # Close before timer fires
        await client.close()
        assert client._snapshot_timer is None

        # Wait past when the timer would have fired
        await asyncio.sleep(1.2)
        assert len(snapshots) == 0

    @pytest.mark.asyncio
    async def test_stop_streaming_cancels_timer(self, mqtt_client_mock: MagicMock) -> None:
        """stop_streaming() should cancel any pending debounce timer."""
        client = _make_client(snapshot_interval=1.0)
        await _connect_client(client, mqtt_client_mock)

        snapshots: list[object] = []
        callback = AsyncMock(side_effect=lambda s: snapshots.append(s))
        client.register_snapshot_callback(callback)
        await client.start_streaming()

        client._on_message(f"{TOPIC_PREFIX_SERIAL}/core/power", "500")
        assert client._snapshot_timer is not None

        await client.stop_streaming()
        assert client._snapshot_timer is None

        await asyncio.sleep(1.2)
        assert len(snapshots) == 0

        await client.close()

    @pytest.mark.asyncio
    async def test_second_batch_after_timer_fires(self, mqtt_client_mock: MagicMock) -> None:
        """A new batch of messages after timer fires should start a new timer."""
        client = _make_client(snapshot_interval=1.0)
        await _connect_client(client, mqtt_client_mock)

        snapshots: list[object] = []
        callback = AsyncMock(side_effect=lambda s: snapshots.append(s))
        client.register_snapshot_callback(callback)
        await client.start_streaming()

        # First batch
        client._on_message(f"{TOPIC_PREFIX_SERIAL}/core/power", "100")
        assert client._snapshot_timer is not None
        client._fire_snapshot()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(snapshots) == 1
        assert client._snapshot_timer is None

        # Second batch
        client._on_message(f"{TOPIC_PREFIX_SERIAL}/core/power", "200")
        assert client._snapshot_timer is not None
        client._fire_snapshot()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(snapshots) == 2

        await client.stop_streaming()
        await client.close()


class TestSnapshotRealtimeMode:
    """interval <= 0 disables debounce for real-time dispatch."""

    @pytest.mark.asyncio
    async def test_zero_interval_dispatches_immediately(self, mqtt_client_mock: MagicMock) -> None:
        """interval=0 should dispatch a snapshot for every message."""
        client = _make_client(snapshot_interval=0)
        await _connect_client(client, mqtt_client_mock)

        snapshots: list[object] = []
        callback = AsyncMock(side_effect=lambda s: snapshots.append(s))
        client.register_snapshot_callback(callback)
        await client.start_streaming()

        client._on_message(f"{TOPIC_PREFIX_SERIAL}/core/power", "100")
        client._on_message(f"{TOPIC_PREFIX_SERIAL}/core/power", "200")
        client._on_message(f"{TOPIC_PREFIX_SERIAL}/core/power", "300")

        # Let dispatch tasks complete
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert len(snapshots) == 3
        assert client._snapshot_timer is None

        await client.stop_streaming()
        await client.close()

    @pytest.mark.asyncio
    async def test_negative_interval_dispatches_immediately(self, mqtt_client_mock: MagicMock) -> None:
        """Negative interval should behave like 0 (no debounce)."""
        client = _make_client(snapshot_interval=-1.0)
        await _connect_client(client, mqtt_client_mock)

        snapshots: list[object] = []
        callback = AsyncMock(side_effect=lambda s: snapshots.append(s))
        client.register_snapshot_callback(callback)
        await client.start_streaming()

        client._on_message(f"{TOPIC_PREFIX_SERIAL}/core/power", "100")
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert len(snapshots) == 1
        assert client._snapshot_timer is None

        await client.stop_streaming()
        await client.close()


class TestSetSnapshotInterval:
    """Test runtime snapshot interval changes."""

    @pytest.mark.asyncio
    async def test_set_snapshot_interval_cancels_timer(self, mqtt_client_mock: MagicMock) -> None:
        """Changing interval at runtime should cancel any pending timer."""
        client = _make_client(snapshot_interval=2.0)
        await _connect_client(client, mqtt_client_mock)

        callback = AsyncMock()
        client.register_snapshot_callback(callback)
        await client.start_streaming()

        client._on_message(f"{TOPIC_PREFIX_SERIAL}/core/power", "100")
        assert client._snapshot_timer is not None

        client.set_snapshot_interval(5.0)
        assert client._snapshot_timer is None
        assert client._snapshot_interval == 5.0

        await client.stop_streaming()
        await client.close()

    @pytest.mark.asyncio
    async def test_set_interval_to_zero_switches_to_immediate(self, mqtt_client_mock: MagicMock) -> None:
        """set_snapshot_interval(0) switches to real-time dispatch."""
        client = _make_client(snapshot_interval=2.0)
        await _connect_client(client, mqtt_client_mock)

        snapshots: list[object] = []
        callback = AsyncMock(side_effect=lambda s: snapshots.append(s))
        client.register_snapshot_callback(callback)
        await client.start_streaming()

        client.set_snapshot_interval(0)

        client._on_message(f"{TOPIC_PREFIX_SERIAL}/core/power", "100")
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert len(snapshots) == 1

        await client.stop_streaming()
        await client.close()

    def test_default_snapshot_interval(self) -> None:
        """Default snapshot_interval should be 1.0 seconds."""
        config = MqttClientConfig(
            broker_host="broker.local",
            username="user",
            password="pass",
        )
        client = SpanMqttClient(
            host="192.168.1.1",
            serial_number=SERIAL,
            broker_config=config,
        )
        assert client._snapshot_interval == 1.0
