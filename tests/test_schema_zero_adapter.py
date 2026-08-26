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


CIRCUIT = "ac3dccda46a94b98878a227df6fed588"


def test_circuit_setter_topics_address_the_panel_device(adapter: SchemaZeroAdapter) -> None:
    relay = adapter.set_circuit_relay_target(CIRCUIT)
    priority = adapter.set_circuit_priority_target(CIRCUIT)

    assert relay is not None and relay.topic == f"ebus/5/{SERIAL}/{CIRCUIT}/relay/set"
    assert priority is not None and priority.topic == f"ebus/5/{SERIAL}/{CIRCUIT}/shed-priority/set"


def _publish(adapter: SchemaZeroAdapter, node: str, prop: str, value: str) -> None:
    adapter.handle_message(f"ebus/5/{SERIAL}/{node}/{prop}", value)


def test_an_always_on_circuit_yields_no_relay_target(adapter: SchemaZeroAdapter) -> None:
    """The same refusal the v1.0 side makes, from the value flat publishes for it.

    `always-on` is already read into `is_user_controllable`, so the panel had
    told this adapter the relay was locked and only the snapshot listened; the
    topic builder was pure string formatting from a node id.
    """
    _publish(adapter, CIRCUIT, "always-on", "true")

    assert adapter.set_circuit_relay_target(CIRCUIT) is None
    # Only the relay. Always-on is not never-backup, on either schema.
    assert adapter.set_circuit_priority_target(CIRCUIT) is not None


def test_a_never_backup_circuit_yields_no_priority_target(adapter: SchemaZeroAdapter) -> None:
    _publish(adapter, CIRCUIT, "never-backup", "true")

    assert adapter.set_circuit_priority_target(CIRCUIT) is None
    assert adapter.set_circuit_relay_target(CIRCUIT) is not None


def test_a_published_false_reads_as_permission(adapter: SchemaZeroAdapter) -> None:
    """A panel that publishes the flags as `false` must not be refused.

    Absence already reads as permission; this is the other half, and it is the
    one a producer actually exercises -- a clone of a real panel writes
    `always-on: "false"` out rather than omitting it.
    """
    _publish(adapter, CIRCUIT, "always-on", "false")
    _publish(adapter, CIRCUIT, "never-backup", "false")

    assert adapter.set_circuit_relay_target(CIRCUIT) is not None
    assert adapter.set_circuit_priority_target(CIRCUIT) is not None


def test_dominant_power_source_topic_is_none_before_the_core_node_is_known(
    adapter: SchemaZeroAdapter,
) -> None:
    """The core node id is discovered from $description, so it is unavailable
    until a description has been routed through handle_message."""
    assert adapter.set_dominant_power_source_target() is None


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


def test_property_callbacks_speak_the_protocol_not_the_accumulator() -> None:
    """The shim between the accumulator's tuple and the protocol's is load-bearing
    and arity-compatible, which is the dangerous combination.

    The accumulator fires `(node_id, property_id, new_value, old_value)`; the
    protocol declares `(device_id, node_id, property_id, value)`. Four strings
    either way, so a bare delegation type-checks, runs, and silently feeds a
    consumer the node id as a device id and the *previous* value as the current
    one. That is the bug this shim fixes, and nothing else pins it: every
    publish-outcome test drives a MagicMock adapter, so write-then-verify on a
    flat panel -- both `CONFIRMED` and the no-op pre-check, which match on
    `(device_id, node_id, property_id)` and compare the reported value -- rests
    entirely on these four arguments arriving in this order.

    Two writes rather than one, because a single write cannot distinguish the
    new value from the old: the first arrives with no previous value at all.
    """
    adapter = SchemaZeroAdapter(serial_number=SERIAL, schema=flat_schema(40))
    seen: list[tuple[str, str, str, str | None]] = []
    unregister = adapter.register_property_callback(lambda d, n, p, v: seen.append((d, n, p, v)))

    adapter.handle_message(f"ebus/5/{SERIAL}/core/power", "100")
    adapter.handle_message(f"ebus/5/{SERIAL}/core/power", "200")

    assert seen == [
        (SERIAL, "core", "power", "100"),
        (SERIAL, "core", "power", "200"),
    ]
    # Stated separately from the tuple comparison above, because these are the
    # two ways a regression here stays silent rather than failing loudly.
    assert seen[1][0] == SERIAL, "the device is the panel serial, never the node id"
    assert seen[1][3] == "200", "the fourth argument is the new value, never the previous one"

    unregister()
    adapter.handle_message(f"ebus/5/{SERIAL}/core/power", "300")
    assert len(seen) == 2
