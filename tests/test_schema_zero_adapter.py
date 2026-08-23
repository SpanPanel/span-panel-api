"""SchemaZeroAdapter contract tests.

The adapter owns every piece of flat-schema knowledge that used to live in
SpanMqttClient: which topics to subscribe to, and how to address a settable
property. These tests pin the exact topic strings, because the flat wire format
is fixed by shipped firmware and must not drift.
"""

from __future__ import annotations

import pytest

from span_panel_api_schema_0 import SchemaZeroAdapter

from conftest import flat_schema
from span_panel_api.protocol import SchemaAdapter

SERIAL = "sim-40t-001"


@pytest.fixture
def adapter() -> SchemaZeroAdapter:
    return SchemaZeroAdapter(serial_number=SERIAL, schema=flat_schema(40))


def test_satisfies_the_protocol(adapter: SchemaZeroAdapter) -> None:
    assert isinstance(adapter, SchemaAdapter)


def test_declares_its_dispatch_key_and_range(adapter: SchemaZeroAdapter) -> None:
    assert adapter.schema_major == "schema_0"
    assert adapter.SUPPORTS_DATA_MODEL_VERSIONS == (">=0", "<1.0")


def test_subscribes_to_the_single_panel_wildcard(adapter: SchemaZeroAdapter) -> None:
    """Flat schema is one device, so one wildcard captures everything."""
    assert adapter.topics_to_subscribe() == [f"ebus/5/{SERIAL}/#"]


def test_circuit_setter_topics_address_the_panel_device(adapter: SchemaZeroAdapter) -> None:
    circuit = "ac3dccda46a94b98878a227df6fed588"
    assert adapter.set_circuit_relay_topic(circuit) == f"ebus/5/{SERIAL}/{circuit}/relay/set"
    assert adapter.set_circuit_priority_topic(circuit) == f"ebus/5/{SERIAL}/{circuit}/shed-priority/set"


def test_dominant_power_source_topic_is_none_before_the_core_node_is_known(
    adapter: SchemaZeroAdapter,
) -> None:
    """The core node id is discovered from $description, so it is unavailable
    until a description has been routed through handle_message."""
    assert adapter.set_dominant_power_source_topic() is None


def test_is_not_ready_before_any_message(adapter: SchemaZeroAdapter) -> None:
    assert adapter.is_ready() is False


def test_schema_zero_marks_missing_property_unresolved() -> None:
    """A type block that exists but drops a property is degradation, and must
    not read the same as a type that is absent entirely."""
    from span_panel_api_schema_0.field_metadata import build_field_metadata

    types = {"energy.ebus.device.circuit": {"name": {"datatype": "string"}}}
    metadata = build_field_metadata(types)

    assert metadata["circuit.instant_power_w"].resolved is False
    assert metadata["circuit.instant_power_w"].unit is None
    assert "battery.soe_percentage" not in metadata


def test_schema_zero_presence_follows_the_lugs_fallback() -> None:
    """The lugs fallback is schema_0's equivalent of schema_1's subtype rule.

    Rows are keyed on the typed lugs variants, but firmware that publishes only
    the generic `…device.lugs` block resolves through `_LUGS_FALLBACK`. Presence
    has to use the same path, or a property dropped from a generic-lugs block
    reads as absent hardware rather than as the drop it is.
    """
    from span_panel_api_schema_0.field_metadata import build_field_metadata

    types = {"energy.ebus.device.lugs": {"active-power": {"datatype": "float", "unit": "W"}}}
    metadata = build_field_metadata(types)

    assert metadata["panel.instant_grid_power_w"].resolved is True
    assert metadata["panel.upstream_l1_current_a"].resolved is False
    assert metadata["panel.downstream_l2_current_a"].resolved is False
    assert "circuit.instant_power_w" not in metadata
