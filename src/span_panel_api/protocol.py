"""Protocol interfaces for SPAN Panel API transports.

Defines structural subtyping contracts (PEP 544) that the MQTT transport
implements. The integration codes against these protocols — never against
transport-specific classes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import Flag, auto
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .models import FieldMetadata, HomieSchemaTypes, SpanPanelSnapshot


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

    def register_connection_callback(self, callback: Callable[[bool], None]) -> Callable[[], None]: ...


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


@runtime_checkable
class SchemaAdapter(Protocol):
    """Parser for a single data-model-major schema.

    Frozen within a major version of this package. Most methods here are
    called by SpanMqttClient, which is the only bootstrap code that knows
    the wire format; ``find_node_by_type`` and ``register_property_callback``
    are not called by the bootstrap at all — they exist for external
    consumers of the active adapter.
    """

    schema_major: str
    SUPPORTS_DATA_MODEL_VERSIONS: tuple[str, str]

    def topics_to_subscribe(self) -> list[str]: ...

    def handle_message(self, topic: str, payload: str) -> None: ...

    def is_ready(self) -> bool: ...

    def build_snapshot(self) -> SpanPanelSnapshot: ...

    def build_field_metadata(self, schema_types: HomieSchemaTypes) -> dict[str, FieldMetadata]: ...

    def circuit_nodes_missing_names(self) -> list[str]: ...

    def find_node_by_type(self, type_str: str) -> str | None: ...

    def set_circuit_relay_topic(self, circuit_id: str) -> str: ...

    def set_circuit_priority_topic(self, circuit_id: str) -> str: ...

    def set_dominant_power_source_topic(self) -> str | None: ...

    def register_property_callback(self, callback: Callable[[str, str, str, str | None], None]) -> Callable[[], None]: ...
