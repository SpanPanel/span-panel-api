"""End-to-end snapshot assembly from the whole captured v1.0 tree."""

from __future__ import annotations

import json

import pytest

from ebus_sdk.homie import DiscoveredDevice

from reference_payloads.schema_one import device_from_topics, parent_child_tree
from span_panel_api.models import ExtensionSubject, SpanPanelSnapshot
from span_panel_api_schema_1.snapshot import TreeRoles, build_snapshot

_TREE = parent_child_tree()

PANEL = "example-40t-001"
SOLAR_CIRCUIT = "573066aaddd7b75114c4563ce3af18c4"


def _device(device_id: str) -> DiscoveredDevice:
    return device_from_topics(device_id, _TREE[device_id])


def _children() -> list[DiscoveredDevice]:
    return [_device(device_id) for device_id in _TREE if device_id != PANEL]


@pytest.fixture(name="snapshot")
def _snapshot() -> SpanPanelSnapshot:
    return build_snapshot(_device(PANEL), _children())


def test_roles_are_sorted_by_declared_type_not_device_id() -> None:
    """The reference tree's ids are the simulator's naming; the type string is
    what the schema defines."""
    roles = TreeRoles(_children())

    assert len(roles.circuits) == 6
    assert len(roles.lugs) == 2
    assert len(roles.evse) == 2
    assert roles.bess is not None and roles.bess.device_id == "bess"
    assert [device.device_id for device in roles.pvs] == ["pv"]
    assert roles.mid is not None and roles.mid.device_id == "bess-mid"


def test_snapshot_carries_panel_identity(snapshot: SpanPanelSnapshot) -> None:
    assert snapshot.serial_number == "example-40t-001"
    assert snapshot.panel_size == 40
    assert snapshot.main_breaker_rating_a == 200


def test_every_real_circuit_is_present(snapshot: SpanPanelSnapshot) -> None:
    real = {cid for cid in snapshot.circuits if not cid.startswith("unmapped_tab_")}

    assert len(real) == 6
    assert SOLAR_CIRCUIT in real
    assert snapshot.circuits[SOLAR_CIRCUIT].name == "Solar Inverter"


def test_unoccupied_positions_are_filled_up_to_the_panel_size(snapshot: SpanPanelSnapshot) -> None:
    """The feature the model lookup exists for: the tree lists occupied
    positions and says nothing about the rest."""
    occupied = {tab for cid, c in snapshot.circuits.items() if not cid.startswith("unmapped_tab_") for tab in c.tabs}
    unmapped = {cid for cid in snapshot.circuits if cid.startswith("unmapped_tab_")}

    assert len(occupied) + len(unmapped) == 40
    assert "unmapped_tab_40" in unmapped
    # Occupied positions are never synthesised.
    for tab in occupied:
        assert f"unmapped_tab_{tab}" not in unmapped


def test_a_circuit_feeding_a_der_reports_the_der_type(snapshot: SpanPanelSnapshot) -> None:
    """Matches the flat adapter, where a PV-feeding circuit reports device_type
    'pv' rather than 'circuit'."""
    assert snapshot.circuits[SOLAR_CIRCUIT].device_type == "pv"


def test_der_snapshots_are_populated(snapshot: SpanPanelSnapshot) -> None:
    assert snapshot.battery.soe_percentage == pytest.approx(50.4104, rel=1e-4)
    assert snapshot.battery.connected is True
    assert snapshot.pv.model == "IQ8PLUS-72-2-US"
    assert snapshot.pv.feed_circuit_id == SOLAR_CIRCUIT
    # Keyed by serial, not by device id: on real flat firmware the EVSE node id is
    # the Drive's serial (SpanPanel/span#214), so this is what keeps a charger's
    # `unique_id` still across the migration. The reference tree's bare `evse` /
    # `evse-2` device ids are the simulator's naming, not a panel's.
    assert set(snapshot.evse) == {"SIM-EVSE-example-40t-001", "SIM-EVSE-example-40t-001-2"}
    assert snapshot.evse["SIM-EVSE-example-40t-001"].status == "CHARGING"


def test_panel_and_lugs_values_reach_the_snapshot(snapshot: SpanPanelSnapshot) -> None:
    assert snapshot.instant_grid_power_w == -4617.0
    assert snapshot.power_flow_pv == -8500.0
    assert snapshot.grid_state == "ON_GRID"
    assert snapshot.l1_voltage == 120.0


def test_the_grid_answers_are_read_from_the_mid_not_derived(snapshot: SpanPanelSnapshot) -> None:
    """Both entities keep the values a user has today, by reading instead of guessing.

    Flat inferred these from `dominant-power-source` plus grid power because nothing
    stated them. v1.0 states them on the MID, so the multi-signal heuristic is gone and
    the answer is authoritative -- while the user-visible vocabulary is unchanged, which
    is the whole point: `dsm_state` and `current_run_config` are existing entities whose
    history must survive the migration.

    This asserted `UNKNOWN` for both until 2026-08-10, on the reasoning that two of the
    heuristic's three inputs no longer exist. True of the *inputs*, wrong as a conclusion:
    v1.0 removed the need to infer rather than the ability to answer.

    `PANEL_BACKUP` versus `PANEL_OFF_GRID` gets strictly better than flat here — flat
    guessed it from the dominant power source, v1.0 names the forming device and its
    class is recoverable from the tree.
    """
    assert snapshot.dsm_state == "DSM_ON_GRID"
    assert snapshot.current_run_config == "PANEL_ON_GRID"


def test_an_unsizable_panel_yields_no_unmapped_positions() -> None:
    """A panel whose model we cannot size must not fabricate positions."""
    panel = _device(PANEL)
    panel.update_property("info", "model", "MAIN_99")

    snapshot = build_snapshot(panel, _children())

    assert snapshot.panel_size == 0
    assert not [cid for cid in snapshot.circuits if cid.startswith("unmapped_tab_")]
    # Real circuits survive — only the synthesised ones depend on the total.
    assert len(snapshot.circuits) == 6


def test_a_panel_with_no_children_still_builds() -> None:
    """A panel mid-discovery has announced itself but no descendants yet."""
    snapshot = build_snapshot(_device(PANEL), [])

    assert snapshot.serial_number == "example-40t-001"
    # Nothing has reported a reading yet, which is not the same as a reading of
    # zero — see `test_absent_readings_are_not_zero`.
    assert snapshot.instant_grid_power_w is None
    assert snapshot.battery.soe_percentage is None
    # Every position is unoccupied, so all 40 are synthesised.
    assert len(snapshot.circuits) == 40


# ---------------------------------------------------------------------------
# More than one PV inverter
# ---------------------------------------------------------------------------

# Firmware from r202639 publishes every commissioned inverter as its own device and,
# once there is more than one, changes every PV device id. The ids here are illustrative.
FIRST_PV = "example-40t-001-IQ8PLUS-72-2-US-1"
SECOND_PV = "example-40t-001-SN1234-2"
UNFED_PV = "example-40t-001-IQ8PLUS-72-2-US-3"
SECOND_SOLAR_CIRCUIT = "5be1d2c3a4f5061728394a5b6c7d8e9f"


def _multi_inverter_children() -> list[DiscoveredDevice]:
    """The capture with three inverters: two fed by a circuit each, one fed by none.

    The second inverter's circuit sits on lower breaker spaces than the captured
    solar circuit, which once made the library rank it first; nothing ranks
    inverters now. Only the second inverter publishes a serial; the others leave it unpublished,
    which is the common case.
    """
    tree = {device_id: dict(topics) for device_id, topics in _TREE.items()}
    pv_topics = tree.pop("pv")
    tree[FIRST_PV] = dict(pv_topics)
    tree[SECOND_PV] = {**pv_topics, "info/serial-number": "SN1234", "info/nominal-power": "4000.0"}
    tree[UNFED_PV] = dict(pv_topics)
    tree[SOLAR_CIRCUIT]["connection/feeds-device-id"] = FIRST_PV
    tree[SECOND_SOLAR_CIRCUIT] = {
        **tree[SOLAR_CIRCUIT],
        "connection/feeds-device-id": SECOND_PV,
        "info/name": "Garage Solar",
        "info/spaces": "5,7",
    }
    return [device_from_topics(device_id, topics) for device_id, topics in tree.items() if device_id != PANEL]


def test_every_inverter_is_a_snapshot_keyed_by_its_feeding_circuit_or_its_device_id() -> None:
    snapshot = build_snapshot(_device(PANEL), _multi_inverter_children())

    assert set(snapshot.pv_inverters) == {SOLAR_CIRCUIT, SECOND_SOLAR_CIRCUIT, UNFED_PV}
    assert snapshot.pv_inverters[SOLAR_CIRCUIT].device_id == FIRST_PV
    assert snapshot.pv_inverters[SECOND_SOLAR_CIRCUIT].device_id == SECOND_PV
    for key, inverter in snapshot.pv_inverters.items():
        assert inverter.node_id == key

    unfed = snapshot.pv_inverters[UNFED_PV]
    assert unfed.feed_circuit_id is None
    assert unfed.relative_position is None


def test_every_inverters_feeding_circuit_is_labeled_pv() -> None:
    """Not only the one `snapshot.pv` describes: an unlabeled PV circuit reads as a load."""
    snapshot = build_snapshot(_device(PANEL), _multi_inverter_children())

    assert {cid for cid, c in snapshot.circuits.items() if c.device_type == "pv"} == {SOLAR_CIRCUIT, SECOND_SOLAR_CIRCUIT}


# A second circuit feeding the first inverter, as a split-feed installation
# publishes it. Its id sorts above the captured solar circuit's, so the
# inverter's key stays the captured circuit and only the label is at issue.
SPLIT_FEED_CIRCUIT = "9f0e1d2c3b4a59687766554433221100"
CHARGER_CIRCUITS = ("62d0e03897b337b57101aae82f1e9ba2", "fe8b85c15bc9610c1b8b4ebc6f82488d")


def _split_feed_children() -> list[DiscoveredDevice]:
    """The three-inverter capture with the first inverter fed by two circuits."""
    split = device_from_topics(
        SPLIT_FEED_CIRCUIT,
        {
            **_TREE[SOLAR_CIRCUIT],
            "connection/feeds-device-id": FIRST_PV,
            "info/name": "Solar Inverter Second Feed",
            "info/spaces": "9,11",
        },
    )
    return [*_multi_inverter_children(), split]


def test_every_circuit_feeding_an_inverter_is_labeled_pv_whatever_the_order() -> None:
    """Both halves of a split feed carry PV power; labelling only one reads the other as a load."""
    children = _split_feed_children()
    labelled = {SOLAR_CIRCUIT: "pv", SECOND_SOLAR_CIRCUIT: "pv", SPLIT_FEED_CIRCUIT: "pv"}
    labelled.update(dict.fromkeys(CHARGER_CIRCUITS, "evse"))

    for order in (children, list(reversed(children))):
        snapshot = build_snapshot(_device(PANEL), order)

        assert {cid: c.device_type for cid, c in snapshot.circuits.items() if c.device_type != "circuit"} == labelled
        assert snapshot.pv_inverters[SOLAR_CIRCUIT].device_id == FIRST_PV


def test_with_several_inverters_pv_describes_them_together() -> None:
    """No inverter is primary: `pv` identifies none of them, whatever the tree order."""
    children = _multi_inverter_children()
    forward = build_snapshot(_device(PANEL), children)
    backward = build_snapshot(_device(PANEL), list(reversed(children)))

    assert forward.pv == backward.pv
    assert forward.pv.device_id is None
    assert forward.pv.node_id is None
    assert forward.pv.feed_circuit_id is None
    assert forward.pv.serial_number is None
    assert forward.pv.software_version is None
    assert forward.pv.vendor_name == forward.pv_inverters[SOLAR_CIRCUIT].vendor_name
    assert forward.pv.model == forward.pv_inverters[SOLAR_CIRCUIT].model
    assert forward.pv.nameplate_capacity_w == sum(
        inverter.nameplate_capacity_w or 0.0 for inverter in forward.pv_inverters.values()
    )
    # Two links reported up and one unreported: not known to be all up, not known down.
    assert forward.pv.connected is None


def test_one_link_down_is_the_link_down() -> None:
    """Three-valued: a link known down decides, whatever an unreported one would say."""
    lost = device_from_topics(
        SECOND_SOLAR_CIRCUIT,
        {
            **_TREE[SOLAR_CIRCUIT],
            "connection/feeds-device-id": SECOND_PV,
            "connection/feeds-device-status": "LOST",
            "info/name": "Garage Solar",
            "info/spaces": "5,7",
        },
    )
    children = [lost if device.device_id == SECOND_SOLAR_CIRCUIT else device for device in _multi_inverter_children()]

    assert build_snapshot(_device(PANEL), children).pv.connected is False


def test_what_several_inverters_do_not_share_is_unknown() -> None:
    children = [
        device_from_topics(SECOND_PV, {**_TREE["pv"], "info/model": "SE7600H"}) if device.device_id == SECOND_PV else device
        for device in _multi_inverter_children()
    ]

    pv = build_snapshot(_device(PANEL), children).pv

    assert pv.model is None
    assert pv.vendor_name == build_snapshot(_device(PANEL), children).pv_inverters[SOLAR_CIRCUIT].vendor_name


def test_a_lone_inverter_is_pv_whatever_feeds_it() -> None:
    lone = [device for device in _multi_inverter_children() if device.device_id not in (FIRST_PV, SECOND_PV)]

    snapshot = build_snapshot(_device(PANEL), lone)

    assert snapshot.pv == snapshot.pv_inverters[UNFED_PV]


def test_the_serial_is_carried_where_published_and_absent_otherwise() -> None:
    snapshot = build_snapshot(_device(PANEL), _multi_inverter_children())

    assert snapshot.pv_inverters[SECOND_SOLAR_CIRCUIT].serial_number == "SN1234"
    assert snapshot.pv_inverters[SOLAR_CIRCUIT].serial_number is None
    # Recorded at installation, and carried as-is: each inverter its own figure.
    assert snapshot.pv_inverters[SECOND_SOLAR_CIRCUIT].nameplate_capacity_w == 4000.0
    assert snapshot.pv_inverters[SOLAR_CIRCUIT].nameplate_capacity_w == 10000.0


def test_extra_inverters_are_modeled_not_adopted() -> None:
    snapshot = build_snapshot(_device(PANEL), _multi_inverter_children())

    assert not [device for device in snapshot.adopted_devices if device.device_type == "energy.ebus.device.pv"]


def _with_vendor_reading(device: DiscoveredDevice, value: str) -> DiscoveredDevice:
    """The same device with one vendor node declared and valued."""
    topics = dict(_TREE["pv"])
    description = json.loads(topics["$description"])
    description["nodes"]["acme"] = {
        "name": "acme",
        "type": "energy.ebus.capability.vendor.acme.string",
        "properties": {"string-voltage": {"name": "String voltage", "datatype": "float", "unit": "V"}},
    }
    topics["$description"] = json.dumps(description)
    topics["acme/string-voltage"] = value
    return device_from_topics(device.device_id, topics)


def test_each_inverters_vendor_readings_are_filed_under_that_inverter() -> None:
    """With more than one inverter, `pv` is keyed per inverter, never one for all."""
    readings = {FIRST_PV: "301.0", SECOND_PV: "302.0", UNFED_PV: "303.0"}
    children = [
        _with_vendor_reading(device, readings[device.device_id]) if device.device_id in readings else device
        for device in _multi_inverter_children()
    ]

    snapshot = build_snapshot(_device(PANEL), children)

    rows = {row.subject: row.value for row in snapshot.extension_properties if row.subject.kind == "pv"}
    assert rows == {
        ExtensionSubject(kind="pv", instance_key=SOLAR_CIRCUIT): "301.0",
        ExtensionSubject(kind="pv", instance_key=SECOND_SOLAR_CIRCUIT): "302.0",
        ExtensionSubject(kind="pv", instance_key=UNFED_PV): "303.0",
    }


def test_a_single_inverters_vendor_readings_keep_the_singleton_subject() -> None:
    children = [_with_vendor_reading(device, "300.0") if device.device_id == "pv" else device for device in _children()]

    snapshot = build_snapshot(_device(PANEL), children)

    assert [row.subject for row in snapshot.extension_properties if row.subject.kind == "pv"] == [
        ExtensionSubject(kind="pv")
    ]


def test_a_single_inverter_reads_as_it_did_before(snapshot: SpanPanelSnapshot) -> None:
    """One inverter keeps its device id at r202639, and `pv` is that inverter."""
    assert set(snapshot.pv_inverters) == {SOLAR_CIRCUIT}
    assert snapshot.pv == snapshot.pv_inverters[SOLAR_CIRCUIT]
    assert snapshot.pv.device_id == "pv"
    assert snapshot.pv.node_id == SOLAR_CIRCUIT
    # Declared and unpublished in the capture, which is the usual state.
    assert snapshot.pv.serial_number is None
