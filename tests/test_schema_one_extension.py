"""What the adapter emits as vendor extensions on devices it *does* model.

`build_extension_properties` is the value-carrying twin of `build_discovery`:
the same declared-but-unaddressed question, asked of the same tree, answered for
a consumer that will render it rather than for a maintainer reading an
attachment. The two must agree exactly, so the first test here is the join —
every extension row has a discovery row and vice versa.

The rest are structural, and they are structural on purpose. "Adopted values
never reach diagnostics" and "an extension property is read-only" are claims the
design makes about *shapes*, so they are asserted about shapes: a type that is
not a `FieldMetadata` cannot enter the metadata map that diagnostics is built
from, and a type with no set-topic member cannot grow a write path by someone
forgetting a rule.
"""

from __future__ import annotations

from collections.abc import Mapping
import json

from ebus_sdk.homie import DiscoveredDevice
import pytest

from reference_payloads.schema_one import device_from_topics, parent_child_tree
from span_panel_api.models import (
    ADOPTION_IDENTITY_NODE,
    ADOPTION_TOPOLOGY_NODE,
    ExtensionProperty,
    ExtensionSubject,
    FieldMetadata,
    SpanPanelSnapshot,
    discovery_path,
)
from span_panel_api_schema_1.extension import build_extension_properties
from span_panel_api_schema_1.field_metadata import addressed_rows, build_discovery
from span_panel_api_schema_1.snapshot import build_snapshot

PANEL_DEVICE_ID = "example-40t-001"
"""The enclosure in the reference capture. Every other device is its child."""

Tree = dict[str, dict[str, str]]


def _tree() -> Tree:
    return {device_id: dict(topics) for device_id, topics in parent_child_tree().items()}


def _snapshot(tree: Tree) -> SpanPanelSnapshot:
    panel = device_from_topics(PANEL_DEVICE_ID, tree[PANEL_DEVICE_ID])
    children = [device_from_topics(device_id, topics) for device_id, topics in tree.items() if device_id != PANEL_DEVICE_ID]
    return build_snapshot(panel, children)


def _devices(tree: Tree) -> list[DiscoveredDevice]:
    return [device_from_topics(device_id, topics) for device_id, topics in tree.items()]


def _short_type(device_type: str) -> str:
    return device_type.rsplit(".", 1)[-1]


def _declared_type(tree: Tree, device_id: str) -> str:
    description: Mapping[str, object] = json.loads(tree[device_id]["$description"])
    return str(description.get("type") or "")


# --- the join with discovery ------------------------------------------------


def test_every_extension_row_is_also_a_discovery_row() -> None:
    """The two surfaces describe the same properties, joined by the wire path.

    A property in one and not the other is the defect this test exists for: an
    entity a consumer renders while the diagnostics report it ignored, or a
    property reported ignored while an entity shows its value. Both read as a
    bug in whichever surface disagreed.
    """
    tree = _tree()
    snapshot = _snapshot(tree)
    discovered = set(build_discovery(_devices(tree)))

    for row in snapshot.extension_properties:
        # The discovery path is keyed by the *device type*, so rebuild it from
        # the subject's device rather than from the subject kind, which is a
        # snapshot concept.
        assert any(
            path.endswith(f"/{row.path}") for path in discovered
        ), f"extension row {row.path} on {row.subject.kind} has no discovery row"


def test_no_extension_row_is_addressed() -> None:
    """An addressed property has a snapshot field; surfacing it twice is the bug."""
    tree = _tree()
    addressed = addressed_rows(_devices(tree))
    for row in _snapshot(tree).extension_properties:
        assert not any(
            node == row.node_id and prop == row.property_id for _type, node, prop in addressed
        ), f"{row.path} is addressed and must not be emitted as an extension"


# --- structure: the diagnostics and read-only guarantees --------------------


def test_extension_property_is_not_field_metadata() -> None:
    """The diagnostics guarantee, asserted as a shape rather than as a rule.

    `partition()` walks `build_field_metadata()`; a type that cannot enter that
    map has no path into a payload that leaves the machine.
    """
    row = ExtensionProperty(
        subject=ExtensionSubject(kind="battery"),
        node_id="battery-2",
        property_id="cell-temperature",
        datatype="float",
    )
    assert not isinstance(row, FieldMetadata)


def test_extension_property_has_no_write_surface() -> None:
    """No set topic, and no member one could be put in.

    A literal `hasattr` check, because the point is to fail the change that adds
    one rather than to describe today's fields.
    """
    row = ExtensionProperty(
        subject=ExtensionSubject(kind="evse", instance_key="acme-001"),
        node_id="acme",
        property_id="charge-limit",
        datatype="float",
        settable=True,
    )
    assert not hasattr(row, "set_topic")
    assert row.settable is True, "settable is carried for triage, and still carries no write path"


def test_identity_and_topology_nodes_are_never_extensions() -> None:
    """`info` and `connection` resolve to the device card and the tree.

    Excluded by node, as `adoption._readings` excludes them, because the
    catalogs carry no marker for "this string is a device reference" and a name
    list goes stale silently.
    """
    for row in _snapshot(_tree()).extension_properties:
        assert row.node_id not in (ADOPTION_IDENTITY_NODE, ADOPTION_TOPOLOGY_NODE)


# --- emission against a synthetic vendor extension --------------------------


VENDOR_NODE = "battery-2"


def _with_vendor_extension(tree: Tree, device_id: str) -> Tree:
    """Add an Acme pack node to one device's description, with one retained value."""
    mutated = {other: dict(topics) for other, topics in tree.items()}
    description = json.loads(mutated[device_id]["$description"])
    description["nodes"][VENDOR_NODE] = {
        "name": VENDOR_NODE,
        "type": "energy.ebus.capability.vendor.acme.pack",
        "properties": {
            "cell-temperature": {"name": "Cell temperature", "datatype": "float", "unit": "°C"},
            "pack-enabled": {"name": "Pack enabled", "datatype": "boolean", "settable": True},
        },
    }
    mutated[device_id]["$description"] = json.dumps(description)
    mutated[device_id][f"{VENDOR_NODE}/cell-temperature"] = "31.4"
    return mutated


def _bess_device_id(tree: Tree) -> str:
    for device_id in tree:
        if _declared_type(tree, device_id).endswith(".bess"):
            return device_id
    pytest.skip("reference tree carries no BESS")


def test_a_vendor_node_on_a_modelled_device_becomes_extension_rows() -> None:
    """The whole point: a property hung off the BESS reaches the snapshot."""
    tree = _tree()
    device_id = _bess_device_id(tree)
    snapshot = _snapshot(_with_vendor_extension(tree, device_id))

    rows = {row.path: row for row in snapshot.extension_properties}
    assert f"{VENDOR_NODE}/cell-temperature" in rows
    assert f"{VENDOR_NODE}/pack-enabled" in rows

    temperature = rows[f"{VENDOR_NODE}/cell-temperature"]
    assert temperature.subject.kind == "battery"
    assert temperature.subject.instance_key is None
    assert temperature.datatype == "float"
    assert temperature.unit == "°C"
    assert temperature.value == "31.4"
    # Declared and never valued is distinguishable from valued: `None` rather
    # than an invented default, so a consumer can tell "nothing has arrived".
    assert rows[f"{VENDOR_NODE}/pack-enabled"].value is None
    assert rows[f"{VENDOR_NODE}/pack-enabled"].settable is True


def test_a_wholly_vendor_node_has_no_curated_siblings() -> None:
    """The one exported bit of the node-to-field map, on a node with none."""
    tree = _tree()
    snapshot = _snapshot(_with_vendor_extension(tree, _bess_device_id(tree)))
    rows = [row for row in snapshot.extension_properties if row.node_id == VENDOR_NODE]
    assert rows
    assert all(row.node_has_curated_siblings is False for row in rows)


def test_an_extension_to_a_curated_node_reports_curated_siblings() -> None:
    """A vendor extending `meter` is extending something this adapter reads."""
    tree = _tree()
    device_id = _bess_device_id(tree)
    mutated = {other: dict(topics) for other, topics in tree.items()}
    description = json.loads(mutated[device_id]["$description"])
    meter = description["nodes"].get("meter")
    if meter is None:
        pytest.skip("reference BESS declares no meter node")
    meter["properties"]["acme-cell-balance"] = {"name": "Cell balance", "datatype": "float", "unit": "%"}
    mutated[device_id]["$description"] = json.dumps(description)

    rows = [row for row in _snapshot(mutated).extension_properties if row.property_id == "acme-cell-balance"]
    assert len(rows) == 1
    assert rows[0].node_has_curated_siblings is True
    # `%` is deliberately unrankable: the consumer maps no device class for it.
    assert rows[0].unit == "%"


def test_an_undeclared_device_is_skipped_rather_than_emitted() -> None:
    """A device mid-discovery declares no type; that is a state, not a finding."""
    tree = _tree()
    device_id = _bess_device_id(tree)
    mutated = {other: dict(topics) for other, topics in tree.items()}
    description = json.loads(mutated[device_id]["$description"])
    description.pop("type", None)
    mutated[device_id]["$description"] = json.dumps(description)

    subjects = [(device_from_topics(device_id, mutated[device_id]), ExtensionSubject(kind="battery"))]
    assert build_extension_properties(subjects, addressed_rows(_devices(mutated))) == ()


def test_the_reference_tree_alone_emits_nothing_addressed() -> None:
    """A sanity floor: every row the untouched tree emits is genuinely unread."""
    snapshot = _snapshot(_tree())
    addressed = addressed_rows(_devices(_tree()))
    for row in snapshot.extension_properties:
        assert not any(node == row.node_id and prop == row.property_id for _t, node, prop in addressed)


def test_discovery_path_joins_the_two_surfaces() -> None:
    """The documented join key actually joins."""
    tree = _tree()
    device_id = _bess_device_id(tree)
    mutated = _with_vendor_extension(tree, device_id)
    discovered = set(build_discovery(_devices(mutated)))
    expected = discovery_path(_short_type(_declared_type(mutated, device_id)), VENDOR_NODE, "cell-temperature")
    assert expected in discovered

    rows = {row.path for row in _snapshot(mutated).extension_properties}
    assert f"{VENDOR_NODE}/cell-temperature" in rows
    assert expected.endswith(f"/{VENDOR_NODE}/cell-temperature")


def test_the_two_lugs_devices_are_two_subjects() -> None:
    """Identical firmware on both lugs is what made one subject a collision.

    A vendor extension on the upstream lugs is the expected case of the same
    extension on the downstream lugs, so folding both into `panel` gave two wire
    addresses one identity -- a consumer keying on
    `(kind, instance_key, node/property)` would mint one id for two readings and
    show whichever sorted first.
    """
    tree = _tree()
    lugs = [
        device_id
        for device_id in tree
        if ".lugs" in _declared_type(tree, device_id) or _declared_type(tree, device_id).endswith("lugs")
    ]
    if len(lugs) < 2:
        pytest.skip("reference tree carries fewer than two lugs devices")

    mutated = {other: dict(topics) for other, topics in tree.items()}
    for device_id, value in zip(lugs, ("1.5", "99.9"), strict=False):
        description = json.loads(mutated[device_id]["$description"])
        description["nodes"]["acme"] = {
            "name": "acme",
            "type": "energy.ebus.capability.vendor.acme.balance",
            "properties": {"phase-balance": {"name": "Phase balance", "datatype": "float", "unit": "%"}},
        }
        mutated[device_id]["$description"] = json.dumps(description)
        mutated[device_id]["acme/phase-balance"] = value

    rows = [row for row in _snapshot(mutated).extension_properties if row.path == "acme/phase-balance"]
    assert len(rows) == 2
    assert all(row.subject.kind == "lugs" for row in rows)
    assert {row.subject.instance_key for row in rows} == {"upstream", "downstream"}
    # Distinct identities carrying distinct readings, which is the point.
    assert {row.value for row in rows} == {"1.5", "99.9"}


def test_every_subject_identity_is_unique_per_property() -> None:
    """No two rows may share `(kind, instance_key, path)` -- that tuple is the identity."""
    identities = [(row.subject.kind, row.subject.instance_key, row.path) for row in _snapshot(_tree()).extension_properties]
    assert len(identities) == len(set(identities))
