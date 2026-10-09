"""A device the panel no longer declares is not part of the panel, whatever it left behind.

Firmware r202639 renames a panel's inverters once it publishes more than one: the
single inverter's `<panel>-se7600h-us` becomes `<panel>-se7600h-us-<n>`, one per
inverter. The upgrade does not clear the old device's retained topics, so the
broker keeps replaying a `$description` that still names the panel as its root
and parent, beside the new devices, until something removes it.

Membership comes from the parent's `$description.children` alone. The SDK
subscribes to a child only once its parent declares it (and drops one the parent
stops declaring), and `ControllerRoutes` holds a message no route has asked for
rather than delivering it. A stale device's own claim to a parent is never read.
These tests pin that through the adapter's real `handle_message` path, so an
`ebus-sdk` upgrade that changed how the tree is walked would fail here rather than
surface as a ghost inverter.
"""

from __future__ import annotations

import json

import pytest

from reference_payloads.schema_one import RetainedTopicTree, parent_child_tree
from span_panel_api.models import V2HomieSchema
from span_panel_api_schema_1 import SchemaOneAdapter

PANEL = "example-40t-001"
SOLAR_CIRCUIT = "573066aaddd7b75114c4563ce3af18c4"
SECOND_SOLAR_CIRCUIT = "5be1d2c3a4f5061728394a5b6c7d8e9f"

# Illustrative ids in the shape firmware r202639 uses.
STALE_PV = f"{PANEL}-se7600h-us"
FIRST_PV = f"{PANEL}-se7600h-us-1"
SECOND_PV = f"{PANEL}-se7600h-us-2"
STALE_MODEL = "SE7600H-STALE"


def _schema() -> V2HomieSchema:
    return V2HomieSchema(
        firmware_version="spanos2/r202639/01",
        types_schema_hash="sha256:test",
        types={},
        data_model_version="1.0",
    )


def _with_children(tree: dict[str, dict[str, str]], children: list[str]) -> None:
    description = json.loads(tree[PANEL]["$description"])
    description["children"] = children
    tree[PANEL] = {**tree[PANEL], "$description": json.dumps(description)}


def _upgraded_tree(stale_state: str | None) -> dict[str, dict[str, str]]:
    """Two declared inverters, each fed by its own circuit; and, unless `stale_state` is None,
    the old inverter's retained topics, which the panel no longer declares."""
    tree = {device_id: dict(topics) for device_id, topics in parent_child_tree().items()}
    pv = tree.pop("pv")
    declared = [child for child in json.loads(tree[PANEL]["$description"])["children"] if child != "pv"]
    _with_children(tree, [*declared, SECOND_SOLAR_CIRCUIT, FIRST_PV, SECOND_PV])
    tree[FIRST_PV] = dict(pv)
    tree[SECOND_PV] = {**pv, "info/model": "SE7600H-B"}
    tree[SOLAR_CIRCUIT]["connection/feeds-device-id"] = FIRST_PV
    tree[SECOND_SOLAR_CIRCUIT] = {
        **tree[SOLAR_CIRCUIT],
        "connection/feeds-device-id": SECOND_PV,
        "info/name": "Garage Solar",
        "info/spaces": "13,15",
    }
    if stale_state is not None:
        tree[STALE_PV] = {**pv, "$state": stale_state, "info/model": STALE_MODEL}
    return tree


def _replay(adapter: SchemaOneAdapter, tree: RetainedTopicTree, order: list[str]) -> None:
    """Deliver each device's retained topics as the broker would, in `order`."""
    for device_id in order:
        topics = tree[device_id]
        prefix = f"ebus/5/{device_id}"
        adapter.handle_message(f"{prefix}/$description", topics["$description"])
        adapter.handle_message(f"{prefix}/$state", topics["$state"])
        for topic, value in topics.items():
            if not topic.startswith("$"):
                adapter.handle_message(f"{prefix}/{topic}", value)


def _without_stale() -> SchemaOneAdapter:
    tree = _upgraded_tree(None)
    adapter = SchemaOneAdapter(PANEL, _schema())
    _replay(adapter, tree, [PANEL, *[device_id for device_id in tree if device_id != PANEL]])
    return adapter


def _assert_stale_pv_is_absent(adapter: SchemaOneAdapter) -> None:
    """Nothing the adapter reports differs from a broker that never held the stale device."""
    reference = _without_stale()
    snapshot = adapter.build_snapshot()

    assert adapter.is_ready()
    assert snapshot == reference.build_snapshot()
    assert adapter.build_field_metadata() == reference.build_field_metadata()
    # Spelled out as well, so a failure names what leaked.
    assert {key: inverter.device_id for key, inverter in snapshot.pv_inverters.items()} == {
        SOLAR_CIRCUIT: FIRST_PV,
        SECOND_SOLAR_CIRCUIT: SECOND_PV,
    }
    assert snapshot.pv.model is None
    assert all(device.device_id != STALE_PV for device in snapshot.adopted_devices)


@pytest.mark.parametrize("stale_state", ["ready", "init", "disconnected", "lost"])
@pytest.mark.parametrize("stale_first", [True, False], ids=["stale-replayed-first", "stale-replayed-last"])
def test_a_pv_device_the_panel_no_longer_declares_never_reaches_the_snapshot(stale_state: str, stale_first: bool) -> None:
    """Whatever its retained `$state` -- `ready` included -- and whenever the broker replays it."""
    tree = _upgraded_tree(stale_state)
    others = [device_id for device_id in tree if device_id not in (PANEL, STALE_PV)]
    adapter = SchemaOneAdapter(PANEL, _schema())

    _replay(adapter, tree, [STALE_PV, PANEL, *others] if stale_first else [PANEL, *others, STALE_PV])

    _assert_stale_pv_is_absent(adapter)


def _before_the_upgrade() -> dict[str, dict[str, str]]:
    """The panel before r202639: one inverter, declared, under the id the upgrade retires."""
    tree = {device_id: dict(topics) for device_id, topics in parent_child_tree().items()}
    tree[STALE_PV] = {**tree.pop("pv"), "info/model": STALE_MODEL}
    declared = json.loads(tree[PANEL]["$description"])["children"]
    _with_children(tree, [STALE_PV if child == "pv" else child for child in declared])
    tree[SOLAR_CIRCUIT]["connection/feeds-device-id"] = STALE_PV
    return tree


def test_a_declared_pv_device_does_reach_the_snapshot() -> None:
    """The control: the same device, declared, is read -- so the tests above can see a leak."""
    tree = _before_the_upgrade()
    adapter = SchemaOneAdapter(PANEL, _schema())

    _replay(adapter, tree, [PANEL, *[device_id for device_id in tree if device_id != PANEL]])

    snapshot = adapter.build_snapshot()
    assert {key: inverter.device_id for key, inverter in snapshot.pv_inverters.items()} == {SOLAR_CIRCUIT: STALE_PV}
    assert snapshot.pv.model == STALE_MODEL


@pytest.mark.parametrize("republished", ["through-init", "while-ready"])
def test_a_pv_device_the_panel_stops_declaring_mid_session_leaves_the_snapshot(republished: str) -> None:
    """An adapter running across the upgrade drops the inverter the new `$description` no longer names.

    The panel either passes through `init` before announcing its new tree, or
    republishes its `$description` while staying `ready`; both must drop it.
    """
    before = _before_the_upgrade()
    adapter = SchemaOneAdapter(PANEL, _schema())
    _replay(adapter, before, [PANEL, *[device_id for device_id in before if device_id != PANEL]])
    after = _upgraded_tree("ready")

    if republished == "through-init":
        adapter.handle_message(f"ebus/5/{PANEL}/$state", "init")
    adapter.handle_message(f"ebus/5/{PANEL}/$description", after[PANEL]["$description"])
    if republished == "through-init":
        adapter.handle_message(f"ebus/5/{PANEL}/$state", "ready")
    # Then everything else retained, the stale inverter included.
    _replay(adapter, after, [device_id for device_id in after if device_id != PANEL])

    _assert_stale_pv_is_absent(adapter)
