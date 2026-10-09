"""A circuit-typed device that declares no breaker space, over every reference capture.

It is a meter outside the panel, classified by declaration, never by value. Its
power stays import-positive (positive = flowing into the panel), consumed is
imported energy and produced is exported energy, it has no relay and no shed
priority to command, and it is never waited on for a name.
"""

from __future__ import annotations

import pytest

from reference_payloads.captures import CAPTURES, capture_adapter, capture_snapshot, capture_tree, space_less_meters
from reference_payloads.schema_one import parent_child_tree, replay

_REFERENCE_PANEL = "example-40t-001"


def _reads(wire: float | None) -> object:
    """What the snapshot must hold for a wire value: the number, or None where it is unvalued."""
    return None if wire is None else pytest.approx(wire)


@pytest.mark.parametrize("stem", CAPTURES)
def test_a_space_less_meter_reads_its_wire_values_import_positive(stem: str) -> None:
    tree, snapshot = capture_tree(stem), capture_snapshot(stem)
    for meter_id in space_less_meters(tree):
        meter = snapshot.circuits[meter_id]
        assert meter.measures_outside_panel is True
        assert meter.instant_power_w == _reads(tree.number(meter_id, "meter/active-power"))
        assert meter.consumed_energy_wh == _reads(tree.number(meter_id, "meter/imported-energy"))
        assert meter.produced_energy_wh == _reads(tree.number(meter_id, "meter/exported-energy"))
        assert meter.tabs == []


@pytest.mark.parametrize("stem", CAPTURES)
def test_a_space_less_meter_has_no_controls_and_is_never_waited_on(stem: str) -> None:
    tree, snapshot, adapter = capture_tree(stem), capture_snapshot(stem), capture_adapter(stem)
    for meter_id in space_less_meters(tree):
        meter = snapshot.circuits[meter_id]
        assert (meter.is_user_controllable, meter.is_sheddable, meter.always_on) == (False, False, False)
        assert adapter.set_circuit_relay_target(meter_id) is None
        assert adapter.set_circuit_priority_target(meter_id) is None
        assert meter.relay_state_target is None and meter.priority_target is None
        assert meter.is_240v is None
        assert meter_id not in adapter.circuit_nodes_missing_names()


@pytest.mark.parametrize("stem", CAPTURES)
def test_every_hosted_circuit_stays_inside_the_panel(stem: str) -> None:
    tree, snapshot = capture_tree(stem), capture_snapshot(stem)
    meters = set(space_less_meters(tree))

    assert all(circuit.measures_outside_panel is (circuit_id in meters) for circuit_id, circuit in snapshot.circuits.items())


def test_the_captures_hold_space_less_meters() -> None:
    assert any(space_less_meters(capture_tree(stem)) for stem in CAPTURES)


def test_the_reference_tree_has_no_space_less_meter() -> None:
    snapshot = replay(parent_child_tree(), _REFERENCE_PANEL).build_snapshot()

    assert not any(circuit.measures_outside_panel for circuit in snapshot.circuits.values())
