"""Panel and circuit readings, and the site properties that never become readings.

The panel's busbar current and line frequency, the upstream lugs' overcurrent
protection rating, and each circuit's nominal voltage and protection functions
are read where the panel declares them and are `None` where it does not, or
has not published them. Their field metadata comes from the declarations, and
an undeclared one is absent rather than a gap.

The site's name, address, coordinates and utility meter serial never become an
extension or adopted reading, on whatever node a device declares them.
"""

from __future__ import annotations

from typing import get_args

import pytest

from conftest import flat_schema
from reference_payloads.schema_one import devices_from_tree, replay
from reference_payloads.synthetic_trees import (
    PANEL_ID,
    PropertyDeclaration,
    SyntheticDevice,
    hosted_circuit,
    lugs,
    panel,
    tree,
    without_values,
)
from span_panel_api import PROTECTION_FUNCTIONS, ProtectionFunction, SpanPanelSnapshot
from span_panel_api.models import FieldMetadata
from span_panel_api_schema_0 import SchemaZeroAdapter
from span_panel_api_schema_1.const import SITE_PROPERTIES
from span_panel_api_schema_1.field_metadata import build_field_metadata

_STRING: PropertyDeclaration = {"name": "Site", "datatype": "string"}


def _snapshot(*children: SyntheticDevice, root: SyntheticDevice | None = None) -> SpanPanelSnapshot:
    return replay(tree(root or panel(), *children), PANEL_ID).build_snapshot()


def test_the_protection_function_set_is_the_literal() -> None:
    assert set(PROTECTION_FUNCTIONS) == set(get_args(ProtectionFunction.__value__))


def test_the_panel_readings_are_read_where_declared() -> None:
    snapshot = _snapshot(
        lugs("UPSTREAM", overcurrent_protection_a=150), root=panel(busbar_current_a=41.5, frequency_hz=59.98)
    )

    assert (snapshot.busbar_current_a, snapshot.frequency_hz) == (41.5, 59.98)
    assert snapshot.upstream_protection_rating_a == 150


def test_the_panel_readings_are_none_where_undeclared() -> None:
    snapshot = _snapshot(lugs("UPSTREAM"), lugs("DOWNSTREAM", overcurrent_protection_a=60))

    assert (snapshot.busbar_current_a, snapshot.frequency_hz, snapshot.upstream_protection_rating_a) == (
        None,
        None,
        None,
    )


def test_the_panel_readings_are_none_until_published() -> None:
    root = without_values(panel(busbar_current_a=41.5, frequency_hz=60.0), "meter/busbar-current", "meter/frequency")
    upstream = without_values(lugs("UPSTREAM", overcurrent_protection_a=150), "connection/overcurrent-protection")
    snapshot = _snapshot(upstream, root=root)

    assert (snapshot.busbar_current_a, snapshot.frequency_hz, snapshot.upstream_protection_rating_a) == (
        None,
        None,
        None,
    )


def test_a_circuit_reads_its_nominal_voltage_and_protection_functions() -> None:
    circuit = _snapshot(
        hosted_circuit("c-1", (1,), nominal_voltage_v=240.0, protection_functions=("ARC_FAULT", "OVERCURRENT"))
    ).circuits["c-1"]

    assert circuit.nominal_voltage_v == 240.0
    assert circuit.protection_functions == ("ARC_FAULT", "OVERCURRENT")


def test_a_protection_function_outside_the_set_is_dropped() -> None:
    circuit = _snapshot(hosted_circuit("c-1", (1,), protection_functions=("GROUND_FAULT", "NOT_A_FUNCTION"))).circuits["c-1"]

    assert circuit.protection_functions == ("GROUND_FAULT",)


def test_circuit_readings_are_none_where_undeclared_or_unpublished() -> None:
    declared = hosted_circuit("c-2", (3,), nominal_voltage_v=120.0, protection_functions=("OVERCURRENT",))
    circuits = _snapshot(
        hosted_circuit("c-1", (1,)),
        without_values(declared, "info/nominal-voltage", "breaker/protection-functions"),
    ).circuits

    for circuit_id in ("c-1", "c-2"):
        assert (circuits[circuit_id].nominal_voltage_v, circuits[circuit_id].protection_functions) == (None, None)


def test_the_readings_carry_metadata_from_their_declarations() -> None:
    metadata = build_field_metadata(
        devices_from_tree(
            tree(
                panel(busbar_current_a=41.5, frequency_hz=60.0),
                hosted_circuit("c-1", (1,), nominal_voltage_v=120.0, protection_functions=("OVERCURRENT",)),
            )
        )
    )

    assert metadata["panel.busbar_current_a"] == FieldMetadata(unit="A", datatype="float")
    assert metadata["panel.frequency_hz"] == FieldMetadata(unit="Hz", datatype="float")
    assert metadata["circuit.nominal_voltage_v"] == FieldMetadata(unit="V", datatype="float")
    assert metadata["circuit.protection_functions"] == FieldMetadata(unit=None, datatype="enum")


def test_an_undeclared_reading_has_no_metadata_rather_than_a_gap() -> None:
    metadata = build_field_metadata(devices_from_tree(tree(panel(), hosted_circuit("c-1", (1,)))))

    for path in (
        "panel.busbar_current_a",
        "panel.frequency_hz",
        "circuit.nominal_voltage_v",
        "circuit.protection_functions",
    ):
        assert path not in metadata


@pytest.mark.parametrize("property_id", sorted(SITE_PROPERTIES))
def test_a_site_property_on_any_node_never_becomes_a_reading(property_id: str) -> None:
    """The identity node keeps them out where the panel declares them; this holds anywhere else."""
    base = panel()
    root = SyntheticDevice(
        device_id=base.device_id,
        device_type=base.device_type,
        name=base.name,
        nodes={**base.nodes, "site": {property_id: _STRING}},
        values={**base.values, f"site/{property_id}": "example"},
    )
    vendor = SyntheticDevice(
        device_id="vendor-a",
        device_type="energy.ebus.device.example",
        name="Vendor",
        nodes={"site": {property_id: _STRING, "label": _STRING}},
        values={f"site/{property_id}": "example", "site/label": "example"},
    )
    snapshot = _snapshot(vendor, root=root)

    assert not [row for row in snapshot.extension_properties if row.property_id == property_id]
    adopted = {row.property_id for device in snapshot.adopted_devices for row in device.properties}
    assert adopted == {"label"}


def test_a_flat_panel_reports_none_of_them() -> None:
    snapshot = SchemaZeroAdapter(serial_number="sim-40t-001", schema=flat_schema(40)).build_snapshot()

    assert (snapshot.busbar_current_a, snapshot.frequency_hz, snapshot.upstream_protection_rating_a) == (
        None,
        None,
        None,
    )
    assert all(
        (circuit.nominal_voltage_v, circuit.protection_functions) == (None, None) for circuit in snapshot.circuits.values()
    )
