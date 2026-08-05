"""End-to-end snapshot assembly from the whole captured v1.0 tree."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ebus_sdk.homie import DiscoveredDevice

from span_panel_api.models import SpanPanelSnapshot
from span_panel_api_schema_1.snapshot import TreeRoles, build_snapshot

_TREE = json.loads((Path(__file__).parent / "fixtures" / "parent_child_tree.json").read_text(encoding="utf-8"))

PANEL = "example-40t-001"
SOLAR_CIRCUIT = "573066aaddd7b75114c4563ce3af18c4"


def _device(device_id: str) -> DiscoveredDevice:
    topics = _TREE[device_id]
    device = DiscoveredDevice(device_id, "ebus")
    device.update_description(topics["$description"])
    device.update_state(topics["$state"])
    for topic, value in topics.items():
        if topic.startswith("$"):
            continue
        node, _, prop = topic.partition("/")
        if prop:
            device.update_property(node, prop, value)
    return device


def _children() -> list[DiscoveredDevice]:
    return [_device(device_id) for device_id in _TREE if device_id != PANEL]


@pytest.fixture(name="snapshot")
def _snapshot() -> SpanPanelSnapshot:
    return build_snapshot(_device(PANEL), _children())


def test_roles_are_sorted_by_declared_type_not_device_id() -> None:
    """The reference tree's ids are the simulator's naming; the type string is
    what the schema defines."""
    roles = TreeRoles(_children())

    assert len(roles.circuits) == 5
    assert len(roles.lugs) == 2
    assert len(roles.evse) == 2
    assert roles.bess is not None and roles.bess.device_id == "bess"
    assert roles.pv is not None and roles.pv.device_id == "pv"
    assert roles.mid is not None and roles.mid.device_id == "bess-mid"


def test_snapshot_carries_panel_identity(snapshot: SpanPanelSnapshot) -> None:
    assert snapshot.serial_number == "example-40t-001"
    assert snapshot.panel_size == 40
    assert snapshot.main_breaker_rating_a == 200


def test_every_real_circuit_is_present(snapshot: SpanPanelSnapshot) -> None:
    real = {cid for cid in snapshot.circuits if not cid.startswith("unmapped_tab_")}

    assert len(real) == 5
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
    assert snapshot.pv.product_name == "IQ8PLUS-72-2-US"
    assert snapshot.pv.feed_circuit_id == SOLAR_CIRCUIT
    assert set(snapshot.evse) == {"evse", "evse-2"}
    assert snapshot.evse["evse"].status == "CHARGING"


def test_panel_and_lugs_values_reach_the_snapshot(snapshot: SpanPanelSnapshot) -> None:
    assert snapshot.instant_grid_power_w == -5847.0
    assert snapshot.power_flow_pv == 8500.0
    assert snapshot.grid_state == "UP"
    assert snapshot.l1_voltage == 120.0


def test_derived_v1_fields_are_unknown_rather_than_reconstructed(snapshot: SpanPanelSnapshot) -> None:
    """The flat adapter derives these from several v2 signals, two of which
    (`dominant-power-source`, `grid-islandable`) no longer exist. Reproducing
    the heuristic against missing inputs would produce a confident wrong
    answer."""
    assert snapshot.dsm_state == "UNKNOWN"
    assert snapshot.current_run_config == "UNKNOWN"


def test_an_unsizable_panel_yields_no_unmapped_positions() -> None:
    """A panel whose model we cannot size must not fabricate positions."""
    panel = _device(PANEL)
    panel.update_property("info", "model", "MAIN_99")

    snapshot = build_snapshot(panel, _children())

    assert snapshot.panel_size == 0
    assert not [cid for cid in snapshot.circuits if cid.startswith("unmapped_tab_")]
    # Real circuits survive — only the synthesised ones depend on the total.
    assert len(snapshot.circuits) == 5


def test_a_panel_with_no_children_still_builds() -> None:
    """A panel mid-discovery has announced itself but no descendants yet."""
    snapshot = build_snapshot(_device(PANEL), [])

    assert snapshot.serial_number == "example-40t-001"
    assert snapshot.instant_grid_power_w == 0.0
    assert snapshot.battery.soe_percentage is None
    # Every position is unoccupied, so all 40 are synthesised.
    assert len(snapshot.circuits) == 40
