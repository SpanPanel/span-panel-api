"""Flat-schema (data-model-version absent) adapter.

Composes the existing accumulator + consumer and owns the flat wire format:
a single Homie device whose node ids are circuit UUIDs and capability names.
Nothing outside this package constructs a flat-schema topic.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

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

    def set_circuit_relay_topic(self, circuit_id: str) -> str:
        return PROPERTY_SET_TOPIC_FMT.format(serial=self._serial_number, node=circuit_id, prop="relay")

    def set_circuit_priority_topic(self, circuit_id: str) -> str:
        return PROPERTY_SET_TOPIC_FMT.format(serial=self._serial_number, node=circuit_id, prop="shed-priority")

    def set_dominant_power_source_topic(self) -> str | None:
        core_node = self._consumer.find_node_by_type(TYPE_CORE)
        if core_node is None:
            return None
        return PROPERTY_SET_TOPIC_FMT.format(serial=self._serial_number, node=core_node, prop="dominant-power-source")

    def register_property_callback(self, callback: Callable[[str, str, str, str | None], None]) -> Callable[[], None]:
        return self._consumer.register_property_callback(callback)
