"""The EVSE lock as a control target, present only where the lock is settable.

A charger whose `switch/lock-state` declaration carries `settable: true` gets
`SpanEvseSnapshot.lock_control`, addressing `…/switch/lock-state/set` on the
charger's own device and observed on the same property. A lock declared without
it, or not declared at all, has no target.
"""

from __future__ import annotations

from conftest import flat_schema
from reference_payloads.schema_one import replay
from reference_payloads.synthetic_trees import PANEL_ID, SyntheticDevice, evse, panel, tree
from span_panel_api.models import ControlTarget, SpanEvseSnapshot
from span_panel_api_schema_0 import SchemaZeroAdapter


def _charger(device: SyntheticDevice) -> SpanEvseSnapshot:
    (charger,) = replay(tree(panel(), device), PANEL_ID).build_snapshot().evse.values()
    return charger


def test_a_settable_lock_has_a_control_target() -> None:
    assert _charger(evse("evse-a")).lock_control == ControlTarget(
        topic="ebus/5/evse-a/switch/lock-state/set",
        device_id="evse-a",
        node_id="switch",
        property_id="lock-state",
    )


def test_a_lock_that_is_not_settable_has_none() -> None:
    assert _charger(evse("evse-a", lock_settable=False)).lock_control is None


def test_a_charger_with_no_lock_has_none() -> None:
    base = evse("evse-a")
    unlocked = SyntheticDevice(
        device_id=base.device_id,
        device_type=base.device_type,
        name=base.name,
        nodes={node: props for node, props in base.nodes.items() if node != "switch"},
        values={path: value for path, value in base.values.items() if not path.startswith("switch/")},
    )

    assert _charger(unlocked).lock_control is None


def test_a_flat_panel_offers_no_lock_control() -> None:
    snapshot = SchemaZeroAdapter(serial_number="sim-40t-001", schema=flat_schema(40)).build_snapshot()

    assert all(charger.lock_control is None for charger in snapshot.evse.values())


def test_the_declared_lock_states_are_reported() -> None:
    assert _charger(evse("evse-a")).lock_state_options == ("UNLOCKED", "LOCKED")
    assert _charger(evse("evse-a", lock_settable=False)).lock_state_options == ("UNLOCKED", "LOCKED")
