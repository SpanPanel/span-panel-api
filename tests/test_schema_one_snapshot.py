"""End-to-end snapshot assembly from the whole captured v1.0 tree."""

from __future__ import annotations

import pytest

from ebus_sdk.homie import DiscoveredDevice

from span_panel_api.models import SpanPanelSnapshot
from span_panel_api_schema_1.reference_payloads import device_from_topics, parent_child_tree
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
    assert snapshot.pv.model == "IQ8PLUS-72-2-US"
    assert snapshot.pv.feed_circuit_id == SOLAR_CIRCUIT
    # Keyed by serial, not by device id: on real flat firmware the EVSE node id is
    # the Drive's serial (SpanPanel/span#214), so this is what keeps a charger's
    # `unique_id` still across the migration. The reference tree's bare `evse` /
    # `evse-2` device ids are the simulator's naming, not a panel's.
    assert set(snapshot.evse) == {"SIM-EVSE-example-40t-001", "SIM-EVSE-example-40t-001-2"}
    assert snapshot.evse["SIM-EVSE-example-40t-001"].status == "CHARGING"


def test_panel_and_lugs_values_reach_the_snapshot(snapshot: SpanPanelSnapshot) -> None:
    assert snapshot.instant_grid_power_w == -5847.0
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
    assert len(snapshot.circuits) == 5


def test_a_panel_with_no_children_still_builds() -> None:
    """A panel mid-discovery has announced itself but no descendants yet."""
    snapshot = build_snapshot(_device(PANEL), [])

    assert snapshot.serial_number == "example-40t-001"
    assert snapshot.instant_grid_power_w == 0.0
    assert snapshot.battery.soe_percentage is None
    # Every position is unoccupied, so all 40 are synthesised.
    assert len(snapshot.circuits) == 40
