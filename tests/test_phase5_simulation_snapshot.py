"""Phase 5: Simulation Engine Snapshot tests.

Tests cover:
- Snapshot structure: get_snapshot() returns SpanPanelSnapshot with correct types
- Field accuracy: Snapshot fields match values from get_panel_data(), get_status(), get_soe()
- Circuit mapping: All configured circuits appear with correct field values
- Branch mapping: All branches appear with correct tab numbers and power values
- Battery SOE: SOE percentage propagated correctly
- Package exports: DynamicSimulationEngine and SimulationConfig in __init__
"""

from __future__ import annotations

from pathlib import Path

import pytest

from span_panel_api import DynamicSimulationEngine
from span_panel_api.simulation import SimulationConfig
from span_panel_api.models import (
    SpanBatterySnapshot,
    SpanBranchSnapshot,
    SpanCircuitSnapshot,
    SpanPanelSnapshot,
)

CONFIG_8TAB = Path(__file__).parent / "fixtures" / "configs" / "simulation_config_8_tab_workshop.yaml"
CONFIG_32 = Path(__file__).parent / "fixtures" / "configs" / "simulation_config_32_circuit.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_engine(config_path: Path = CONFIG_8TAB) -> DynamicSimulationEngine:
    """Create and initialize a simulation engine from a YAML config."""
    engine = DynamicSimulationEngine(
        serial_number="TEST-PANEL-001",
        config_path=config_path,
    )
    await engine.initialize_async()
    return engine


# ---------------------------------------------------------------------------
# Snapshot structure
# ---------------------------------------------------------------------------


class TestSnapshotStructure:
    """get_snapshot() returns a well-formed SpanPanelSnapshot."""

    @pytest.mark.asyncio
    async def test_returns_span_panel_snapshot(self) -> None:
        engine = await _make_engine()
        snapshot = await engine.get_snapshot()
        assert isinstance(snapshot, SpanPanelSnapshot)

    @pytest.mark.asyncio
    async def test_circuits_are_circuit_snapshots(self) -> None:
        engine = await _make_engine()
        snapshot = await engine.get_snapshot()
        assert len(snapshot.circuits) > 0
        for cid, circuit in snapshot.circuits.items():
            assert isinstance(cid, str)
            assert isinstance(circuit, SpanCircuitSnapshot)

    @pytest.mark.asyncio
    async def test_branches_are_branch_snapshots(self) -> None:
        engine = await _make_engine()
        snapshot = await engine.get_snapshot()
        assert len(snapshot.branches) > 0
        for branch in snapshot.branches:
            assert isinstance(branch, SpanBranchSnapshot)

    @pytest.mark.asyncio
    async def test_battery_is_battery_snapshot(self) -> None:
        engine = await _make_engine()
        snapshot = await engine.get_snapshot()
        assert isinstance(snapshot.battery, SpanBatterySnapshot)

    @pytest.mark.asyncio
    async def test_snapshot_is_frozen(self) -> None:
        engine = await _make_engine()
        snapshot = await engine.get_snapshot()
        with pytest.raises(AttributeError):
            snapshot.serial_number = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Field accuracy — compare snapshot to raw engine outputs
# ---------------------------------------------------------------------------


class TestFieldAccuracy:
    """Snapshot fields match the raw dict outputs from the engine."""

    @pytest.mark.asyncio
    async def test_serial_number_matches_status(self) -> None:
        engine = await _make_engine()
        snapshot = await engine.get_snapshot()
        status = await engine.get_status()
        assert snapshot.serial_number == status["system"]["serial"]

    @pytest.mark.asyncio
    async def test_firmware_version_matches_status(self) -> None:
        engine = await _make_engine()
        snapshot = await engine.get_snapshot()
        status = await engine.get_status()
        assert snapshot.firmware_version == status["software"]["firmwareVersion"]

    @pytest.mark.asyncio
    async def test_door_state_matches_status(self) -> None:
        engine = await _make_engine()
        snapshot = await engine.get_snapshot()
        status = await engine.get_status()
        assert snapshot.door_state == status["system"]["doorState"]

    @pytest.mark.asyncio
    async def test_network_flags_match_status(self) -> None:
        engine = await _make_engine()
        snapshot = await engine.get_snapshot()
        status = await engine.get_status()
        assert snapshot.eth0_link == status["network"]["eth0Link"]
        assert snapshot.wlan_link == status["network"]["wlanLink"]
        assert snapshot.wwan_link == status["network"]["wwanLink"]

    @pytest.mark.asyncio
    async def test_soe_matches_get_soe(self) -> None:
        engine = await _make_engine()
        snapshot = await engine.get_snapshot()
        soe_data = await engine.get_soe()
        assert snapshot.battery.soe_percentage == soe_data["soe"]["percentage"]

    @pytest.mark.asyncio
    async def test_panel_power_fields(self) -> None:
        engine = await _make_engine()
        snapshot = await engine.get_snapshot()
        assert isinstance(snapshot.instant_grid_power_w, float)
        assert isinstance(snapshot.feedthrough_power_w, float)
        assert isinstance(snapshot.main_meter_energy_consumed_wh, float)
        assert isinstance(snapshot.main_meter_energy_produced_wh, float)

    @pytest.mark.asyncio
    async def test_dsm_and_relay_fields(self) -> None:
        engine = await _make_engine()
        snapshot = await engine.get_snapshot()
        assert snapshot.main_relay_state == "CLOSED"
        assert snapshot.dsm_grid_state == "DSM_GRID_UP"
        assert snapshot.dsm_state == "DSM_ON_GRID"
        assert snapshot.current_run_config == "PANEL_ON_GRID"


# ---------------------------------------------------------------------------
# Circuit mapping
# ---------------------------------------------------------------------------


class TestCircuitMapping:
    """All configured circuits appear in the snapshot with correct fields."""

    @pytest.mark.asyncio
    async def test_all_circuits_present(self) -> None:
        engine = await _make_engine()
        snapshot = await engine.get_snapshot()
        panel_data = await engine.get_panel_data()
        raw_circuits = panel_data["circuits"]["circuits"]
        assert set(snapshot.circuits.keys()) == set(raw_circuits.keys())

    @pytest.mark.asyncio
    async def test_circuit_field_values(self) -> None:
        engine = await _make_engine()
        snapshot = await engine.get_snapshot()
        panel_data = await engine.get_panel_data()
        raw_circuits = panel_data["circuits"]["circuits"]

        for cid, raw in raw_circuits.items():
            circ = snapshot.circuits[cid]
            assert circ.circuit_id == raw["id"]
            assert circ.name == raw["name"]
            assert circ.relay_state == raw["relayState"]
            assert circ.tabs == raw["tabs"]
            assert circ.priority == raw["priority"]
            assert circ.is_user_controllable == raw["isUserControllable"]
            assert circ.is_sheddable == raw["isSheddable"]
            assert circ.is_never_backup == raw["isNeverBackup"]

    @pytest.mark.asyncio
    async def test_circuit_power_types(self) -> None:
        engine = await _make_engine()
        snapshot = await engine.get_snapshot()
        for circ in snapshot.circuits.values():
            assert isinstance(circ.instant_power_w, float)
            assert isinstance(circ.produced_energy_wh, float)
            assert isinstance(circ.consumed_energy_wh, float)
            assert isinstance(circ.energy_accum_update_time_s, int)
            assert isinstance(circ.instant_power_update_time_s, int)


# ---------------------------------------------------------------------------
# Branch mapping
# ---------------------------------------------------------------------------


class TestBranchMapping:
    """All branches appear with correct tab numbers and power values."""

    @pytest.mark.asyncio
    async def test_branch_count_matches_total_tabs(self) -> None:
        """8-tab config should produce 8 branches."""
        engine = await _make_engine(CONFIG_8TAB)
        snapshot = await engine.get_snapshot()
        assert len(snapshot.branches) == 8

    @pytest.mark.asyncio
    async def test_branch_tab_numbers_sequential(self) -> None:
        engine = await _make_engine(CONFIG_8TAB)
        snapshot = await engine.get_snapshot()
        tab_numbers = [b.tab_number for b in snapshot.branches]
        assert tab_numbers == list(range(1, 9))

    @pytest.mark.asyncio
    async def test_branch_field_types(self) -> None:
        engine = await _make_engine()
        snapshot = await engine.get_snapshot()
        for branch in snapshot.branches:
            assert isinstance(branch.tab_number, int)
            assert isinstance(branch.relay_state, str)
            assert isinstance(branch.instant_power_w, float)
            assert isinstance(branch.imported_energy_wh, float)
            assert isinstance(branch.exported_energy_wh, float)

    @pytest.mark.asyncio
    async def test_32_circuit_branch_count(self) -> None:
        """32-circuit config should produce 32 branches."""
        engine = await _make_engine(CONFIG_32)
        snapshot = await engine.get_snapshot()
        assert len(snapshot.branches) == 32


# ---------------------------------------------------------------------------
# Battery SOE
# ---------------------------------------------------------------------------


class TestBatterySoe:
    """SOE percentage propagated correctly."""

    @pytest.mark.asyncio
    async def test_soe_percentage_is_float(self) -> None:
        engine = await _make_engine()
        snapshot = await engine.get_snapshot()
        assert isinstance(snapshot.battery.soe_percentage, float)

    @pytest.mark.asyncio
    async def test_soe_percentage_in_range(self) -> None:
        engine = await _make_engine()
        snapshot = await engine.get_snapshot()
        assert snapshot.battery.soe_percentage is not None
        assert 0.0 <= snapshot.battery.soe_percentage <= 100.0


# ---------------------------------------------------------------------------
# Package exports
# ---------------------------------------------------------------------------


class TestPackageExports:
    """DynamicSimulationEngine and SimulationConfig are importable."""

    def test_dynamic_simulation_engine_importable(self) -> None:
        from span_panel_api import DynamicSimulationEngine

        assert DynamicSimulationEngine is not None

    def test_simulation_config_importable(self) -> None:
        from span_panel_api.simulation import SimulationConfig

        assert SimulationConfig is not None

    def test_exports_in_all(self) -> None:
        import span_panel_api

        assert "DynamicSimulationEngine" in span_panel_api.__all__
