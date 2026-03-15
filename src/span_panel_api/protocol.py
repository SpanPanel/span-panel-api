"""Protocol interfaces for SPAN Panel API transports.

Defines structural subtyping contracts (PEP 544) that both MQTT and
simulation transports implement. The integration codes against these
protocols — never against transport-specific classes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import Flag, auto
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .models import FieldMetadata, SpanPanelSnapshot


class PanelCapability(Flag):
    """Runtime feature advertisement."""

    NONE = 0
    PUSH_STREAMING = auto()
    EBUS_MQTT = auto()
    CIRCUIT_CONTROL = auto()
    BATTERY_SOE = auto()


@runtime_checkable
class SpanPanelClientProtocol(Protocol):
    """Core protocol every transport must satisfy."""

    @property
    def capabilities(self) -> PanelCapability: ...

    @property
    def serial_number(self) -> str: ...

    @property
    def field_metadata(self) -> dict[str, FieldMetadata] | None: ...

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def ping(self) -> bool: ...

    async def get_snapshot(self) -> SpanPanelSnapshot: ...


@runtime_checkable
class CircuitControlProtocol(Protocol):
    """Control protocol for relay and priority changes."""

    async def set_circuit_relay(self, circuit_id: str, state: str) -> None: ...

    async def set_circuit_priority(self, circuit_id: str, priority: str) -> None: ...


@runtime_checkable
class PanelControlProtocol(Protocol):
    """Control protocol for panel-level settable properties."""

    async def set_dominant_power_source(self, value: str) -> None: ...


@runtime_checkable
class StreamingCapableProtocol(Protocol):
    """Push-based transport that delivers updates via callbacks."""

    def register_snapshot_callback(
        self,
        callback: Callable[[SpanPanelSnapshot], Awaitable[None]],
    ) -> Callable[[], None]: ...

    async def start_streaming(self) -> None: ...

    async def stop_streaming(self) -> None: ...
