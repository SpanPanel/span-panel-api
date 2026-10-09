"""A battery fed by a circuit takes its link status from that circuit.

Where a circuit's `connection/feeds-device-id` names the battery, the
circuit's `feeds-device-status` is the panel's view of the link, exactly as it
is for an inverter or a charger: `SpanBatterySnapshot.connected` follows it and
`feed_circuit_id` names the circuit. The circuit stays a circuit. A battery
claimed by the upstream lugs keeps reading its link from them.
"""

from __future__ import annotations

import pytest

from conftest import flat_schema
from reference_payloads.schema_one import parent_child_tree, replay
from reference_payloads.synthetic_trees import PANEL_ID, SyntheticDevice, circuit_fed_battery, panel, tree, without_values
from span_panel_api import SpanPanelSnapshot
from span_panel_api_schema_0 import SchemaZeroAdapter

_REFERENCE_PANEL = "example-40t-001"


def _snapshot(*children: SyntheticDevice) -> SpanPanelSnapshot:
    return replay(tree(panel(), *children), PANEL_ID).build_snapshot()


@pytest.mark.parametrize(("status", "connected"), [("OK", True), ("LOST", False), ("DEGRADED", False)])
def test_the_feeding_circuits_status_is_the_link(status: str, connected: bool) -> None:
    snapshot = _snapshot(*circuit_fed_battery("c-1", "battery-a", status=status))

    assert snapshot.battery.connected is connected
    assert snapshot.battery.feed_circuit_id == "c-1"
    assert snapshot.circuits["c-1"].device_type == "circuit"


def test_an_unpublished_status_leaves_the_link_unknown() -> None:
    circuit, battery = circuit_fed_battery("c-1", "battery-a")
    snapshot = _snapshot(without_values(circuit, "connection/feeds-device-status"), battery)

    assert snapshot.battery.connected is None
    assert snapshot.battery.feed_circuit_id == "c-1"


def test_an_unpublished_feed_names_no_circuit() -> None:
    circuit, battery = circuit_fed_battery("c-1", "battery-a")
    snapshot = _snapshot(without_values(circuit, "connection/feeds-device-id", "connection/feeds-device-status"), battery)

    assert (snapshot.battery.connected, snapshot.battery.feed_circuit_id) == (None, None)


def test_a_battery_behind_the_upstream_lugs_keeps_their_link() -> None:
    battery = replay(parent_child_tree(), _REFERENCE_PANEL).build_snapshot().battery

    assert battery.connected is True
    assert battery.feed_circuit_id is None


def test_a_flat_panel_names_no_feeding_circuit() -> None:
    snapshot = SchemaZeroAdapter(serial_number="sim-40t-001", schema=flat_schema(40)).build_snapshot()

    assert snapshot.battery.feed_circuit_id is None
