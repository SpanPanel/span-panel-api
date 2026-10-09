"""Panel and circuit readings, over every reference capture.

Each reading equals the wire value its capture declares.
"""

from __future__ import annotations

import pytest

from reference_payloads.captures import CAPTURES, capture_adapter, capture_snapshot, capture_tree
from span_panel_api import PROTECTION_FUNCTIONS

_LUGS_TYPE = "energy.ebus.device.lugs"
_NEW_ROWS = (
    "panel.busbar_current_a",
    "panel.frequency_hz",
    "circuit.nominal_voltage_v",
    "circuit.protection_functions",
)


@pytest.mark.parametrize("stem", CAPTURES)
def test_the_panel_readings_equal_their_wire_values(stem: str) -> None:
    tree, snapshot = capture_tree(stem), capture_snapshot(stem)
    upstream = [
        device_id
        for device_id in tree.devices
        if tree.device_type(device_id) == _LUGS_TYPE and tree.value(device_id, "info/direction") == "UPSTREAM"
    ]
    rating = tree.number(upstream[0], "connection/overcurrent-protection") if upstream else None

    assert snapshot.busbar_current_a == tree.number(tree.root_id, "meter/busbar-current")
    assert snapshot.frequency_hz == tree.number(tree.root_id, "meter/frequency")
    assert snapshot.upstream_protection_rating_a == (None if rating is None else int(rating))


@pytest.mark.parametrize("stem", CAPTURES)
def test_each_circuit_reading_equals_its_wire_value(stem: str) -> None:
    tree, circuits = capture_tree(stem), capture_snapshot(stem).circuits
    for circuit_id in tree.circuits():
        published = tree.value(circuit_id, "breaker/protection-functions")
        functions = (
            None
            if published is None
            else tuple(part.strip() for part in published.split(",") if part.strip() in PROTECTION_FUNCTIONS)
        )
        assert circuits[circuit_id].nominal_voltage_v == tree.number(circuit_id, "info/nominal-voltage")
        assert circuits[circuit_id].protection_functions == functions


def test_the_captures_publish_the_new_readings() -> None:
    snapshots = [capture_snapshot(stem) for stem in CAPTURES]

    assert any(snapshot.busbar_current_a is not None for snapshot in snapshots)
    assert any(snapshot.frequency_hz is not None for snapshot in snapshots)
    assert any(snapshot.upstream_protection_rating_a is not None for snapshot in snapshots)
    circuits = [circuit for snapshot in snapshots for circuit in snapshot.circuits.values()]
    assert any(circuit.protection_functions for circuit in circuits)
    assert any(circuit.nominal_voltage_v is not None for circuit in circuits)


@pytest.mark.parametrize("stem", CAPTURES)
def test_the_new_readings_carry_resolved_metadata_where_declared(stem: str) -> None:
    metadata = capture_adapter(stem).build_field_metadata()

    assert not [path for path in _NEW_ROWS if path in metadata and not metadata[path].resolved]
    assert not [
        path
        for path in metadata
        if path.endswith(("/busbar-current", "/frequency", "/nominal-voltage", "/protection-functions"))
    ]
