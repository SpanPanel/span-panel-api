"""Phase 5: Simulation Engine Snapshot tests.

Tests cover:
- Snapshot structure: get_snapshot() returns SpanPanelSnapshot with correct types
- Field accuracy: Snapshot fields match values from get_panel_data(), get_status(), get_soe()
- Circuit mapping: All configured circuits appear with correct field values
- Unmapped tab synthesis: Unoccupied tabs synthesized as zero-power circuit entries
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
        assert snapshot.dsm_grid_state == "DSM_ON_GRID"
        assert snapshot.current_run_config == "PANEL_ON_GRID"

    @pytest.mark.asyncio
    async def test_v2_native_fields_populated(self) -> None:
        engine = await _make_engine()
        snapshot = await engine.get_snapshot()
        assert snapshot.dominant_power_source == "GRID"
        assert snapshot.grid_islandable is False
        assert snapshot.l1_voltage == 120.0
        assert snapshot.l2_voltage == 120.0
        assert isinstance(snapshot.main_breaker_rating_a, int)
        assert snapshot.wifi_ssid == "SimulatedNetwork"
        assert snapshot.vendor_cloud == "CONNECTED"
        assert isinstance(snapshot.panel_size, int)

    @pytest.mark.asyncio
    async def test_power_flow_fields_populated(self) -> None:
        engine = await _make_engine()
        snapshot = await engine.get_snapshot()
        assert isinstance(snapshot.power_flow_battery, float)
        assert isinstance(snapshot.power_flow_site, float)
        assert isinstance(snapshot.power_flow_grid, float)
        assert isinstance(snapshot.power_flow_pv, float)

    @pytest.mark.asyncio
    async def test_lugs_current_fields_populated(self) -> None:
        engine = await _make_engine()
        snapshot = await engine.get_snapshot()
        assert isinstance(snapshot.upstream_l1_current_a, float)
        assert isinstance(snapshot.upstream_l2_current_a, float)


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
        # Snapshot includes both configured circuits and unmapped tab entries
        assert set(raw_circuits.keys()).issubset(set(snapshot.circuits.keys()))

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
# Unmapped tab synthesis
# ---------------------------------------------------------------------------


class TestUnmappedTabs:
    """Unoccupied tab positions are synthesized as zero-power circuit entries."""

    @pytest.mark.asyncio
    async def test_unmapped_tabs_present(self) -> None:
        """8-tab config with fewer circuits should have unmapped entries."""
        engine = await _make_engine(CONFIG_8TAB)
        snapshot = await engine.get_snapshot()
        unmapped = {cid: c for cid, c in snapshot.circuits.items() if cid.startswith("unmapped_tab_")}
        # Should have some unmapped tabs (8 total minus configured circuits)
        assert len(unmapped) > 0

    @pytest.mark.asyncio
    async def test_unmapped_tabs_have_zero_power(self) -> None:
        engine = await _make_engine(CONFIG_8TAB)
        snapshot = await engine.get_snapshot()
        for cid, circuit in snapshot.circuits.items():
            if cid.startswith("unmapped_tab_"):
                assert circuit.instant_power_w == 0.0
                assert circuit.produced_energy_wh == 0.0
                assert circuit.consumed_energy_wh == 0.0

    @pytest.mark.asyncio
    async def test_unmapped_tabs_not_controllable(self) -> None:
        engine = await _make_engine(CONFIG_8TAB)
        snapshot = await engine.get_snapshot()
        for cid, circuit in snapshot.circuits.items():
            if cid.startswith("unmapped_tab_"):
                assert circuit.is_user_controllable is False
                assert circuit.is_sheddable is False
                assert circuit.is_never_backup is False

    @pytest.mark.asyncio
    async def test_total_tabs_covered(self) -> None:
        """All tab positions 1..N should be covered by circuits or unmapped entries."""
        engine = await _make_engine(CONFIG_8TAB)
        snapshot = await engine.get_snapshot()
        all_tabs: set[int] = set()
        for circuit in snapshot.circuits.values():
            all_tabs.update(circuit.tabs)
        # Should cover all positions up to at least 8
        assert all_tabs.issuperset(set(range(1, 9)))


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
