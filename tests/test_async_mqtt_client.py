"""Tests for NullLock and AsyncMQTTClient."""

from __future__ import annotations

from paho.mqtt.enums import CallbackAPIVersion

from span_panel_api.mqtt.async_client import (
    AsyncMQTTClient,
    NullLock,
    _PAHO_LOCK_ATTRS,
)


class TestNullLock:
    """Verify NullLock is a complete no-op."""

    def test_context_manager(self) -> None:
        lock = NullLock()
        with lock as ctx:
            assert ctx is lock

    def test_repeated_calls_cached(self) -> None:
        lock = NullLock()
        result1 = lock.__enter__()
        result2 = lock.__enter__()
        assert result1 is result2


class TestAsyncMQTTClient:
    """Verify AsyncMQTTClient.setup() replaces all paho locks."""

    def test_setup_replaces_all_locks(self) -> None:
        client = AsyncMQTTClient(
            callback_api_version=CallbackAPIVersion.VERSION2,
        )
        # Before setup: locks should be real threading primitives
        for attr in _PAHO_LOCK_ATTRS:
            assert not isinstance(getattr(client, attr), NullLock)

        client.setup()

        # After setup: all locks should be NullLock
        for attr in _PAHO_LOCK_ATTRS:
            assert isinstance(getattr(client, attr), NullLock)

    def test_setup_creates_separate_instances(self) -> None:
        client = AsyncMQTTClient(
            callback_api_version=CallbackAPIVersion.VERSION2,
        )
        client.setup()

        locks = [getattr(client, attr) for attr in _PAHO_LOCK_ATTRS]
        # Each attribute should have its own NullLock instance
        assert len(set(id(lock) for lock in locks)) == len(_PAHO_LOCK_ATTRS)
