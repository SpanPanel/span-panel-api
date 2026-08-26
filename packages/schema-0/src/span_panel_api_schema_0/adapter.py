"""Flat-schema (data-model-version absent) adapter.

Composes the existing accumulator + consumer and owns the flat wire format:
a single Homie device whose node ids are circuit UUIDs and capability names.
Nothing outside this package constructs a flat-schema topic.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from span_panel_api.models import ControlTarget
from span_panel_api_schema_0.accumulator import HomiePropertyAccumulator
from span_panel_api_schema_0.const import PROPERTY_SET_TOPIC_FMT, TYPE_CORE, WILDCARD_TOPIC_FMT
from span_panel_api_schema_0.consumer import HomieDeviceConsumer
from span_panel_api_schema_0.field_metadata import build_field_metadata

if TYPE_CHECKING:
    from span_panel_api.models import FieldMetadata, SpanPanelSnapshot, V2HomieSchema


class SchemaZeroAdapter:
    """Parser for the flat single-device schema (firmware r202603-r202627)."""

    # A literal, deliberately not imported from span_panel_api.protocol: a value
    # read from the installed bootstrap would agree with every bootstrap, which
    # is the disagreement the check exists to find. Bump when this adapter is
    # rebuilt against a new contract, never to match what happens to be installed.
    ADAPTER_CONTRACT: int = 1
    schema_major = "schema_0"
    SUPPORTS_DATA_MODEL_VERSIONS: tuple[str, str] = (">=0", "<1.0")

    def __init__(self, serial_number: str, schema: V2HomieSchema) -> None:
        self._serial_number = serial_number
        # `panel_size` is derived here rather than handed in, because deriving
        # it means reading the flat schema's `types` block for the circuit
        # `space` format — knowledge that belongs to this package. The
        # transport used to do this on every adapter's behalf, which only
        # worked while every adapter was this one.
        self._schema = schema
        self._accumulator = HomiePropertyAccumulator(serial_number)
        self._consumer = HomieDeviceConsumer(self._accumulator, schema.panel_size)

    def topics_to_subscribe(self) -> list[str]:
        return [WILDCARD_TOPIC_FMT.format(serial=self._serial_number)]

    def handle_message(self, topic: str, payload: str) -> None:
        self._consumer.handle_message(topic, payload)

    def is_ready(self) -> bool:
        return self._consumer.is_ready()

    def build_snapshot(self) -> SpanPanelSnapshot:
        return self._consumer.build_snapshot()

    def build_field_metadata(self) -> dict[str, FieldMetadata]:
        return build_field_metadata(self._schema.types)

    def circuit_nodes_missing_names(self) -> list[str]:
        return self._consumer.circuit_nodes_missing_names()

    def find_node_by_type(self, type_str: str) -> str | None:
        return self._consumer.find_node_by_type(type_str)

    def set_circuit_relay_target(self, circuit_id: str) -> ControlTarget:
        return self._target(circuit_id, "relay")

    def set_circuit_priority_target(self, circuit_id: str) -> ControlTarget:
        return self._target(circuit_id, "shed-priority")

    def set_dominant_power_source_target(self) -> ControlTarget | None:
        core_node = self._consumer.find_node_by_type(TYPE_CORE)
        if core_node is None:
            return None
        return self._target(core_node, "dominant-power-source")

    def _target(self, node: str, prop: str) -> ControlTarget:
        """One node/property pair as both a topic and an observation address.

        `device_id` is the panel serial for every flat control, because the flat
        schema is a single Homie device and its nodes hang directly off it. That
        is the same string `register_property_callback` reports under, which is
        what lets the transport match a write against the value that comes back.
        """
        return ControlTarget(
            topic=PROPERTY_SET_TOPIC_FMT.format(serial=self._serial_number, node=node, prop=prop),
            device_id=self._serial_number,
            node_id=node,
            property_id=prop,
        )

    def dominant_power_source_payload(self, value: str) -> str | None:
        """Flat speaks this vocabulary already, so the caller's value passes through.

        The method exists because `schema_1` has to translate — its successor
        property accepts `NONE`/`ON_GRID`/`OFF_GRID`, not a source class — and a
        caller should not have to know which schema it is talking to. Here the
        translation is the identity.

        Validated rather than passed blindly: an unrecognised value returns None
        and the transport refuses the command, which matches `schema_1`'s
        behaviour and is better than putting a string outside the enum on the
        wire.
        """
        allowed = {"GRID", "BATTERY", "PV", "GENERATOR", "NONE", "UNKNOWN"}
        candidate = value.strip().upper()
        return candidate if candidate in allowed else None

    def set_evse_charge_limit_target(self, node_id: str) -> ControlTarget | None:  # pylint: disable=unused-argument
        """None: flat firmware publishes no charge-current ceiling to write.

        The flat `energy.ebus.device.evse` type carries `advertised-current` —
        what the charger is offering the vehicle, read-only — and nothing that
        sets it. There is no property to aim a set topic at, so the transport
        refuses the command rather than publishing to a topic no panel of this
        generation subscribes to.

        `node_id` is accepted and unused for the same reason
        `set_dominant_power_source_target` takes no arguments and still returns
        None on a panel with no core node: the answer does not depend on which
        charger is asked.
        """
        return None

    def evse_charge_limit_payload(self, node_id: str, amps: int) -> str | None:  # pylint: disable=unused-argument
        """None, for the same reason: no property, so no representable value."""
        return None

    def register_property_callback(self, callback: Callable[[str, str, str, str | None], None]) -> Callable[[], None]:
        """Subscribe to per-property updates; returns an unregister callable.

        Adapts the accumulator's `(node_id, property_id, new_value, old_value)`
        to the protocol's `(device_id, node_id, property_id, value)`, the same
        way `SchemaOneAdapter` adapts the SDK's five arguments to the same four.

        This used to be a bare delegation, which handed the accumulator's tuple
        straight to a consumer expecting the protocol's. The two agree on arity
        and on nothing else: the fourth argument was the *previous* value where
        the protocol wants the current one, and the device was missing entirely.
        A consumer written against the protocol therefore read a flat panel's
        node id as a device id and its old value as its new one, and could not
        have noticed -- both are strings. The protocol has no place for the
        previous value; a consumer that needs one keeps it.
        """

        def _adapt(node_id: str, property_id: str, value: str, _old_value: str | None) -> None:
            callback(self._serial_number, node_id, property_id, value)

        return self._consumer.register_property_callback(_adapt)
