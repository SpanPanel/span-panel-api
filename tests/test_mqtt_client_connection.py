"""Tests for SpanMqttClient connection callbacks and get_snapshot() liveness guards."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from span_panel_api.exceptions import SpanPanelError, SpanPanelStaleDataError
from span_panel_api.mqtt.client import SpanMqttClient
from span_panel_api.mqtt.models import MqttClientConfig


def _make_client() -> SpanMqttClient:
    """Build a SpanMqttClient without I/O for unit testing."""
    config = MqttClientConfig(
        broker_host="127.0.0.1",
        username="test",
        password="test",
    )
    return SpanMqttClient("127.0.0.1", "test-serial", config)


class _FakeBridge:
    """Minimal bridge stub for get_snapshot() and fan-out tests."""

    def __init__(self, connected: bool = True) -> None:
        self._connected = connected
        self.subscribed_topics: list[tuple[str, int]] = []

    def is_connected(self) -> bool:
        return self._connected

    def subscribe(self, topic: str, qos: int = 0) -> None:
        self.subscribed_topics.append((topic, qos))


class _FakeHomie:
    """Minimal Homie stub for get_snapshot() tests."""

    def __init__(self, ready: bool = True, snapshot: Any = None) -> None:
        self._ready = ready
        self._snapshot = snapshot if snapshot is not None else SimpleNamespace()

    def is_ready(self) -> bool:
        return self._ready

    def build_snapshot(self) -> Any:
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
