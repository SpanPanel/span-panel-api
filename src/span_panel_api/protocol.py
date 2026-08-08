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
    from .models import FieldMetadata, SpanPanelSnapshot, V2HomieSchema


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


ADAPTER_CONTRACT_VERSION = 1
"""The bootstrap-to-adapter contract this package speaks.

Bumped only when a change leaves existing adapters unusable — a different
``__init__`` signature, or a method whose meaning changes under an unchanged
name. Purely additive changes do not bump it: ``_derive_required_members``
already requires every member the protocol declares, so an adapter missing a
newly added method is rejected on that basis alone.

This exists because member presence is not the whole contract. A Protocol
cannot express signatures at runtime, so an adapter carrying every required
name and the wrong ``__init__`` arity passes discovery and fails much later,
inside the transport, as a bare ``TypeError`` about an argument count. That is
exactly what a stale adapter looks like, and it is the least actionable moment
to find out. A declared integer is checkable at discovery, where the remedy —
upgrade this package — can still be named.

**Adapters must declare this as a literal, never by importing this constant.**
An adapter that echoes whatever the installed bootstrap defines agrees with
every bootstrap by construction, which is precisely the disagreement being
looked for. The value has to be baked into the adapter's wheel at build time.
"""


@runtime_checkable
class SchemaAdapter(Protocol):
    """Parser for a single data-model-major schema.

    Frozen within a major version of this package. Most methods here are
    called by SpanMqttClient, which is the only bootstrap code that knows
    the wire format; ``find_node_by_type`` and ``register_property_callback``
    are not called by the bootstrap at all — they exist for external
    consumers of the active adapter.
    """

    ADAPTER_CONTRACT: int
    schema_major: str
    SUPPORTS_DATA_MODEL_VERSIONS: tuple[str, str]

    def __init__(self, serial_number: str, schema: V2HomieSchema) -> None:
        """Construct a parser for one panel session.

        Declared because construction is part of the contract: the transport
        resolves an adapter *class* from the entry-point registry and calls it.

        Takes the whole schema rather than anything derived from it. The
        previous signature passed ``panel_size``, which the transport extracted
        on the adapter's behalf from a block only the flat schema has — so the
        bootstrap had to understand a wire format it is supposed to know nothing
        about, and any adapter whose schema is shaped differently could not say
        so. Each adapter now reads what its own format defines.
        """

    def topics_to_subscribe(self) -> list[str]: ...

    def handle_message(self, topic: str, payload: str) -> None: ...

    def is_ready(self) -> bool: ...

    def build_snapshot(self) -> SpanPanelSnapshot: ...

    def build_field_metadata(self) -> dict[str, FieldMetadata]: ...

    def circuit_nodes_missing_names(self) -> list[str]: ...

    def find_node_by_type(self, type_str: str) -> str | None: ...

    def set_circuit_relay_topic(self, circuit_id: str) -> str: ...

    def set_circuit_priority_topic(self, circuit_id: str) -> str: ...

    def set_dominant_power_source_topic(self) -> str | None: ...

    def dominant_power_source_payload(self, value: str) -> str | None:
        """Translate a caller's value into what this schema's wire accepts.

        Callers speak the flat vocabulary (`GRID`, `BATTERY`, `PV`, `GENERATOR`,
        `NONE`, `UNKNOWN`) because that is the published contract. Under v1.0 the
        settable successor is `shed/asserted-islanding-state`, whose enum is
        `NONE`/`ON_GRID`/`OFF_GRID`, so the value has to be mapped rather than
        forwarded. Returning None means "no legal representation", and the
        transport should refuse the command rather than publish a value the
        panel will reject.
        """

    def register_property_callback(self, callback: Callable[[str, str, str, str | None], None]) -> Callable[[], None]: ...
