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
