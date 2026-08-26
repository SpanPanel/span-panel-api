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
    from .exceptions import SpanPanelError
    from .models import ControlTarget, FieldMetadata, SpanPanelSnapshot, V2HomieSchema
    from .mqtt.control import ControlInterceptor, PublishOutcome


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

    def register_fatal_error_callback(self, callback: Callable[[SpanPanelError], None]) -> Callable[[], None]:
        """Subscribe to the transport stopping for good.

        Declared here rather than only on the MQTT client because the consumer
        depends on it and this module's rule is that the consumer codes against
        protocols, never against transport-specific classes.

        Distinct from `register_connection_callback` because the two say
        different things and the difference is the whole point. "Disconnected" is
        what an ordinary outage looks like and a consumer is right to wait
        through it; this fires only for a failure no amount of waiting fixes,
        and the consumer is expected to surface it to a person.
        """


@runtime_checkable
class CircuitControlProtocol(Protocol):
    """Control protocol for relay and priority changes.

    Every setter across the four control protocols returns a `PublishOutcome`
    rather than `None`. **Additive for callers, breaking for implementers**: an
    existing call site that ignores the return value is unaffected, but a class
    type-checked against one of these protocols with `-> None` stops conforming.
    Test fakes and simulators are exactly that.

    The change exists because `None` could not distinguish a breaker that opened
    from a command that was never handed to the broker, and the transport had
    three separate paths that returned `None` having published nothing.
    """

    async def set_circuit_relay(self, circuit_id: str, state: str) -> PublishOutcome: ...

    async def set_circuit_priority(self, circuit_id: str, priority: str) -> PublishOutcome: ...


@runtime_checkable
class PanelControlProtocol(Protocol):
    """Control protocol for panel-level settable properties."""

    async def set_dominant_power_source(self, value: str) -> PublishOutcome: ...


@runtime_checkable
class EvseControlProtocol(Protocol):
    """Control protocol for settable properties on a commissioned EV charger.

    Separate from `PanelControlProtocol` because the subject is different: an
    EVSE is its own device under v1.0, several may be commissioned at once, and
    every call here names which one. A consumer asks `isinstance` before offering
    the control, exactly as it does for circuit and panel control.
    """

    async def set_evse_charge_limit(self, node_id: str, amps: int) -> PublishOutcome: ...


@runtime_checkable
class AdoptedControlProtocol(Protocol):
    """Control protocol for settable properties on a device nothing here models.

    Separate from the three above because its subject is different in kind. Those
    name a control this library understands -- a relay, a shed priority, a charge
    ceiling -- and translate or bound the value on the way out. This one names a
    property by its wire address and passes the caller's value through, because
    the declaration is all anybody here knows about it.

    The write is authorised by the snapshot rather than by the arguments: the
    transport resolves the property against the current `adopted_devices` and
    refuses anything it does not find carrying a set topic. A device this library
    models produces no `AdoptedDevice` and so cannot be addressed here, which is
    what stops this becoming a generic write around the curated setters.
    """

    async def set_adopted_property(self, device_id: str, node_id: str, property_id: str, value: str) -> PublishOutcome: ...


@runtime_checkable
class ControlInterceptionProtocol(Protocol):
    """Transport that can be given one veto-and-observe point for every command.

    A protocol of its own rather than a member added to the four control
    protocols or to `StreamingCapableProtocol`. Adding it to the control
    protocols would break every implementer of them a second time in one
    release, and streaming has nothing to do with control -- a transport could
    reasonably offer one and not the other.

    Declared here at all because the consumer's authorisation gate is built on
    it, and this module's rule is that the consumer codes against protocols,
    never against transport-specific classes.
    """

    def set_control_interceptor(self, interceptor: ControlInterceptor | None) -> None:
        """Install the interceptor, or `None` to remove it. One at a time."""


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

    def set_circuit_relay_target(self, circuit_id: str) -> ControlTarget:
        """Where a relay command goes, and the property that reports it.

        Renamed from `set_circuit_relay_topic`, which returned a bare string.
        The rename is deliberate rather than a return-type change under the old
        name: an adapter built against the old contract would still carry the
        old name, pass discovery on presence, and fail deep inside a setter with
        an `AttributeError` on a `str`. Under a new name it is rejected at
        discovery, where the remedy -- upgrade both packages together -- can
        still be named. That is also why `ADAPTER_CONTRACT_VERSION` does not
        move: the change is additive plus a removal, not a redefinition.
        """

    def set_circuit_priority_target(self, circuit_id: str) -> ControlTarget:
        """Where a shed-priority command goes, and the property that reports it."""

    def set_dominant_power_source_target(self) -> ControlTarget | None:
        """Where a dominant-power-source command goes, or None if the panel has no such control."""

    def set_evse_charge_limit_target(self, node_id: str) -> ControlTarget | None:
        """The target that writes one charger's charge-current limit, or None.

        `node_id` is the key the snapshot's `evse` map uses, so a caller needs
        nothing but the snapshot it already has. Returning None means this
        schema, this panel, or this charger offers no such control — no
        property, or one the charger does not declare `$settable` — and the
        transport must refuse the command rather than publish to it.

        Named at runtime from the charger's own `$description` under v1.0,
        because the node carrying the limit is one of two spellings and the
        `$description` is the specification's authority on which. See
        `span_panel_api_schema_1.charge_limit`.
        """

    def evse_charge_limit_payload(self, node_id: str, amps: int) -> str | None:
        """Translate a requested amperage into what this charger accepts.

        Returning None means the value may not be published — above the
        commissioned ceiling, or otherwise outside what the declaration allows.
        The transport refuses rather than clamping: a silently clamped write
        reports a limit the charger is not enforcing.
        """

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

    def register_property_callback(self, callback: Callable[[str, str, str, str | None], None]) -> Callable[[], None]:
        """Subscribe to per-property updates; returns an unregister callable.

        **The callback receives `(device_id, node_id, property_id, value)`** --
        the same triple `ControlTarget` carries, spelled the same way, because
        write-then-verify matches one against the other. `value` is `None` only
        where the adapter can report a property with no value; a consumer that
        needs the previous value keeps it itself.

        Under the flat schema every property belongs to the one device, so
        `device_id` is the panel serial rather than being omitted. Filling it in
        is what lets a consumer treat both schemas' streams as one shape.
        """
