"""A circuit-typed device that declares no breaker space measures outside the panel.

It is classified by declaration, never by value: a device whose `$description`
declares no `info/spaces` is a meter outside the panel however its values read,
and one that declares spaces stays hosted while its values have not arrived.

Its power stays import-positive (positive = flowing into the panel), consumed is
imported energy and produced is exported energy, so consumed minus produced is
net import. It has no relay, no breaker and no shed priority, so it offers no
control and no voltage, and it is never waited on for a name it does not
declare. A hosted circuit keeps the panel's load frame exactly as before.
"""

from __future__ import annotations

import math

import pytest

from reference_payloads.schema_one import parent_child_tree, replay
from reference_payloads.synthetic_trees import (
    METER_READING,
    PANEL_ID,
    MeterReading,
    SyntheticDevice,
    discovered,
    hosted_circuit,
    panel,
    space_less_meter,
    tree,
    without_values,
)
from span_panel_api.models import SpanCircuitSnapshot
from span_panel_api_schema_1 import SchemaOneAdapter
from span_panel_api_schema_1.circuits import relay_is_settable

_REFERENCE_PANEL = "example-40t-001"

_IMPORTING = MeterReading(active_power_w=13.5, current_a=0.1, imported_energy_wh=1200.5, exported_energy_wh=75.25)
"""Power flowing into the panel through the meter."""


def _adapter(*children: SyntheticDevice) -> SchemaOneAdapter:
    return replay(tree(panel(), *children), PANEL_ID)


def _circuit(device: SyntheticDevice) -> SpanCircuitSnapshot:
    return _adapter(device).build_snapshot().circuits[device.device_id]


@pytest.mark.parametrize("reading", [_IMPORTING, METER_READING], ids=["importing", "exporting"])
def test_a_space_less_meter_reads_its_wire_values_import_positive(reading: MeterReading) -> None:
    meter = _circuit(space_less_meter("meter-a", reading=reading))

    assert meter.measures_outside_panel is True
    assert meter.instant_power_w == pytest.approx(reading.active_power_w)
    assert meter.consumed_energy_wh == pytest.approx(reading.imported_energy_wh)
    assert meter.produced_energy_wh == pytest.approx(reading.exported_energy_wh)
    assert meter.current_a == pytest.approx(reading.current_a)
    assert meter.tabs == []


def test_a_space_less_meter_reading_zero_reads_positive_zero() -> None:
    """A published `-0.0` is a zero reading, and is reported as `0.0`."""
    meter = _circuit(space_less_meter("meter-a", reading=MeterReading(-0.0, 0.0, 10.0, 5.0)))

    assert meter.instant_power_w == 0.0
    assert meter.instant_power_w is not None and math.copysign(1.0, meter.instant_power_w) == 1.0


def test_a_space_less_meter_has_no_controls_and_is_never_waited_on() -> None:
    adapter = _adapter(space_less_meter("meter-a"))

    meter = adapter.build_snapshot().circuits["meter-a"]

    assert (meter.is_user_controllable, meter.always_on, meter.is_sheddable) == (False, False, False)
    assert adapter.set_circuit_relay_target("meter-a") is None
    assert adapter.set_circuit_priority_target("meter-a") is None
    assert meter.relay_state_target is None and meter.priority_target is None
    assert meter.is_240v is None
    assert meter.breaker_rating_a is None
    assert "meter-a" not in adapter.circuit_nodes_missing_names()


def test_an_unvalued_space_less_meter_is_still_outside_the_panel() -> None:
    """Declared and not yet valued: outside the panel, every reading unknown."""
    adapter = _adapter(without_values(space_less_meter("meter-a")))

    meter = adapter.build_snapshot().circuits["meter-a"]

    assert meter.measures_outside_panel is True
    assert (meter.instant_power_w, meter.consumed_energy_wh, meter.produced_energy_wh, meter.current_a) == (
        None,
        None,
        None,
        None,
    )
    assert meter.is_user_controllable is False
    assert adapter.circuit_nodes_missing_names() == []


def test_a_hosted_circuit_with_no_value_yet_counts_as_hosted() -> None:
    """It declares its spaces before it values them, and the declaration decides."""
    adapter = _adapter(without_values(hosted_circuit("c-1", (1,))))

    circuit = adapter.build_snapshot().circuits["c-1"]

    assert circuit.measures_outside_panel is False
    assert circuit.tabs == []
    assert circuit.instant_power_w is None
    assert circuit.is_user_controllable is True
    assert "c-1" in adapter.circuit_nodes_missing_names()


def test_a_hosted_circuit_keeps_the_panel_load_frame() -> None:
    """Negated power, exported energy as consumed: unchanged by the meter rule."""
    circuit = _circuit(hosted_circuit("c-1", (1,)))

    assert circuit.measures_outside_panel is False
    assert circuit.instant_power_w == pytest.approx(-METER_READING.active_power_w)
    assert circuit.consumed_energy_wh == pytest.approx(METER_READING.exported_energy_wh)
    assert circuit.produced_energy_wh == pytest.approx(METER_READING.imported_energy_wh)
    assert circuit.tabs == [1]


@pytest.mark.parametrize(("spaces", "is_240v"), [((1,), False), ((1, 3), True)])
def test_a_hosted_circuit_reports_its_voltage_from_its_poles(spaces: tuple[int, ...], is_240v: bool) -> None:
    assert _circuit(hosted_circuit("c-1", spaces)).is_240v is is_240v


def test_a_breaker_whose_pole_count_has_not_arrived_reports_no_voltage() -> None:
    assert _circuit(without_values(hosted_circuit("c-1", (1, 3)), "breaker/poles")).is_240v is None


# ---------------------------------------------------------------------------
# Controllability follows the `switch` node, whatever the spaces
# ---------------------------------------------------------------------------


def test_a_hosted_circuit_without_a_switch_node_is_not_controllable() -> None:
    """No relay to command, and none locked on: neither controllable nor always on."""
    device = hosted_circuit("c-1", (1,), relay=False)
    adapter = _adapter(device)

    circuit = adapter.build_snapshot().circuits["c-1"]

    assert circuit.measures_outside_panel is False
    assert (circuit.is_user_controllable, circuit.always_on, circuit.is_sheddable) == (False, False, False)
    assert relay_is_settable(discovered(device)) is False
    assert adapter.set_circuit_relay_target("c-1") is None


def test_an_unvalued_relay_controllable_on_a_switch_node_is_controllable() -> None:
    """The property marks the exception, so a declared relay without it is controllable."""
    device = without_values(hosted_circuit("c-1", (1,)), "switch/relay-controllable")
    adapter = _adapter(device)

    circuit = adapter.build_snapshot().circuits["c-1"]

    assert (circuit.is_user_controllable, circuit.always_on, circuit.is_sheddable) == (True, False, True)
    assert relay_is_settable(discovered(device)) is True
    assert adapter.set_circuit_relay_target("c-1") is not None


# ---------------------------------------------------------------------------
# The reference panel hosts every circuit
# ---------------------------------------------------------------------------


def test_the_reference_panel_has_no_space_less_meter() -> None:
    """Every circuit of the reference tree declares its spaces, its relay and its breaker."""
    circuits = replay(parent_child_tree(), _REFERENCE_PANEL).build_snapshot().circuits

    assert circuits
    assert not any(circuit.measures_outside_panel for circuit in circuits.values())
