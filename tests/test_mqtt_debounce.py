"""Tests for MQTT snapshot debounce timer in SpanMqttClient.

Exercises the snapshot_interval parameter and debounce logic:
- Multiple rapid messages → single dispatch
- Snapshot fires after configured interval
- close() cancels pending timer
- interval=0 dispatches immediately (backward compat)
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
    """Test debounce timer behavior with snapshot_interval > 0."""

    @pytest.mark.asyncio
    async def test_multiple_messages_single_dispatch(self, mqtt_client_mock: MagicMock) -> None:
        """Multiple rapid MQTT messages should produce only one snapshot dispatch."""
        client = _make_client(snapshot_interval=0.2)
        await _connect_client(client, mqtt_client_mock)

        snapshots: list[object] = []
        callback = AsyncMock(side_effect=lambda s: snapshots.append(s))
        client.register_snapshot_callback(callback)
        await client.start_streaming()

        # Fire 10 rapid messages — only one timer should be scheduled
        for i in range(10):
            client._on_message(f"{TOPIC_PREFIX_SERIAL}/core/power", str(i * 100))

        # Before timer fires: no snapshots yet
        assert len(snapshots) == 0

        # Wait for debounce timer to fire
        await asyncio.sleep(0.35)

        # Exactly one snapshot dispatched
        assert len(snapshots) == 1
        callback.assert_called_once()

        await client.stop_streaming()
        await client.close()

    @pytest.mark.asyncio
    async def test_snapshot_fires_after_interval(self, mqtt_client_mock: MagicMock) -> None:
        """Snapshot dispatches after the configured interval, not immediately."""
        client = _make_client(snapshot_interval=0.3)
        await _connect_client(client, mqtt_client_mock)

        snapshots: list[object] = []
        callback = AsyncMock(side_effect=lambda s: snapshots.append(s))
        client.register_snapshot_callback(callback)
        await client.start_streaming()

        client._on_message(f"{TOPIC_PREFIX_SERIAL}/core/power", "1000")

        # Not yet fired at half the interval
        await asyncio.sleep(0.15)
        assert len(snapshots) == 0

        # Fired after full interval
        await asyncio.sleep(0.25)
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
        client = _make_client(snapshot_interval=0.15)
        await _connect_client(client, mqtt_client_mock)

        snapshots: list[object] = []
        callback = AsyncMock(side_effect=lambda s: snapshots.append(s))
        client.register_snapshot_callback(callback)
        await client.start_streaming()

        # First batch
        client._on_message(f"{TOPIC_PREFIX_SERIAL}/core/power", "100")
        await asyncio.sleep(0.25)
        assert len(snapshots) == 1

        # Second batch
        client._on_message(f"{TOPIC_PREFIX_SERIAL}/core/power", "200")
        await asyncio.sleep(0.25)
        assert len(snapshots) == 2

        await client.stop_streaming()
        await client.close()


class TestSnapshotNoDebounce:
    """Test interval=0 preserves immediate dispatch behavior."""

    @pytest.mark.asyncio
    async def test_zero_interval_dispatches_immediately(self, mqtt_client_mock: MagicMock) -> None:
        """interval=0 should dispatch a snapshot for every message (no debounce)."""
        client = _make_client(snapshot_interval=0)
        await _connect_client(client, mqtt_client_mock)

        snapshots: list[object] = []
        callback = AsyncMock(side_effect=lambda s: snapshots.append(s))
        client.register_snapshot_callback(callback)
        await client.start_streaming()

        # Each message should trigger an immediate dispatch task
        client._on_message(f"{TOPIC_PREFIX_SERIAL}/core/power", "100")
        client._on_message(f"{TOPIC_PREFIX_SERIAL}/core/power", "200")
        client._on_message(f"{TOPIC_PREFIX_SERIAL}/core/power", "300")

        # Let tasks complete
        await asyncio.sleep(0.1)

        # Three separate dispatches
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
        await asyncio.sleep(0.05)

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
        """Changing from debounce to zero should switch to immediate dispatch."""
        client = _make_client(snapshot_interval=2.0)
        await _connect_client(client, mqtt_client_mock)

        snapshots: list[object] = []
        callback = AsyncMock(side_effect=lambda s: snapshots.append(s))
        client.register_snapshot_callback(callback)
        await client.start_streaming()

        # Switch to immediate mode
        client.set_snapshot_interval(0)

        client._on_message(f"{TOPIC_PREFIX_SERIAL}/core/power", "100")
        await asyncio.sleep(0.05)

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
