"""Tests for SpanMqttClient connection callbacks and get_snapshot() liveness guards."""

from __future__ import annotations

import logging

import pytest

from span_panel_api.exceptions import SpanPanelError, SpanPanelStaleDataError
from span_panel_api.models import SpanPanelSnapshot
from span_panel_api.mqtt.client import SpanMqttClient
from span_panel_api.mqtt.connection import AsyncMqttBridge
from span_panel_api.mqtt.const import WILDCARD_TOPIC_FMT
from span_panel_api.mqtt.homie import HomieDeviceConsumer
from span_panel_api.mqtt.models import MqttClientConfig


def _make_client() -> SpanMqttClient:
    """Build a SpanMqttClient without I/O for unit testing."""
    config = MqttClientConfig(
        broker_host="127.0.0.1",
        username="test",
        password="test",
    )
    return SpanMqttClient("127.0.0.1", "test-serial", config)


class _FakeBridge(AsyncMqttBridge):
    """Minimal bridge stub for get_snapshot() and fan-out tests.

    Bypasses AsyncMqttBridge.__init__ (which does TLS/CA/network setup) —
    only is_connected() and subscribe() are invoked on this stub.
    """

    def __init__(self, connected: bool = True) -> None:
        # Intentionally do not call super().__init__ — avoids I/O setup.
        self._connected = connected
        self.subscribed_topics: list[tuple[str, int]] = []

    def is_connected(self) -> bool:
        return self._connected

    def subscribe(self, topic: str, qos: int = 0) -> None:
        self.subscribed_topics.append((topic, qos))


class _FakeHomie(HomieDeviceConsumer):
    """Minimal Homie stub for get_snapshot() tests.

    Bypasses HomieDeviceConsumer.__init__ — only is_ready() and
    build_snapshot() are invoked on this stub.
    """

    def __init__(self, ready: bool = True, snapshot: SpanPanelSnapshot | None = None) -> None:
        # Intentionally do not call super().__init__ — avoids accumulator setup.
        self._ready_flag = ready
        self._snapshot = snapshot

    def is_ready(self) -> bool:
        return self._ready_flag

    def build_snapshot(self) -> SpanPanelSnapshot:
        if self._snapshot is None:
            raise RuntimeError("_FakeHomie: no snapshot configured")
        return self._snapshot


class TestRegisterConnectionCallback:
    """Callback subscription API — structural only (fan-out is tested in Task 4)."""

    def test_register_returns_unregister_function(self) -> None:
        client = _make_client()
        unregister = client.register_connection_callback(lambda _c: None)
        assert callable(unregister)

    def test_register_appends_to_callback_list(self) -> None:
        client = _make_client()
        cb = lambda _c: None  # noqa: E731
        client.register_connection_callback(cb)
        assert cb in client._connection_callbacks

    def test_unregister_removes_from_callback_list(self) -> None:
        client = _make_client()
        cb = lambda _c: None  # noqa: E731
        unregister = client.register_connection_callback(cb)
        unregister()
        assert cb not in client._connection_callbacks

    def test_double_unregister_is_noop(self) -> None:
        client = _make_client()
        unregister = client.register_connection_callback(lambda _c: None)
        unregister()
        unregister()  # must not raise


class TestConnectionEventDispatch:
    """Edge-only fan-out in _on_connection_change."""

    def test_multiple_callbacks_all_fire(self) -> None:
        client = _make_client()
        client._bridge = _FakeBridge(connected=True)
        calls_a: list[bool] = []
        calls_b: list[bool] = []
        client.register_connection_callback(calls_a.append)
        client.register_connection_callback(calls_b.append)

        client._on_connection_change(True)

        assert calls_a == [True]
        assert calls_b == [True]

    def test_unregister_prevents_future_calls(self) -> None:
        client = _make_client()
        client._bridge = _FakeBridge(connected=True)
        calls: list[bool] = []
        unregister = client.register_connection_callback(calls.append)
        unregister()

        client._on_connection_change(True)

        assert calls == []

    def test_initial_false_to_true_fires_online(self) -> None:
        client = _make_client()
        client._bridge = _FakeBridge(connected=True)
        calls: list[bool] = []
        client.register_connection_callback(calls.append)

        client._on_connection_change(True)

        assert calls == [True]
        assert client._live is True

    def test_true_to_false_fires_offline(self) -> None:
        client = _make_client()
        client._bridge = _FakeBridge(connected=True)
        client._live = True
        calls: list[bool] = []
        client.register_connection_callback(calls.append)

        client._on_connection_change(False)

        assert calls == [False]
        assert client._live is False

    def test_duplicate_true_suppressed(self) -> None:
        client = _make_client()
        client._bridge = _FakeBridge(connected=True)
        client._live = True
        calls: list[bool] = []
        client.register_connection_callback(calls.append)

        client._on_connection_change(True)

        assert calls == []

    def test_duplicate_false_suppressed(self) -> None:
        client = _make_client()
        client._bridge = _FakeBridge(connected=False)
        client._live = False
        calls: list[bool] = []
        client.register_connection_callback(calls.append)

        client._on_connection_change(False)

        assert calls == []

    def test_callback_exception_does_not_break_fanout(self, caplog: pytest.LogCaptureFixture) -> None:
        client = _make_client()
        client._bridge = _FakeBridge(connected=True)

        def bad(_connected: bool) -> None:
            raise RuntimeError("intentional")

        good_calls: list[bool] = []
        client.register_connection_callback(bad)
        client.register_connection_callback(good_calls.append)

        with caplog.at_level(logging.ERROR):
            client._on_connection_change(True)

        assert good_calls == [True]
        assert any("Connection callback raised" in r.message for r in caplog.records)

    def test_unregister_during_fanout_safe(self) -> None:
        client = _make_client()
        client._bridge = _FakeBridge(connected=True)

        order: list[str] = []
        unregister_holder: dict[str, object] = {}

        def first(_connected: bool) -> None:
            order.append("first")
            unregister_fn = unregister_holder["unregister"]
            assert callable(unregister_fn)
            unregister_fn()

        def second(_connected: bool) -> None:
            order.append("second")

        unregister_holder["unregister"] = client.register_connection_callback(first)
        client.register_connection_callback(second)

        client._on_connection_change(True)

        assert order == ["first", "second"]
        assert first not in client._connection_callbacks

    def test_reconnect_triggers_resubscribe_and_callback(self) -> None:
        client = _make_client()
        bridge = _FakeBridge(connected=True)
        client._bridge = bridge
        client._live = False  # was offline
        calls: list[bool] = []
        client.register_connection_callback(calls.append)

        client._on_connection_change(True)

        expected_topic = WILDCARD_TOPIC_FMT.format(serial="test-serial")
        assert len(bridge.subscribed_topics) == 1
        assert bridge.subscribed_topics[0] == (expected_topic, 0)
        assert calls == [True]

    def test_resubscribe_fires_even_on_duplicate_true(self) -> None:
        """Duplicate connected=True still triggers re-subscribe (intentional).

        Paho may re-emit connected events; re-subscribing is broker-benign
        and ensures subscriptions survive session restoration. Callback
        fan-out is separately edge-only.
        """
        client = _make_client()
        bridge = _FakeBridge(connected=True)
        client._bridge = bridge
        client._live = True  # already online
        calls: list[bool] = []
        client.register_connection_callback(calls.append)

        client._on_connection_change(True)

        # Re-subscribe fires (side effect preserved) but no callback edge
        assert len(bridge.subscribed_topics) == 1
        assert calls == []


def _make_sentinel_snapshot() -> SpanPanelSnapshot:
    """Build a minimal SpanPanelSnapshot for identity-assertion tests.

    Field values are arbitrary — tests only assert object identity
    (`snapshot is sentinel`), not content. If SpanPanelSnapshot grows
    a new required field, add it here with a zero/empty default; no
    test assertion needs to change.
    """
    return SpanPanelSnapshot(
        serial_number="test-serial",
        firmware_version="0.0.0",
        main_relay_state="CLOSED",
        instant_grid_power_w=0.0,
        feedthrough_power_w=0.0,
        main_meter_energy_consumed_wh=0.0,
        main_meter_energy_produced_wh=0.0,
        feedthrough_energy_consumed_wh=0.0,
        feedthrough_energy_produced_wh=0.0,
        dsm_state="DSM_GRID_OK",
        current_run_config="PANEL_ON_GRID",
        door_state="CLOSED",
        proximity_proven=True,
        uptime_s=0,
        eth0_link=False,
        wlan_link=False,
        wwan_link=False,
        panel_size=32,
    )


class TestGetSnapshotLiveness:
    """get_snapshot() must raise SpanPanelStaleDataError when not live."""

    async def test_raises_stale_when_bridge_none(self) -> None:
        client = _make_client()
        client._bridge = None
        client._homie = _FakeHomie(ready=True)

        with pytest.raises(SpanPanelStaleDataError) as exc_info:
            await client.get_snapshot()
        assert "not connected" in str(exc_info.value).lower()

    async def test_raises_stale_when_homie_none(self) -> None:
        client = _make_client()
        client._bridge = _FakeBridge(connected=True)
        client._homie = None

        with pytest.raises(SpanPanelStaleDataError) as exc_info:
            await client.get_snapshot()
        assert "not connected" in str(exc_info.value).lower()

    async def test_raises_stale_when_broker_disconnected(self) -> None:
        client = _make_client()
        client._bridge = _FakeBridge(connected=False)
        client._homie = _FakeHomie(ready=True)

        with pytest.raises(SpanPanelStaleDataError) as exc_info:
            await client.get_snapshot()
        assert "broker" in str(exc_info.value).lower()

    async def test_raises_stale_when_homie_not_ready(self) -> None:
        client = _make_client()
        client._bridge = _FakeBridge(connected=True)
        client._homie = _FakeHomie(ready=False)

        with pytest.raises(SpanPanelStaleDataError) as exc_info:
            await client.get_snapshot()
        assert "not ready" in str(exc_info.value).lower()

    async def test_returns_snapshot_when_fully_live(self) -> None:
        sentinel = _make_sentinel_snapshot()
        client = _make_client()
        client._bridge = _FakeBridge(connected=True)
        client._homie = _FakeHomie(ready=True, snapshot=sentinel)

        snapshot = await client.get_snapshot()
        assert snapshot is sentinel

    async def test_raised_exception_is_span_panel_error(self) -> None:
        client = _make_client()
        client._bridge = None
        client._homie = None

        with pytest.raises(SpanPanelError):
            await client.get_snapshot()
