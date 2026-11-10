"""Circuits that share a meter or a relay name each other.

`meter/` and `switch/shared-with-device-ids` list the other devices one meter
measures or one relay switches. They are resolved to the circuit ids of the
snapshot: the circuit itself and ids naming no circuit are dropped, and the
rest are ordered by device id with instance numbers compared as numbers.
Undeclared or not yet published reads `None`; declared with no known peer reads
`()`. `shared_meter_groups` groups the circuits one meter measures; nothing is
summed.
"""

from __future__ import annotations

from conftest import flat_schema
from reference_payloads.schema_one import parent_child_tree, replay
from reference_payloads.synthetic_trees import (
    PANEL_ID,
    SyntheticDevice,
    hosted_circuit,
    panel,
    shared_pair,
    tree,
    without_values,
)
from span_panel_api import SpanPanelSnapshot, shared_meter_groups
from span_panel_api.models import device_id_order
from span_panel_api_schema_0 import SchemaZeroAdapter

_REFERENCE_PANEL = "example-40t-001"


def _snapshot(*circuits: SyntheticDevice) -> SpanPanelSnapshot:
    return replay(tree(panel(), *circuits), PANEL_ID).build_snapshot()


def test_a_shared_pair_names_each_other_for_meter_and_relay() -> None:
    circuits = _snapshot(*shared_pair("c-1", "c-2")).circuits

    assert (circuits["c-1"].meter_shared_with, circuits["c-1"].relay_shared_with) == (("c-2",), ("c-2",))
    assert (circuits["c-2"].meter_shared_with, circuits["c-2"].relay_shared_with) == (("c-1",), ("c-1",))


def test_the_circuit_itself_and_unknown_ids_are_dropped() -> None:
    circuits = _snapshot(
        hosted_circuit("c-1", (1,), meter_shared_with=("c-1", "ghost", "c-2"), relay_shared_with=("ghost",)),
        hosted_circuit("c-2", (3,)),
    ).circuits

    assert circuits["c-1"].meter_shared_with == ("c-2",)
    assert circuits["c-1"].relay_shared_with == ()


def test_instance_numbers_are_ordered_as_numbers() -> None:
    circuits = _snapshot(
        hosted_circuit("c-1", (1,), meter_shared_with=("c-10", "c-9")),
        hosted_circuit("c-9", (3,)),
        hosted_circuit("c-10", (5,)),
    ).circuits

    assert circuits["c-1"].meter_shared_with == ("c-9", "c-10")
    assert sorted(["c-10", "c-9", "c-1", "meter-a"], key=device_id_order) == ["c-1", "c-9", "c-10", "meter-a"]


def test_undeclared_shared_with_reads_none() -> None:
    circuit = _snapshot(hosted_circuit("c-1", (1,))).circuits["c-1"]

    assert (circuit.meter_shared_with, circuit.relay_shared_with) == (None, None)


def test_declared_shared_with_not_yet_published_reads_none() -> None:
    first, second = shared_pair("c-1", "c-2")
    unpublished = without_values(first, "meter/shared-with-device-ids", "switch/shared-with-device-ids")
    snapshot = _snapshot(unpublished, second)

    assert (snapshot.circuits["c-1"].meter_shared_with, snapshot.circuits["c-1"].relay_shared_with) == (None, None)


def test_declared_with_no_known_peer_reads_empty_and_has_no_group() -> None:
    snapshot = _snapshot(hosted_circuit("c-1", (1,), meter_shared_with=("ghost",)))

    assert snapshot.circuits["c-1"].meter_shared_with == ()
    assert shared_meter_groups(snapshot.circuits) == {}


def test_groups_are_keyed_by_their_first_member() -> None:
    snapshot = _snapshot(*shared_pair("c-10", "c-9", space=1), *shared_pair("c-2", "c-3", space=3))

    assert shared_meter_groups(snapshot.circuits) == {"c-2": ("c-2", "c-3"), "c-9": ("c-9", "c-10")}


def test_a_group_holds_every_circuit_one_meter_measures() -> None:
    """Named by any member, a circuit is in the group, even where its own list has not arrived."""
    snapshot = _snapshot(
        hosted_circuit("c-1", (1,), meter_shared_with=("c-2",)),
        hosted_circuit("c-2", (1,), meter_shared_with=("c-3",)),
        hosted_circuit("c-3", (1,)),
    )

    assert shared_meter_groups(snapshot.circuits) == {"c-1": ("c-1", "c-2", "c-3")}


def test_the_reference_tree_shares_nothing() -> None:
    snapshot = replay(parent_child_tree(), _REFERENCE_PANEL).build_snapshot()

    assert all(
        (circuit.meter_shared_with, circuit.relay_shared_with) == (None, None) for circuit in snapshot.circuits.values()
    )
    assert shared_meter_groups(snapshot.circuits) == {}


def test_a_flat_panel_shares_nothing() -> None:
    snapshot = SchemaZeroAdapter(serial_number="sim-40t-001", schema=flat_schema(40)).build_snapshot()

    assert all(circuit.meter_shared_with is None for circuit in snapshot.circuits.values())
    assert shared_meter_groups(snapshot.circuits) == {}
