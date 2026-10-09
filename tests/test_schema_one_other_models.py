"""Tree shapes that panel models other than MAIN 32 publish.

Built from the capture, reshaped the way those models differ: circuit device ids
are `circuit-<n>`, a remote current transformer is a circuit-class device
carrying only a meter, no PV device exists and the circuit feeding the solar
says so through `connection/feeds-role`, and `info/model` may read `UNKNOWN`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from conftest import flat_schema
from reference_payloads.schema_one import RetainedTopicTree, device_from_topics, other_model_tree, parent_child_tree
from span_panel_api.models import FieldMetadata, SpanPanelSnapshot, SpanPVSnapshot, V2HomieSchema, is_discovery_path
from span_panel_api_schema_0 import SchemaZeroAdapter
from span_panel_api_schema_1 import SchemaOneAdapter
from span_panel_api_schema_1.const import TYPE_CIRCUIT
from span_panel_api_schema_1.field_metadata import build_field_metadata
from span_panel_api_schema_1.panel import PanelFields, panel_model_drift, panel_size_from_model
from span_panel_api_schema_1.snapshot import build_snapshot

PANEL = "example-40t-001"

_CIRCUIT_IDS = {
    "0ab966b95f92a6a51ec548485aa85f54": "circuit-1",
    "d3724e0d660ba506aa79c1cafe5d1181": "circuit-2",
    "62d0e03897b337b57101aae82f1e9ba2": "circuit-3",
    "fe8b85c15bc9610c1b8b4ebc6f82488d": "circuit-4",
    "acf35888f35522c721501d35e66503e6": "circuit-5",
    "573066aaddd7b75114c4563ce3af18c4": "circuit-6",
}
KITCHEN = "circuit-1"
SOLAR = "circuit-6"
GARAGE_DRIVE = "circuit-3"
"""Feeds `evse` in the capture."""
REMOTE_CT = "remote-ct-1"

_FEEDS_ROLE = {"datatype": "enum", "format": "LOADS,SUBPANEL,SOLAR,STORAGE,GENERATOR,MIXED,UNUSED"}


def _meter_only(meter_node: dict[str, object]) -> str:
    return json.dumps(
        {
            "homie": "5.0",
            "version": 1,
            "type": TYPE_CIRCUIT,
            "name": "Remote CT",
            "nodes": {"meter": meter_node},
            "root": PANEL,
            "parent": PANEL,
        }
    )


def _tree() -> dict[str, dict[str, str]]:
    """The capture reshaped as a panel model other than MAIN 32 publishes it."""
    source = parent_child_tree()
    tree: dict[str, dict[str, str]] = {}
    for device_id, topics in source.items():
        if device_id == "pv":
            continue
        tree[_CIRCUIT_IDS.get(device_id, device_id)] = dict(topics)

    solar = tree[SOLAR]
    del solar["connection/feeds-device-id"]
    description = json.loads(solar["$description"])
    description["nodes"]["connection"]["properties"]["feeds-role"] = _FEEDS_ROLE
    solar["$description"] = json.dumps(description)
    solar["connection/feeds-role"] = "SOLAR"

    kitchen_meter = json.loads(tree[KITCHEN]["$description"])["nodes"]["meter"]
    tree[REMOTE_CT] = {
        "$description": _meter_only(kitchen_meter),
        "$state": "ready",
        "meter/active-power": "250.0",
        "meter/current": "2.1",
        "meter/exported-energy": "0.0",
        "meter/imported-energy": "900.0",
    }

    panel = json.loads(tree[PANEL]["$description"])
    panel["children"] = [child for child in tree if child != PANEL]
    tree[PANEL]["$description"] = json.dumps(panel)
    return tree


def _snapshot(tree: RetainedTopicTree) -> SpanPanelSnapshot:
    panel = device_from_topics(PANEL, tree[PANEL])
    children = [device_from_topics(device_id, topics) for device_id, topics in tree.items() if device_id != PANEL]
    return build_snapshot(panel, children)


def _adapter(tree: RetainedTopicTree) -> SchemaOneAdapter:
    schema = V2HomieSchema(
        firmware_version="spanos2/r202639/01", types_schema_hash="sha256:test", types={}, data_model_version="1.0"
    )
    adapter = SchemaOneAdapter(PANEL, schema)
    for device_id in [PANEL, *[d for d in tree if d != PANEL]]:
        topics = tree[device_id]
        adapter.handle_message(f"ebus/5/{device_id}/$description", topics["$description"])
        adapter.handle_message(f"ebus/5/{device_id}/$state", topics.get("$state", "ready"))
        for topic, payload in topics.items():
            if not topic.startswith("$"):
                adapter.handle_message(f"ebus/5/{device_id}/{topic}", payload)
    return adapter


@pytest.fixture(name="snapshot")
def _built() -> SpanPanelSnapshot:
    return _snapshot(_tree())


# --- circuit-<n> ids --------------------------------------------------------


def test_circuits_are_keyed_by_their_device_id_as_published(snapshot: SpanPanelSnapshot) -> None:
    real = {cid for cid in snapshot.circuits if not cid.startswith("unmapped_tab_")}

    assert real == {*_CIRCUIT_IDS.values(), REMOTE_CT}
    assert snapshot.circuits[KITCHEN].circuit_id == KITCHEN
    assert snapshot.circuits[KITCHEN].name == "Kitchen Lights"


def test_the_controls_address_a_circuit_by_its_device_id() -> None:
    adapter = _adapter(_tree())

    target = adapter.set_circuit_relay_target(KITCHEN)

    assert target is not None
    assert target.device_id == KITCHEN
    assert KITCHEN in target.topic


# --- a remote current transformer -------------------------------------------


def test_a_meter_only_circuit_reports_its_reading(snapshot: SpanPanelSnapshot) -> None:
    ct = snapshot.circuits[REMOTE_CT]

    assert ct.instant_power_w == 250.0
    assert ct.current_a == 2.1
    assert ct.consumed_energy_wh == 900.0
    assert ct.tabs == []
    assert ct.name == ""
    assert ct.device_type == "circuit"


def test_a_circuit_without_declared_spaces_is_not_negated_or_swapped(snapshot: SpanPanelSnapshot) -> None:
    """A remote CT reads into the panel already: positive and imported-energy on import."""
    tree = _tree()
    tree[REMOTE_CT].update(
        {"meter/active-power": "-1200.0", "meter/imported-energy": "900.0", "meter/exported-energy": "300.0"}
    )
    ct = _snapshot(tree).circuits[REMOTE_CT]

    assert ct.instant_power_w == -1200.0
    assert ct.consumed_energy_wh == 900.0
    assert ct.produced_energy_wh == 300.0
    # An ordinary circuit in the same tree is still negated and swapped.
    kitchen = _snapshot(tree).circuits[KITCHEN]
    raw = float(tree[KITCHEN]["meter/active-power"])
    assert kitchen.instant_power_w == (0.0 if raw == 0.0 else -raw)
    assert kitchen.consumed_energy_wh == float(tree[KITCHEN]["meter/exported-energy"])


def test_a_named_circuit_without_declared_spaces_still_measures_outside_the_panel() -> None:
    """A later firmware may name the remote CT; without `info/spaces` it is still outside."""
    tree = _tree()
    description = json.loads(tree[REMOTE_CT]["$description"])
    description["nodes"]["info"] = {"properties": {"name": {"datatype": "string"}}}
    tree[REMOTE_CT]["$description"] = json.dumps(description)
    tree[REMOTE_CT]["info/name"] = "Service"
    ct = _snapshot(tree).circuits[REMOTE_CT]

    assert ct.measures_outside_panel is True
    assert ct.name == "Service"
    assert ct.instant_power_w == 250.0


def test_a_branch_that_declares_spaces_without_a_value_is_still_a_branch() -> None:
    """The declaration decides, not the value: an unpublished `info/spaces` is still loading."""
    tree = _tree()
    del tree[KITCHEN]["info/spaces"]
    kitchen = _snapshot(tree).circuits[KITCHEN]

    assert kitchen.measures_outside_panel is False
    raw = float(tree[KITCHEN]["meter/active-power"])
    assert kitchen.instant_power_w == (0.0 if raw == 0.0 else -raw)


def test_only_the_remote_ct_measures_outside_the_panel(snapshot: SpanPanelSnapshot) -> None:
    assert {cid for cid, c in snapshot.circuits.items() if c.measures_outside_panel} == {REMOTE_CT}


def test_no_main_32_circuit_measures_outside_the_panel() -> None:
    reference = _snapshot(parent_child_tree())
    assert not any(c.measures_outside_panel for c in reference.circuits.values())


def test_a_meter_only_circuit_has_no_relay_to_control_or_shed(snapshot: SpanPanelSnapshot) -> None:
    """No `switch` node is not a switch that omits `relay-controllable`."""
    ct = snapshot.circuits[REMOTE_CT]

    assert ct.is_user_controllable is False
    assert ct.is_sheddable is False
    assert ct.always_on is False
    assert ct.relay_state == "UNKNOWN"
    assert ct.relay_requester == "UNKNOWN"
    assert ct.relay_state_target is None
    assert ct.priority == "UNKNOWN"
    assert ct.priority_target is None
    assert ct.is_never_backup is True


def test_a_meter_only_circuit_refuses_both_controls() -> None:
    adapter = _adapter(_tree())

    assert adapter.has_circuit(REMOTE_CT)
    assert adapter.set_circuit_relay_target(REMOTE_CT) is None
    assert adapter.set_circuit_priority_target(REMOTE_CT) is None


def test_a_meter_only_circuit_is_not_waited_on_for_a_name() -> None:
    """It declares no `info/name`, so the wait would always time out."""
    assert REMOTE_CT not in _adapter(_tree()).circuit_nodes_missing_names()


# --- solar with no PV device ------------------------------------------------


def test_a_circuit_whose_feeds_role_is_solar_is_labeled_pv(snapshot: SpanPanelSnapshot) -> None:
    assert snapshot.circuits[SOLAR].device_type == "pv"
    assert snapshot.circuits[SOLAR].instant_power_w == -8500.0
    assert snapshot.circuits[KITCHEN].device_type == "circuit"


def test_no_pv_device_leaves_the_pv_snapshot_empty(snapshot: SpanPanelSnapshot) -> None:
    assert snapshot.pv == SpanPVSnapshot()
    assert snapshot.pv_inverters == {}


@pytest.mark.parametrize("role", ["LOADS", "SUBPANEL", "STORAGE", "GENERATOR", "MIXED", "UNUSED", "solar", ""])
def test_no_other_role_is_mapped(role: str) -> None:
    tree = _tree()
    tree[SOLAR]["connection/feeds-role"] = role

    assert _snapshot(tree).circuits[SOLAR].device_type == "circuit"


def test_feeds_device_id_is_authoritative_over_feeds_role() -> None:
    tree = _tree()
    del tree[GARAGE_DRIVE]["connection/feeds-device-id"]
    tree[SOLAR]["connection/feeds-device-id"] = "evse"

    assert _snapshot(tree).circuits[SOLAR].device_type == "evse"


def test_a_feeds_device_id_naming_no_der_is_not_overridden_by_the_role() -> None:
    tree = _tree()
    tree[SOLAR]["connection/feeds-device-id"] = "something-else"

    assert _snapshot(tree).circuits[SOLAR].device_type == "circuit"


# --- whether a panel publishes solar roles structurally ---------------------

_FLAT_WIRE = Path(__file__).parent / "fixtures" / "flat_wire.json"
_FLAT_SERIAL = "sim-40t-001"


def _other_model_snapshot() -> SpanPanelSnapshot:
    return _shipped()


def _parent_child_snapshot() -> SpanPanelSnapshot:
    return _snapshot(parent_child_tree())


def _flat_snapshot() -> SpanPanelSnapshot:
    """The flat capture replayed the way the retained store delivers it."""
    capture: dict[str, dict[str, str]] = json.loads(_FLAT_WIRE.read_text())
    adapter = SchemaZeroAdapter(serial_number=_FLAT_SERIAL, schema=flat_schema(40))
    for device in sorted(capture):
        for key in sorted(capture[device]):
            adapter.handle_message(f"ebus/5/{device}/{key}", capture[device][key])
    return adapter.build_snapshot()


def test_a_panel_that_declares_feeds_role_publishes_solar_roles() -> None:
    assert _other_model_snapshot().publishes_solar_roles is True
    assert _snapshot(_tree()).publishes_solar_roles is True


def test_main_32_and_the_flat_schema_do_not() -> None:
    assert _parent_child_snapshot().publishes_solar_roles is False
    assert _flat_snapshot().publishes_solar_roles is False


@pytest.mark.parametrize("role", [None, "LOADS"])
def test_the_declaration_decides_not_the_value(role: str | None) -> None:
    """A declared role with no value yet, or naming no solar, still marks the panel."""
    tree = _tree()
    if role is None:
        del tree[SOLAR]["connection/feeds-role"]
    else:
        tree[SOLAR]["connection/feeds-role"] = role

    assert _snapshot(tree).publishes_solar_roles is True


# --- info/model UNKNOWN -----------------------------------------------------


def _unknown_model_tree() -> dict[str, dict[str, str]]:
    tree = _tree()
    description = json.loads(tree[PANEL]["$description"])
    description["nodes"]["info"]["properties"]["model"]["format"] = "MAIN_16,MLO_24,MAIN_32,MAIN_40,MLO_48,UNKNOWN"
    tree[PANEL]["$description"] = json.dumps(description)
    tree[PANEL]["info/model"] = "UNKNOWN"
    return tree


def test_an_unknown_model_sizes_nothing_without_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        snapshot = _snapshot(_unknown_model_tree())

    assert snapshot.panel_size == 0
    assert not [cid for cid in snapshot.circuits if cid.startswith("unmapped_tab_")]
    assert not caplog.records
    assert panel_size_from_model("UNKNOWN") == 0


def test_an_unknown_model_reads_as_absent_so_a_consumer_falls_back() -> None:
    tree = _unknown_model_tree()
    panel = device_from_topics(PANEL, tree[PANEL])

    assert PanelFields(panel=panel, upstream_lugs=None, downstream_lugs=None, mid=None).model is None
    assert _snapshot(tree).model is None


def test_unknown_in_the_model_enum_is_not_drift(caplog: pytest.LogCaptureFixture) -> None:
    panel = device_from_topics(PANEL, _unknown_model_tree()[PANEL])

    with caplog.at_level(logging.WARNING):
        assert panel_model_drift(panel) == ()
    assert not caplog.records


# --- properties only these models declare -----------------------------------

_UNION = {
    "busbar-current": {"datatype": "float", "unit": "A"},
    "frequency": {"datatype": "float", "unit": "Hz"},
    "nominal-voltage": {"datatype": "float", "unit": "V"},
    "protection-functions": {"datatype": "enum", "format": "OVERCURRENT,SHORT_CIRCUIT,GROUND_FAULT,ARC_FAULT"},
    "shared-with-device-ids": {"datatype": "string"},
}
"""Declarations as the union schema states them."""

_SITE = {
    "name": {"datatype": "string"},
    "address-lines": {"datatype": "string"},
    "locality": {"datatype": "string"},
    "region": {"datatype": "string"},
    "country-code": {"datatype": "string"},
    "latitude": {"datatype": "float"},
    "longitude": {"datatype": "float"},
    "utility-meter-serial-number": {"datatype": "string"},
}
_BACKED_UP = {"datatype": "enum", "format": "BACKED_UP,NOT_BACKED_UP,UNKNOWN"}
_CIRCUIT_ONLY = {
    ("info", "tags"): {"datatype": "string"},
    ("info", "locations"): {"datatype": "string"},
    ("info", "dedicated"): {"datatype": "boolean"},
    ("connection", "fed-by-device-id"): {"datatype": "string"},
    ("connection", "fed-by-device-type"): {"datatype": "string"},
    ("connection", "fed-by-device-status"): {"datatype": "string"},
    ("connection", "backed-up"): _BACKED_UP,
}
_LUGS_ONLY = {
    "backed-up": _BACKED_UP,
    "feeds-role": _FEEDS_ROLE,
    "service-rating": {"datatype": "integer", "unit": "A"},
    "overcurrent-protection": {"datatype": "integer", "unit": "A"},
}
_UPSTREAM_LUGS = "lugs-upstream"


def _declare(tree: RetainedTopicTree, device_id: str, node: str, prop: str, definition: object, value: str) -> None:
    description = json.loads(tree[device_id]["$description"])
    description["nodes"].setdefault(node, {"properties": {}})["properties"][prop] = definition
    tree[device_id]["$description"] = json.dumps(description)
    tree[device_id][f"{node}/{prop}"] = value


def _with_model_properties() -> dict[str, dict[str, str]]:
    tree = _tree()
    _declare(tree, PANEL, "meter", "busbar-current", _UNION["busbar-current"], "41.5")
    _declare(tree, PANEL, "meter", "frequency", _UNION["frequency"], "60.01")
    _declare(tree, KITCHEN, "info", "nominal-voltage", _UNION["nominal-voltage"], "120.0")
    _declare(tree, KITCHEN, "breaker", "protection-functions", _UNION["protection-functions"], "GROUND_FAULT,ARC_FAULT")
    shared = _UNION["shared-with-device-ids"]
    _declare(tree, KITCHEN, "switch", "shared-with-device-ids", shared, "circuit-4,circuit-2")
    _declare(tree, "circuit-2", "switch", "shared-with-device-ids", shared, "circuit-1,circuit-4")
    _declare(tree, KITCHEN, "meter", "shared-with-device-ids", shared, f"{REMOTE_CT}, not-yet-seen,{KITCHEN},circuit-2")
    return tree


def test_the_panel_meter_reads_busbar_current_and_frequency() -> None:
    snapshot = _snapshot(_with_model_properties())

    assert snapshot.busbar_current_a == 41.5
    assert snapshot.frequency_hz == 60.01


def test_a_circuit_reads_its_nominal_voltage_and_protection_functions() -> None:
    kitchen = _snapshot(_with_model_properties()).circuits[KITCHEN]

    assert kitchen.nominal_voltage_v == 120.0
    assert kitchen.protection_functions == ("GROUND_FAULT", "ARC_FAULT")


def test_shared_with_resolves_to_other_circuits_in_device_id_order() -> None:
    """The circuit itself and an id naming no circuit in the snapshot are left out."""
    circuits = _snapshot(_with_model_properties()).circuits

    assert circuits[KITCHEN].relay_shared_with == ("circuit-2", "circuit-4")
    assert circuits["circuit-2"].relay_shared_with == ("circuit-1", "circuit-4")
    assert circuits[KITCHEN].meter_shared_with == ("circuit-2", REMOTE_CT)


def test_shared_with_orders_instance_numbers_numerically() -> None:
    tree = _with_model_properties()
    tree["circuit-10"] = {**tree["circuit-2"]}
    tree[KITCHEN]["switch/shared-with-device-ids"] = "circuit-10,circuit-2"

    assert _snapshot(tree).circuits[KITCHEN].relay_shared_with == ("circuit-2", "circuit-10")


def test_a_shared_group_naming_no_known_circuit_is_still_shared() -> None:
    tree = _with_model_properties()
    tree[KITCHEN]["meter/shared-with-device-ids"] = "not-yet-seen"

    assert _snapshot(tree).circuits[KITCHEN].meter_shared_with == ()


def test_every_model_property_is_none_where_not_published() -> None:
    snapshot = _snapshot(_with_model_properties())
    unshared = snapshot.circuits["circuit-5"]

    assert unshared.nominal_voltage_v is None
    assert unshared.protection_functions is None
    assert unshared.relay_shared_with is None
    assert unshared.meter_shared_with is None
    assert snapshot.circuits["circuit-4"].relay_shared_with is None


def test_the_model_properties_carry_metadata_from_their_declarations() -> None:
    tree = _with_model_properties()
    metadata = build_field_metadata([device_from_topics(device_id, topics) for device_id, topics in tree.items()])

    assert metadata["panel.busbar_current_a"] == FieldMetadata(unit="A", datatype="float")
    assert metadata["panel.frequency_hz"] == FieldMetadata(unit="Hz", datatype="float")
    assert metadata["circuit.nominal_voltage_v"] == FieldMetadata(unit="V", datatype="float")
    assert metadata["circuit.protection_functions"] == FieldMetadata(unit=None, datatype="enum")
    assert not [path for path in metadata if is_discovery_path(path) and path.split("/")[-1] in _UNION]


def test_the_model_properties_are_not_extension_properties() -> None:
    rows = _snapshot(_with_model_properties()).extension_properties

    assert not [row for row in rows if row.property_id in _UNION]


def test_site_and_topology_properties_never_become_readings() -> None:
    """`info` and `connection` resolve to the device card and the tree, never an entity."""
    tree = _with_model_properties()
    for prop, definition in _SITE.items():
        _declare(tree, PANEL, "info", prop, definition, "1.5" if definition["datatype"] == "float" else "example")
    for (node, prop), definition in _CIRCUIT_ONLY.items():
        _declare(tree, KITCHEN, node, prop, definition, "true" if definition["datatype"] == "boolean" else "UNKNOWN")
    for prop, definition in _LUGS_ONLY.items():
        _declare(
            tree, _UPSTREAM_LUGS, "connection", prop, definition, "200" if definition["datatype"] == "integer" else "LOADS"
        )

    snapshot = _snapshot(tree)
    declared = set(_SITE) | {prop for _node, prop in _CIRCUIT_ONLY} | set(_LUGS_ONLY)

    assert not [row for row in snapshot.extension_properties if row.property_id in declared]
    assert not [row for device in snapshot.adopted_devices for row in device.properties if row.property_id in declared]


def test_the_reference_main_32_publishes_none_of_them() -> None:
    snapshot = _snapshot(parent_child_tree())
    metadata = build_field_metadata(
        [device_from_topics(device_id, topics) for device_id, topics in parent_child_tree().items()]
    )

    assert snapshot.busbar_current_a is None
    assert snapshot.frequency_hz is None
    for circuit in snapshot.circuits.values():
        assert circuit.nominal_voltage_v is None
        assert circuit.protection_functions is None
        assert circuit.relay_shared_with is None
        assert circuit.meter_shared_with is None
    assert not {
        "panel.busbar_current_a",
        "panel.frequency_hz",
        "circuit.nominal_voltage_v",
        "circuit.protection_functions",
    } & set(metadata)


# --- the shipped other-model reference tree ---------------------------------

_OTHER_PANEL = "example-48t-001"


def _shipped() -> SpanPanelSnapshot:
    tree = other_model_tree()
    panel = device_from_topics(_OTHER_PANEL, tree[_OTHER_PANEL])
    return build_snapshot(panel, [device_from_topics(d, topics) for d, topics in tree.items() if d != _OTHER_PANEL])


def _shipped_metadata() -> dict[str, FieldMetadata]:
    return build_field_metadata([device_from_topics(d, topics) for d, topics in other_model_tree().items()])


def test_the_shipped_tree_has_the_other_model_shape() -> None:
    snapshot = _shipped()
    real = {cid for cid in snapshot.circuits if not cid.startswith("unmapped_tab_")}

    assert snapshot.serial_number == _OTHER_PANEL
    assert snapshot.model == "MLO_48"
    assert snapshot.panel_size == 48
    assert snapshot.main_breaker_rating_a is None
    assert real == {f"circuit-{n}" for n in range(1, 7)} | {"remote-ct-1", "remote-ct-2"}
    assert snapshot.circuits["circuit-6"].device_type == "pv"
    assert snapshot.circuits["circuit-3"].device_type == "evse"
    assert snapshot.pv == SpanPVSnapshot()
    assert snapshot.pv_inverters == {}
    assert snapshot.busbar_current_a == 41.5
    assert snapshot.frequency_hz == 60.01
    assert snapshot.circuits["circuit-1"].relay_shared_with == ("circuit-2",)
    assert snapshot.circuits["circuit-5"].relay_requester == "CIRCUIT_SCHEDULER"


def test_the_shipped_tree_remote_cts_are_meter_only() -> None:
    snapshot = _shipped()
    for ct_id in ("remote-ct-1", "remote-ct-2"):
        ct = snapshot.circuits[ct_id]
        assert ct.name == ""
        assert ct.tabs == []
        assert ct.is_user_controllable is False
    assert snapshot.circuits["remote-ct-2"].instant_power_w == 480.0


def test_the_shipped_tree_resolves_every_mapped_row() -> None:
    """Undeclared model-specific rows and the missing panel breaker are silent, not gaps."""
    metadata = _shipped_metadata()

    assert not [path for path, entry in metadata.items() if not entry.resolved]
    assert "panel.main_breaker_rating_a" not in metadata
    assert metadata["panel.busbar_current_a"] == FieldMetadata(unit="A", datatype="float")


def test_the_shipped_tree_reports_its_other_model_only_properties_as_discovered() -> None:
    discovered = {path for path in _shipped_metadata() if is_discovery_path(path)}

    for prop in ("name", "latitude", "longitude", "address-lines", "utility-meter-serial-number"):
        assert f"discovered.distribution-enclosure/info/{prop}" in discovered
    for path in ("info/tags", "info/locations", "info/dedicated", "connection/backed-up"):
        assert f"discovered.circuit/{path}" in discovered
    for prop in ("service-rating", "overcurrent-protection", "feeds-role"):
        assert f"discovered.lugs/connection/{prop}" in discovered


def test_the_shipped_tree_raises_no_reading_for_site_location() -> None:
    snapshot = _shipped()
    site = set(_SITE)

    assert not [row for row in snapshot.extension_properties if row.property_id in site]
    assert snapshot.adopted_devices == ()
