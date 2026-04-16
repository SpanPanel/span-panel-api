"""Async MQTT client — NullLock + AsyncMQTTClient.

Replicates the pattern from Home Assistant core's MQTT integration:
paho-mqtt's 7 internal threading locks are replaced with no-op NullLock
instances so the client can run entirely on a single asyncio event loop
thread without contention overhead.
"""

from __future__ import annotations

from types import TracebackType

from paho.mqtt.client import Client as MQTTClient
from paho.mqtt.enums import CallbackAPIVersion

_PAHO_LOCK_ATTRS = (
    "_in_callback_mutex",
    "_callback_mutex",
    "_msgtime_mutex",
    "_out_message_mutex",
    "_in_message_mutex",
    "_reconnect_delay_mutex",
    "_mid_generate_mutex",
)


def _verify_paho_lock_attrs() -> None:
    """Verify paho-mqtt's lock layout matches the list we monkey-patch.

    Runs once at import. Raises ``RuntimeError`` if any expected attribute
    is missing (paho renamed/removed one) or if paho grew a new lock we
    don't yet patch. Running ``python -O`` does not bypass this check.
    """
    probe = MQTTClient(callback_api_version=CallbackAPIVersion.VERSION2)
    expected = set(_PAHO_LOCK_ATTRS)
    found = {name for name in vars(probe) if name.endswith("_mutex")}
    missing = expected - found
    extra = found - expected
    if missing or extra:
        raise RuntimeError(
            "paho-mqtt lock attributes changed — NullLock monkey-patch is out of date. "
            f"missing={sorted(missing)}, extra={sorted(extra)}. "
            "Update _PAHO_LOCK_ATTRS in span_panel_api.mqtt.async_client."
        )


_verify_paho_lock_attrs()


class NullLock:
    """No-op lock for single-threaded event loop execution.

    Replaces threading.Lock in paho-mqtt's internals. All methods are
    trivial no-ops since the client runs on a single event loop thread.
    """

    def __enter__(self) -> NullLock:
        """Enter the lock (no-op)."""
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        """Exit the lock (no-op)."""

    def acquire(self, _blocking: bool = False, _timeout: int = -1) -> None:
        """Acquire the lock (no-op)."""

    def release(self) -> None:
        """Release the lock (no-op)."""


class AsyncMQTTClient(MQTTClient):
    """paho Client subclass with NullLock replacing all internal locks.

    Wrapper around paho.mqtt.client.Client to remove the threading
    locks that are not needed when running in an async event loop.
    Call ``setup()`` immediately after construction.
    """

    def setup(self) -> None:
        """Replace paho's 7 internal threading locks with NullLock.

        Must be called before any I/O. The client will then be safe to
        drive exclusively from the event loop thread.
        """
        for attr in _PAHO_LOCK_ATTRS:
            setattr(self, attr, NullLock())
